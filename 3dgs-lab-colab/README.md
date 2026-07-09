# 3dgs-lab-colab

[3dgs-lab](../3dgs-lab/)(Mac + Brush + COLMAP CPU)と同じ入力(動画/画像)から、**Google Colab の NVIDIA GPU(CUDA)** を使って
[gsplat](https://github.com/nerfstudio-project/gsplat) で高速に学習し、`.ply` だけを持ち帰るための構成です。

ローカルでずっと動かし続けたくない/CUDAで速く済ませたい場合に使います。学習エンジンが変わるだけで、
**出力はローカル版と同じ標準的な3DGS `.ply`形式**なので、閲覧は `3dgs-lab/viewer/index.html` をそのまま使い回せます。

## 使い方

1. `colab_pipeline.ipynb` を [Google Colab](https://colab.research.google.com/) で開く(アップロードするか、GitHub経由で開く)
2. メニュー **ランタイム > ランタイムのタイプを変更** で **GPU** (T4など)を選択
3. 上から順にセルを実行:
   - セットアップ(COLMAP/ffmpeg/gsplat導入)
   - 設定(シーン名、フレーム数、解像度、ステップ数、スプラット上限)
   - 動画 or 画像のアップロード
   - フレーム抽出 → COLMAP(SfM) → gsplat学習(CUDA)
   - `.ply` のダウンロード
4. ダウンロードした `.ply` を `3dgs-lab/viewer/index.html` にドラッグ&ドロップして見る
   (または `3dgs-lab/output/<シーン名>/<シーン名>.ply` に置いて `python3 splat.py --name <シーン名> --only view`)

## ローカル版との対応

| ローカル版(splat.py) | Colab版 |
|---|---|
| `--preset` | ノートブック内の設定セル(FRAMES_TARGET/LONG_EDGE/MAX_STEPS/CAP_MAX_SPLATS) |
| Brush(wgpu/Metal) | gsplat(CUDA、`mcmc`戦略 = Brushの`--max-splats`と同じMCMC方式) |
| COLMAP CPUモード | COLMAP(Colab上、CPUビルド。SfMは全体の数%なのでボトルネックにならない) |
| `viewer/index.html` | 同じものをそのまま使用 |

既定値(速さ重視): 200フレーム・長辺1600px・15,000ステップ・スプラット上限150万。
Colab Pro/Pro+でより強いGPU(A100等)が使えるなら、ステップ数や解像度を上げても十分な速度が出るはずです。

## 注意・既知の制約

- **このノートブックはgsplat公式ソース(README/`examples/simple_trainer.py`)を読んで作成したもので、
  実際にColabのGPUランタイム上で最後まで実行検証はできていません**(Claude CodeはColab実行環境に
  直接アクセスできないため)。`pip install`まわりでエラーが出た場合は内容を教えてください。
- 無料版ColabのT4はセッション時間制限・切断があります。長時間の学習は要注意。
- アップロードした動画/画像やCOLMAPの中間生成物はColabのセッション終了とともに消えます。
  必要なら `.ply` だけでなく `sparse/` 等も明示的にダウンロード/Driveに保存してください。
- 撮影ガイド・トラブルシュートは [3dgs-lab/README.md](../3dgs-lab/README.md) を参照してください(共通)。
