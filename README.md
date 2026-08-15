# 3dgs

スマホ等で撮影した**動画または複数枚の静止画**から [3D Gaussian Splatting](https://repo-sam.inria.fr/fungraph/3d-gaussian-splatting/) でシーンを再構成し、ブラウザのローカルビューアでインタラクティブに閲覧するためのパイプライン群です。

## 構成

| ディレクトリ | 内容 | 状態 |
|---|---|---|
| [`3dgs-lab/`](3dgs-lab/) | macOSネイティブ版。[Brush](https://github.com/ArthurBrussee/brush)(wgpu/Metal) + [COLMAP](https://colmap.github.io/)(CPU)。CUDA不使用 | **メイン、稼働中** |
| [`3dgs-lab-colab/`](3dgs-lab-colab/) | Google Colab版。CUDA PyCOLMAP + [gsplat](https://github.com/nerfstudio-project/gsplat)で高速化し、Google Driveへ段階保存 | **高速・永続保存版** |

どちらも入出力は標準的な3DGS `.ply`形式で揃えてあるため、学習側だけ差し替えてビューアは共通で使えます。

## 動作要件

- **`3dgs-lab`**: macOS, Apple Silicon(arm64)必須。Homebrew, Xcode Command Line Tools。ディスク空き20GB以上推奨。Intel Macでは動作しません。
- **`3dgs-lab-colab`**: Google Colabアカウント(GPUランタイム)のみ。ローカル環境は問いません。
- ビューア(`3dgs-lab/viewer/index.html`)はChrome推奨(three.js + WebGL)。

## 使い方(概要)

```bash
cd 3dgs-lab
./setup.sh                                  # ffmpeg/colmap/brushを導入(初回のみ)
source .venv/bin/activate
python3 splat.py input/myvideo.mp4 --view   # 動画から再構成してビューアを開く
```

詳しいCLIオプション・撮影ガイド・トラブルシュートは [`3dgs-lab/README.md`](3dgs-lab/README.md) を参照してください。
