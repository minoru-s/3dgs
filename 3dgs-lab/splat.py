#!/usr/bin/env python3
"""3dgs-lab メインCLI: 入力(動画/画像) -> フレーム抽出 -> COLMAP(SfM) -> Brush(学習) -> ビューア"""

import argparse
import json
import math
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
    # frames: 動画から最終的に残すキーフレーム数
    # steps: 学習反復数 / long_edge: 画像長辺 / max_splats: MCMCスプラット上限
    "quick":    {"frames": 80,  "steps": 5000,  "long_edge": 1080, "max_splats": 350_000},
    "standard": {"frames": 120, "steps": 10000, "long_edge": 1280, "max_splats": 600_000},
    "high":     {"frames": 180, "steps": 20000, "long_edge": 1600, "max_splats": 1_200_000},
}


class Logger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", buffering=1)

    def log(self, msg: str):
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line, flush=True)
        self._fh.write(line + "\n")


def run(cmd, logger: Logger, fail_on_error: bool = True, **kwargs) -> subprocess.CompletedProcess:
    logger.log("$ " + " ".join(str(c) for c in cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, **kwargs)
    logger.log(f"  -> exit={proc.returncode} ({time.time() - t0:.1f}s)")
    if proc.returncode != 0 and fail_on_error:
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


def video_color_transfer(path: Path) -> str:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=color_transfer",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return proc.stdout.strip().lower()


def _thumbnail_metrics(path: Path):
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        gray = ImageOps.pad(img.convert("L"), (320, 320), color=0, method=Image.Resampling.BILINEAR)
        sharpness = laplacian_variance(gray)
    return sharpness, gray


def _normalized(value: float, low: float, high: float) -> float:
    if high <= low:
        return 1.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def select_keyframes(candidate_dir: Path, images_dir: Path, target: int, logger: Logger):
    """時間方向を均等に保ちつつ、各区間からブレが少なく変化量のある1枚を選ぶ。"""
    from PIL import ImageChops, ImageStat

    candidates = sorted(candidate_dir.glob("frame_*.jpg"))
    if not candidates:
        sys.exit("キーフレーム候補を1枚も抽出できませんでした。")

    metrics = {path: _thumbnail_metrics(path) for path in candidates}
    sharp_values = sorted(value[0] for value in metrics.values())
    low = sharp_values[int(0.10 * (len(sharp_values) - 1))]
    high = sharp_values[int(0.90 * (len(sharp_values) - 1))]
    keep = min(target, len(candidates))
    selected = []
    previous_thumb = None

    for bucket_index in range(keep):
        start = round(bucket_index * len(candidates) / keep)
        end = round((bucket_index + 1) * len(candidates) / keep)
        bucket = candidates[start:max(start + 1, end)]
        best = None
        best_score = -1.0
        for path in bucket:
            sharpness, thumb = metrics[path]
            sharp_score = _normalized(sharpness, low, high)
            if previous_thumb is None:
                motion_score = 0.5
            else:
                # RMS差分は厳密なオプティカルフローではないが、同じ短い時間区間内では
                # 「ほぼ重複」と「適度に視点が動いたフレーム」を安価に区別できる。
                rms = ImageStat.Stat(ImageChops.difference(previous_thumb, thumb)).rms[0]
                motion_score = min(1.0, rms / 32.0)
            score = 0.70 * sharp_score + 0.30 * motion_score
            if score > best_score:
                best, best_score = path, score
        selected.append(best)
        previous_thumb = metrics[best][1]

    images_dir.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(selected, start=1):
        shutil.copy2(source, images_dir / f"frame_{index:05d}.jpg")

    selected_sharpness = [metrics[path][0] for path in selected]
    logger.log(
        f"[extract] キーフレーム選別: {len(candidates)}候補 -> {len(selected)}枚 "
        f"(時間均等 + シャープネス70% + 画面変化30%、sharpness中央値={statistics.median(selected_sharpness):.1f})"
    )


def stage_extract(input_path: Path, images_dir: Path, frames_target: int, long_edge: int, logger: Logger):
    logger.log(f"[extract] input={input_path} frames_target={frames_target} long_edge={long_edge or '原寸'}")
    if images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True)

    if is_video(input_path):
        candidate_dir = images_dir.parent / "keyframe_candidates"
        if candidate_dir.exists():
            shutil.rmtree(candidate_dir)
        candidate_dir.mkdir(parents=True)

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
        candidate_target = max(frames_target + 20, frames_target * 2)
        fps = max(candidate_target / duration, 0.1)
        logger.log(f"[extract] duration={duration:.1f}s -> 候補fps={fps:.4f} ({candidate_target}枚目標)")

        vf = f"fps={fps:.6f}"
        transfer = video_color_transfer(input_path)
        filters = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True,
        ).stdout
        if transfer in {"arib-std-b67", "smpte2084"} and "zscale" in filters:
            vf += (
                ",zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
            )
            logger.log(f"[extract] HDR({transfer})をSDRへトーンマッピングします")
        elif transfer in {"arib-std-b67", "smpte2084"}:
            logger.log(f"[extract] 警告: HDR({transfer})ですがffmpegにzscaleが無いため、そのまま変換します")
        if long_edge > 0:
            vf += "," + scale_filter_expr(long_edge)
        run(
            ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", str(input_path),
             "-map_metadata", "-1", "-vf", vf, "-q:v", "2",
             str(candidate_dir / "frame_%05d.jpg")],
            logger,
        )
        select_keyframes(candidate_dir, images_dir, frames_target, logger)
        shutil.rmtree(candidate_dir)
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
# (b) sfm: COLMAP (MacではCPUモード)
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


def model_registered_images(model_dir: Path) -> int:
    analyzer = subprocess.run(
        ["colmap", "model_analyzer", "--path", str(model_dir)],
        capture_output=True, text=True,
    )
    combined = analyzer.stdout + analyzer.stderr
    match = re.search(r"Registered images:\s*(\d+)", combined)
    return int(match.group(1)) if match else 0


def best_sparse_model(root: Path):
    models = [path for path in root.iterdir() if path.is_dir()] if root.exists() else []
    scored = [(model_registered_images(path), path) for path in models]
    return max(scored, default=(0, None), key=lambda item: item[0])


def stage_sfm(is_video_scene: bool, work_dir: Path, images_dir: Path, logger: Logger,
              force: bool, mapper_mode: str):
    db_path = work_dir / "colmap.db"
    sparse_dir = work_dir / "sparse"
    if db_path.exists():
        db_path.unlink()
    if sparse_dir.exists():
        shutil.rmtree(sparse_dir)
    sparse_dir.mkdir(parents=True)

    logger.log("[sfm] feature_extractor 実行中(CPUモード、最大4096特徴)...")
    run(
        ["colmap", "feature_extractor",
         "--database_path", str(db_path),
         "--image_path", str(images_dir),
         "--ImageReader.single_camera", "1",
         "--ImageReader.camera_model", "SIMPLE_RADIAL",
         "--FeatureExtraction.use_gpu", "0",
         "--FeatureExtraction.max_image_size", "1600",
         "--SiftExtraction.max_num_features", "4096"],
        logger,
    )

    matcher = "sequential_matcher" if is_video_scene else "exhaustive_matcher"
    logger.log(f"[sfm] {matcher} 実行中(CPUモード)...")
    run(
        ["colmap", matcher,
         "--database_path", str(db_path),
         "--FeatureMatching.use_gpu", "0",
         "--FeatureMatching.max_num_matches", "16384"]
        + (["--SequentialMatching.overlap", "10",
            "--SequentialMatching.quadratic_overlap", "1"] if is_video_scene else []),
        logger,
    )

    total = len(list(images_dir.glob("*.jpg")))
    candidates = []

    if mapper_mode == "global":
        global_dir = work_dir / "sparse_global"
        shutil.rmtree(global_dir, ignore_errors=True)
        global_dir.mkdir(parents=True)
        logger.log("[sfm] view graphを較正後、Global Mapperを実行します")
        run(
            ["colmap", "view_graph_calibrator", "--database_path", str(db_path)],
            logger, fail_on_error=False,
        )
        proc = run(
            ["colmap", "global_mapper",
             "--database_path", str(db_path),
             "--image_path", str(images_dir),
             "--output_path", str(global_dir),
             "--GlobalMapper.gp_use_gpu", "0",
             "--GlobalMapper.ba_ceres_use_gpu", "0"],
            logger, fail_on_error=False,
        )
        if proc.returncode == 0:
            candidates.append(best_sparse_model(global_dir))

    best_registered, best_model = max(candidates, default=(0, None), key=lambda item: item[0])
    if mapper_mode == "incremental" or best_registered < max(3, math.ceil(total * 0.5)):
        incremental_dir = work_dir / "sparse_incremental"
        shutil.rmtree(incremental_dir, ignore_errors=True)
        incremental_dir.mkdir(parents=True)
        reason = "指定" if mapper_mode == "incremental" else f"Global Mapper登録率不足({best_registered}/{total})"
        logger.log(f"[sfm] Incremental Mapperを実行します ({reason})")
        proc = run(
            ["colmap", "mapper",
             "--database_path", str(db_path),
             "--image_path", str(images_dir),
             "--output_path", str(incremental_dir)],
            logger, fail_on_error=False,
        )
        if proc.returncode == 0:
            candidates.append(best_sparse_model(incremental_dir))

    registered, best_model = max(candidates, default=(0, None), key=lambda item: item[0])
    model_dir = sparse_dir / "0"
    if best_model is None or registered == 0:
        sys.exit("COLMAP の疎再構成に失敗しました(sparse/0 が生成されませんでした)。" + SHOOTING_GUIDE)
    shutil.copytree(best_model, model_dir)
    shutil.rmtree(work_dir / "sparse_global", ignore_errors=True)
    shutil.rmtree(work_dir / "sparse_incremental", ignore_errors=True)

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
    p.add_argument("--frames", type=int, default=None, help="動画から残すキーフレーム数(既定: プリセット依存)")
    p.add_argument("--long-edge", type=int, default=None, help="画像の長辺リサイズ(px)。0で原寸")
    p.add_argument("--only", choices=["extract", "sfm", "train", "view"], help="ステージ単体実行")
    p.add_argument("--view", action="store_true", help="学習後に自動でビューアを起動")
    p.add_argument("--force", action="store_true", help="SfM登録率が低くても続行する")
    p.add_argument("--steps", type=int, default=None, help="Brush学習ステップ数(プリセットを上書き)")
    p.add_argument("--max-splats", type=int, default=None, help="スプラット数上限(プリセットを上書き)")
    p.add_argument("--mapper", choices=["global", "incremental"], default="global",
                   help="COLMAPマッパー(既定global、登録率50%%未満ならincrementalへ自動フォールバック)")
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
    frames = args.frames or preset["frames"]

    if frames <= 0 or steps <= 0 or max_splats <= 0 or long_edge < 0:
        sys.exit("frames/steps/max-splats は正、long-edge は0以上を指定してください。")

    scene = resolve_scene(args)
    work_dir = WORK_DIR / scene
    images_dir = work_dir / "images"
    output_dir = OUTPUT_DIR / scene
    logger = Logger(LOGS_DIR / f"{scene}.log")

    logger.log(
        f"=== scene={scene} preset={args.preset} steps={steps} "
        f"frames={frames} long_edge={long_edge or '原寸'} max_splats={max_splats} "
        f"mapper={args.mapper} only={args.only or 'all'} ==="
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
        stage_extract(input_path, images_dir, frames, long_edge, logger)

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
        stage_sfm(is_video_scene, work_dir, images_dir, logger, args.force, args.mapper)

    if run_train:
        stage_train(work_dir, output_dir, scene, steps, max_splats, long_edge, args.with_viewer, logger)

    if run_view:
        stage_view(output_dir, scene, logger, args.port)

    logger.log("=== 完了 ===")
    if not run_view:
        print(f"\nビューアで確認するには: python3 splat.py --name {scene} --only view")


if __name__ == "__main__":
    main()
