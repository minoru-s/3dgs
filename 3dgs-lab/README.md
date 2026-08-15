# 3dgs-lab

スマホ等で撮影した**動画または複数枚の静止画**から、[Brush](https://github.com/ArthurBrussee/brush) と [COLMAP](https://colmap.github.io/) を使って macOS ネイティブ（Apple Silicon / Metal）で 3D Gaussian Splatting のシーンを再構成し、ブラウザのローカルビューアでインタラクティブに閲覧するためのパイプラインです。

CUDA は使いません。学習は Brush（wgpu/Metal）、SfM（カメラ姿勢推定）は COLMAP の CPU モードで行います。動画は一度多めに候補を抽出し、時間範囲を保ちながらブレと重複の少ないキーフレームへ絞ります。

## 0. 必要環境

- macOS, Apple Silicon（arm64）必須。Intel Mac では動作しません。
- Homebrew, Xcode Command Line Tools
- ディスク空き 20GB 以上推奨

## 1. セットアップ

```bash
cd 3dgs-lab
./setup.sh
```

`ffmpeg` / `colmap` を Homebrew で、`brush` を GitHub Releases の macOS(arm64) バイナリでダウンロードします。何度実行しても安全です（導入済みのものはスキップ）。

### Gatekeeper 警告について

`brush_app` は Apple の公証（notarization）を受けていないバイナリです。`setup.sh` は自動的に `xattr -d com.apple.quarantine` で quarantine 属性を外しますが、それでも初回起動時に「開発元を確認できないため開けません」という警告が出る場合は、

**システム設定 > プライバシーとセキュリティ** を開き、下の方にある「このまま開く」を選択してください。

## 2. 使い方

```bash
source .venv/bin/activate

# 動画から（60〜120秒の動画を input/ に置く）
python3 splat.py input/myvideo.mp4 --view

# 画像フォルダから（100〜300枚を input/myphotos/ に置く）
python3 splat.py input/myphotos --preset high --view
```

`--view` を付けると学習後に自動でブラウザ（ビューア）が開きます。

### CLI オプション

```
python3 splat.py <input> [options]
  <input>            .mp4/.mov 動画、または画像フォルダ
  --name NAME        シーン名(省略時は入力ファイル/フォルダ名)
  --preset {quick,standard,high}   既定 standard
  --frames N         動画から残すキーフレーム数(既定: プリセット依存)
  --long-edge PX     画像の長辺リサイズ(既定: プリセット依存。0で原寸)
  --only {extract,sfm,train,view}  ステージ単体実行
  --view             学習後に自動でビューアを起動
  --force            SfM登録率が低くても続行する
  --steps N          Brush学習ステップ数(プリセットを上書き)
  --max-splats N     スプラット数上限(プリセットを上書き)
  --mapper {global,incremental}  既定 global。登録率不足時は自動フォールバック
  --with-viewer      学習中にBrushのGUIを開く(デバッグ用)
  --port PORT        ビューア用HTTPサーバのポート(既定 8000)
```

### プリセット

| preset | キーフレーム | 反復数 | 長辺 | スプラット上限 | 想定用途 |
|---|---:|---:|---:|---:|---|
| quick | 80 | 5,000 | 1080 | 35万 | 撮影・形状確認 |
| standard | 120 | 10,000 | 1280 | 60万 | 通常利用（推奨） |
| high | 180 | 20,000 | 1600 | 120万 | 品質優先 |

1M スプラット ≈ 2GB メモリが目安です。従来の `standard`（30,000 step・200万上限）はM2/16GBには重すぎたため、既定値を実測に基づき下げています。メモリが厳しい場合は `--max-splats` をさらに下げてください。

### 軽量化の仕組み

- 動画から目標枚数の約2倍を候補抽出し、各時間区間から「シャープネス70% + 画面変化30%」で1枚を選びます。撮影全体を残したまま、ブレとほぼ同一のフレームを減らします。
- カメラモデルは過剰な自由度を避けた `SIMPLE_RADIAL`、SIFTは最大4,096特徴、動画の照合は隣接10枚 + 二次間隔です。
- COLMAP 4.1のGlobal Mapperを先に使い、登録率50%未満または失敗時だけ従来のIncremental Mapperへフォールバックします。
- HDR/HLG/PQ動画は、ffmpegに`zscale`があればSDRへトーンマッピングしてから処理します。

### ステージ単体実行

各ステージは独立して呼び出せます(パラメータを変えて途中からやり直したい場合など)。

```bash
python3 splat.py input/myvideo.mp4 --name myscene --only extract
python3 splat.py --name myscene --only sfm
python3 splat.py --name myscene --only train --steps 10000
python3 splat.py --name myscene --only view
```

## 3. ディレクトリ構成

```
3dgs-lab/
├── README.md
├── setup.sh
├── splat.py
├── viewer/index.html      # Webビューア(three.js + GaussianSplats3D)
├── input/                 # 動画/画像フォルダを置く場所
├── work/<scene>/          # 中間生成物(images/, colmap.db, sparse/0/...)
├── output/<scene>/        # 最終 .ply
└── logs/<scene>.log       # 実行ログ
```

## 4. ビューアについて

- `--view` を付けるか `python3 splat.py --name <scene> --only view` で、`python3 -m http.server` が起動しブラウザが自動で開きます。
- **Chrome 推奨**です(WebGPU/WebGLの都合上、Safari は不安定なことがあります)。
- `viewer/index.html` は `?file=` クエリでも直接開けます。ページが `viewer/` 配下にあるため、パスは**サーバのルートからの絶対パス(先頭に `/`)** で指定してください:
  `http://localhost:8000/viewer/index.html?file=/output/myscene/myscene.ply`
- `.ply` / `.splat` / `.ksplat` ファイルをブラウザにドラッグ&ドロップしても読み込めます。
- gsplat出力は通常 `+Z` が上方向です。床が傾く場合は上部の「上方向」を切り替え、「水平に戻す」を押してください（`H`キーでも実行できます）。
- 通常はGoogle Mapsの3D表示に近く、左ドラッグで左右旋回と上下チルトができます。地面の上下は保たれ、床の下へ回り込んで反転しません。「水平だけに固定」をオンにすると左右旋回だけに制限できます。
- **`file://` で直接開くことはできません**(モジュール読み込み・fetch の制約上、必ずローカルサーバ経由にしてください)。

### 代替の閲覧・編集経路

- Brush 自体をビューアとして使う: `.tools/brush-app-aarch64-apple-darwin/brush_app path/to/scene.ply`
- [SuperSplat](https://playcanvas.com/supersplat/editor)(Web) に `.ply` をドラッグ&ドロップして編集・軽量化(`.ksplat`化などファイルサイズ削減)
- 将来的に Unity で見たい場合: [aras-p/UnityGaussianSplatting](https://github.com/aras-p/UnityGaussianSplatting)(Metal対応)に `.ply` を持ち込めます

## 5. 撮影ガイド

きれいに再構成するために:

- **静止シーン限定**。動く人・ペット・揺れる植物・照明変化は NG。
- 対象の**周囲を回り込む**ように撮影する。その場で回転するだけのパノラマ撮影は SfM(カメラ位置推定)が破綻します。
- 隣接フレームの重なりを大きめに、ゆっくり移動。可能なら露出固定。
- 動画なら 60〜120秒、高さを2〜3段変えて周回。写真なら 100〜300枚。
- 苦手なもの: 鏡・ガラス・水面・真っ白な壁。得意なもの: 屋外曇天や均一照明、模様のある静物。

## 6. トラブルシューティング

- **COLMAP の登録画像数が少ない(50%未満で停止)**: ほぼ撮影起因です。上の撮影ガイドを見直してください。撮影をやり直さずに強行したい場合は `--force` を付けられますが品質は保証されません。
- **`brush_app` が開けない(Gatekeeper)**: 上記「Gatekeeper 警告について」を参照。
- **メモリ不足で学習が落ちる**: `--max-splats` や `--long-edge` を下げて再実行してください(自動フォールバックはありません)。
- **画像枚数を増やすと COLMAP が急に遅くなる**: COLMAP は CPU 実行のため画像数に対して非線形に遅くなります。まず `standard` の120枚で登録率を確認してください。
- **Global Mapperで登録できない**: 既定では登録率50%未満になるとIncremental Mapperを自動実行します。最初から従来方式を使う場合は `--mapper incremental` を指定できます。
- **Safari で表示が乱れる/真っ黒**: Chrome で開き直してください。
- **HEIC / Live Photo**: `input/` には `.mp4`/`.mov` などの動画、または `.jpg`/`.png` 等の画像フォルダを置いてください。HEIC 画像は macOS 標準の `sips -s format jpeg in.heic --out out.jpg` などで事前に JPEG へ変換してください。

## 7. スコープ外

- メッシュ抽出(SuGaR / 2DGS 等) — 出力はあくまでスプラット(.ply)
- 動的シーン(4DGS)、少数枚/単眼画像からの生成
- 実寸スケールの復元(単眼 SfM のためスケール不定)

## 8. 参考

- Brush: https://github.com/ArthurBrussee/brush
- COLMAP: https://colmap.github.io/
- Web ビューア: https://github.com/mkkellogg/GaussianSplats3D
- 編集/軽量化: https://github.com/playcanvas/supersplat
