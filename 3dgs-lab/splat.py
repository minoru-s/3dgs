#!/usr/bin/env python3
"""3dgs-lab メインCLI: 入力(動画/画像) -> フレーム抽出 -> COLMAP(SfM) -> Brush(学習) -> ビューア"""

import argparse
import json
import re
import shutil
import subprocess
import statistics
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / ".tools"
BRUSH_BIN = TOOLS_DIR / "brush-app-aarch64-apple-darwin" / "brush_app"
INPUT_DIR = ROOT / "input"
WORK_DIR = ROOT / "work"
OUTPUT_DIR = ROOT / "output"
LOGS_DIR = ROOT / "logs"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

PRESETS = {
    # steps: 学習反復数 / long_edge: 画像長辺リサイズ(0=原寸) / max_splats: MCMCスプラット上限
    "quick":    {"steps": 7000,  "long_edge": 1080, "max_splats": 1_000_000},
    "standard": {"steps": 30000, "long_edge": 1600, "max_splats": 2_000_000},
    "high":     {"steps": 45000, "long_edge": 0,    "max_splats": 3_000_000},
}


class Logger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", buffering=1)

    def log(self, msg: str):
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line, flush=True)
        self._fh.write(line + "\n")


def run(cmd, logger: Logger, **kwargs) -> subprocess.CompletedProcess:
    logger.log("$ " + " ".join(str(c) for c in cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, **kwargs)
    logger.log(f"  -> exit={proc.returncode} ({time.time() - t0:.1f}s)")
    if proc.returncode != 0:
        raise SystemExit(f"コマンドが失敗しました: {' '.join(str(c) for c in cmd)}")
    return proc


def check_tools():
    missing = []
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    if shutil.which("ffprobe") is None:
        missing.append("ffprobe")
    if shutil.which("colmap") is None:
        missing.append("colmap")
    if not BRUSH_BIN.exists():
        missing.append(f"brush ({BRUSH_BIN})")
    if missing:
        sys.exit(
            "以下のツールが見つかりません: " + ", ".join(missing) +
            "\nまず ./setup.sh を実行してください。"
        )


def resolve_scene(args) -> str:
    if args.name:
        return args.name
    if args.input:
        return Path(args.input).stem
    sys.exit("シーン名を特定できません。--name を指定するか入力を指定してください。")


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS


# ---------------------------------------------------------------------------
# (a) extract: 動画からのフレーム抽出 / 画像フォルダのリサイズ・整形
# ---------------------------------------------------------------------------

def laplacian_variance(img) -> float:
    from PIL import ImageFilter
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1))
    return statistics.pvariance(edges.getdata())


def scale_filter_expr(long_edge: int) -> str:
    # 長辺を long_edge に合わせて縮小(拡大はしない)。アスペクト比は維持。
    return (
        f"scale=w='if(gt(iw,ih),min(iw,{long_edge}),-2)':"
        f"h='if(gt(iw,ih),-2,min(ih,{long_edge}))'"
    )


def stage_extract(input_path: Path, images_dir: Path, frames_target: int, long_edge: int, logger: Logger):
    logger.log(f"[extract] input={input_path} frames_target={frames_target} long_edge={long_edge or '原寸'}")
    if images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True)

    if is_video(input_path):
        dur_proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(input_path)],
            capture_output=True, text=True,
        )
        try:
            duration = float(dur_proc.stdout.strip())
        except ValueError:
            duration = 0.0
        if duration <= 0:
            sys.exit(f"動画の長さを取得できませんでした: {input_path}")
        fps = max(frames_target / duration, 0.1)
        logger.log(f"[extract] duration={duration:.1f}s -> fps={fps:.4f}")

        vf = f"fps={fps:.6f}"
        if long_edge > 0:
            vf += "," + scale_filter_expr(long_edge)
        run(
            ["ffmpeg", "-y", "-i", str(input_path), "-vf", vf, "-q:v", "2",
             str(images_dir / "frame_%05d.jpg")],
            logger,
        )

        # ブレ画像の簡易除去: シャープネス(ラプラシアン分散)下位10%を破棄
        from PIL import Image
        files = sorted(images_dir.glob("frame_*.jpg"))
        if len(files) > 10:
            scores = []
            for f in files:
                with Image.open(f) as img:
                    scores.append((laplacian_variance(img), f))
            scores.sort(key=lambda x: x[0])
            n_drop = len(scores) // 10
            for _, f in scores[:n_drop]:
                f.unlink()
            logger.log(f"[extract] ブレ画像 {n_drop} 枚を除去 ({len(files)} -> {len(files) - n_drop})")
    else:
        # 画像フォルダ: リサイズしつつ images/ にコピー
        from PIL import Image, ImageOps
        src_files = sorted(
            p for p in input_path.iterdir() if p.suffix.lower() in IMAGE_EXTS
        )
        if not src_files:
            sys.exit(f"画像ファイルが見つかりません: {input_path}")
        for i, src in enumerate(src_files, start=1):
            with Image.open(src) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                if long_edge > 0:
                    w, h = img.size
                    scale = min(1.0, long_edge / max(w, h))
                    if scale < 1.0:
                        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
                img.save(images_dir / f"frame_{i:05d}.jpg", quality=95)
        logger.log(f"[extract] 画像 {len(src_files)} 枚をコピー・リサイズしました")

    count = len(list(images_dir.glob("*.jpg")))
    if count == 0:
        sys.exit("画像を1枚も取得できませんでした。入力を確認してください。")
    logger.log(f"[extract] 完了: {count} 枚 -> {images_dir}")

    meta_path = images_dir.parent / "meta.json"
    meta_path.write_text(json.dumps({"is_video": is_video(input_path)}))


# ---------------------------------------------------------------------------
# (b) sfm: COLMAP (CPUモード)
# ---------------------------------------------------------------------------

SHOOTING_GUIDE = """
撮影データからカメラ位置を十分に推定できませんでした(登録画像が少なすぎます)。
よくある原因と対策:
  - 対象の周囲を回り込めていない(その場回転のパノラマ撮影は不可)
  - 移動が速すぎる/ブレが多い    -> ゆっくり移動し、隣接フレームの重なりを増やす
  - テクスチャが乏しい/反射面がある -> 模様のある静物・屋外シーンなどで試す
  - 動く被写体・照明変化がある   -> 静止シーン限定
詳しくは README.md の「撮影ガイド」を参照してください。
このまま強行する場合は --force を指定してください。
"""


def stage_sfm(is_video_scene: bool, work_dir: Path, images_dir: Path, logger: Logger, force: bool):
    db_path = work_dir / "colmap.db"
    sparse_dir = work_dir / "sparse"
    if db_path.exists():
        db_path.unlink()
    if sparse_dir.exists():
        shutil.rmtree(sparse_dir)
    sparse_dir.mkdir(parents=True)

    logger.log("[sfm] feature_extractor 実行中(CPUモード)...")
    run(
        ["colmap", "feature_extractor",
         "--database_path", str(db_path),
         "--image_path", str(images_dir),
         "--ImageReader.single_camera", "1",
         "--ImageReader.camera_model", "OPENCV",
         "--FeatureExtraction.use_gpu", "0"],
        logger,
    )

    matcher = "sequential_matcher" if is_video_scene else "exhaustive_matcher"
    logger.log(f"[sfm] {matcher} 実行中(CPUモード)...")
    run(
        ["colmap", matcher,
         "--database_path", str(db_path),
         "--FeatureMatching.use_gpu", "0"],
        logger,
    )

    logger.log("[sfm] mapper 実行中(疎再構成)...")
    run(
        ["colmap", "mapper",
         "--database_path", str(db_path),
         "--image_path", str(images_dir),
         "--output_path", str(sparse_dir)],
        logger,
    )

    model_dir = sparse_dir / "0"
    if not model_dir.exists():
        sys.exit("COLMAP の疎再構成に失敗しました(sparse/0 が生成されませんでした)。" + SHOOTING_GUIDE)

    analyzer = subprocess.run(
        ["colmap", "model_analyzer", "--path", str(model_dir)],
        capture_output=True, text=True,
    )
    combined = analyzer.stdout + analyzer.stderr
    m = re.search(r"Registered images:\s*(\d+)", combined)
    registered = int(m.group(1)) if m else 0
    total = len(list(images_dir.glob("*.jpg")))
    ratio = registered / total if total else 0.0
    logger.log(f"[sfm] 登録画像数: {registered}/{total} ({ratio:.0%})")

    if ratio < 0.5 and not force:
        sys.exit(SHOOTING_GUIDE)
    elif ratio < 0.8:
        logger.log("[sfm] 警告: 登録率が80%未満です。品質が低い可能性があります。")

    logger.log(f"[sfm] 完了 -> {model_dir}")


# ---------------------------------------------------------------------------
# (c) train: Brush
# ---------------------------------------------------------------------------

def stage_train(work_dir: Path, output_dir: Path, scene: str, steps: int, max_splats: int,
                 long_edge: int, with_viewer: bool, logger: Logger):
    model_dir = work_dir / "sparse" / "0"
    if not model_dir.exists():
        sys.exit(f"COLMAP の疎再構成が見つかりません: {model_dir} (先に --only sfm を実行してください)")

    output_dir.mkdir(parents=True, exist_ok=True)
    export_name = f"{scene}.ply"
    export_every = min(5000, steps)

    cmd = [
        str(BRUSH_BIN), str(work_dir),
        "--total-steps", str(steps),
        "--max-splats", str(max_splats),
        "--export-path", str(output_dir),
        "--export-name", export_name,
        "--export-every", str(export_every),
    ]
    if long_edge > 0:
        cmd += ["--max-resolution", str(long_edge)]
    if with_viewer:
        cmd.append("--with-viewer")

    logger.log(f"[train] steps={steps} max_splats={max_splats} export={output_dir / export_name}")
    run(cmd, logger)

    ply_path = output_dir / export_name
    if not ply_path.exists():
        sys.exit(f"学習は完了しましたが出力ファイルが見つかりません: {ply_path}")
    logger.log(f"[train] 完了 -> {ply_path}")


# ---------------------------------------------------------------------------
# (d) view
# ---------------------------------------------------------------------------

def stage_view(output_dir: Path, scene: str, logger: Logger, port: int = 8000):
    ply_path = output_dir / f"{scene}.ply"
    if not ply_path.exists():
        sys.exit(f"表示する .ply が見つかりません: {ply_path} (先に学習を実行してください)")

    rel_ply = ply_path.relative_to(ROOT)
    # viewer/index.html から見て相対パスではなくルート相対にするため先頭に '/' を付与
    url = f"http://localhost:{port}/viewer/index.html?file=/{rel_ply.as_posix()}"
    logger.log(f"[view] {url} (Chrome推奨。Ctrl+Cで終了)")

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--directory", str(ROOT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.8)
    try:
        webbrowser.open(url)
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="3D Gaussian Splatting ローカル再構成パイプライン")
    p.add_argument("input", nargs="?", help=".mp4/.mov 動画、または画像フォルダ")
    p.add_argument("--name", help="シーン名(省略時は入力ファイル/フォルダ名)")
    p.add_argument("--preset", choices=list(PRESETS), default="standard")
    p.add_argument("--frames", type=int, default=200, help="動画から抽出する目標フレーム数")
    p.add_argument("--long-edge", type=int, default=None, help="画像の長辺リサイズ(px)。0で原寸")
    p.add_argument("--only", choices=["extract", "sfm", "train", "view"], help="ステージ単体実行")
    p.add_argument("--view", action="store_true", help="学習後に自動でビューアを起動")
    p.add_argument("--force", action="store_true", help="SfM登録率が低くても続行する")
    p.add_argument("--steps", type=int, default=None, help="Brush学習ステップ数(プリセットを上書き)")
    p.add_argument("--max-splats", type=int, default=None, help="スプラット数上限(プリセットを上書き)")
    p.add_argument("--with-viewer", action="store_true", help="学習中にBrushのGUIを開く(デバッグ用)")
    p.add_argument("--port", type=int, default=8000, help="ビューア用HTTPサーバのポート")
    return p


def main():
    args = build_parser().parse_args()
    check_tools()

    preset = PRESETS[args.preset]
    long_edge = args.long_edge if args.long_edge is not None else preset["long_edge"]
    steps = args.steps or preset["steps"]
    max_splats = args.max_splats or preset["max_splats"]

    scene = resolve_scene(args)
    work_dir = WORK_DIR / scene
    images_dir = work_dir / "images"
    output_dir = OUTPUT_DIR / scene
    logger = Logger(LOGS_DIR / f"{scene}.log")

    logger.log(
        f"=== scene={scene} preset={args.preset} steps={steps} "
        f"long_edge={long_edge or '原寸'} max_splats={max_splats} only={args.only or 'all'} ==="
    )

    input_path = Path(args.input).expanduser().resolve() if args.input else None
    if input_path and not input_path.exists():
        sys.exit(f"入力が見つかりません: {input_path}")

    run_extract = args.only in (None, "extract")
    run_sfm = args.only in (None, "sfm")
    run_train = args.only in (None, "train")
    run_view = args.only == "view" or (args.only is None and args.view)

    if run_extract:
        if input_path is None:
            sys.exit("extract ステージには入力(動画/画像フォルダ)が必要です。")
        stage_extract(input_path, images_dir, args.frames, long_edge, logger)

    if run_sfm:
        if input_path is None and not images_dir.exists():
            sys.exit(f"画像フォルダが見つかりません: {images_dir} (先に --only extract を実行してください)")
        meta_path = work_dir / "meta.json"
        if input_path is not None:
            is_video_scene = is_video(input_path)
        elif meta_path.exists():
            is_video_scene = json.loads(meta_path.read_text())["is_video"]
        else:
            sys.exit(f"{meta_path} が見つかりません。--only extract を先に実行するか入力を指定してください。")
        stage_sfm(is_video_scene, work_dir, images_dir, logger, args.force)

    if run_train:
        stage_train(work_dir, output_dir, scene, steps, max_splats, long_edge, args.with_viewer, logger)

    if run_view:
        stage_view(output_dir, scene, logger, args.port)

    logger.log("=== 完了 ===")
    if not run_view:
        print(f"\nビューアで確認するには: python3 splat.py --name {scene} --only view")


if __name__ == "__main__":
    main()
