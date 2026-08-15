# 3dgs-lab-colab

[3dgs-lab](../3dgs-lab/)(Mac + Brush + COLMAP CPU)と同じ入力(動画/画像)から、**Google Colab の NVIDIA GPU(CUDA)** を使って
[gsplat](https://github.com/nerfstudio-project/gsplat) で高速に学習し、`.ply` だけを持ち帰るための構成です。

ローカルでずっと動かし続けたくない/CUDAで速く済ませたい場合に使います。学習エンジンが変わるだけで、
**出力はローカル版と同じ標準的な3DGS `.ply`形式**なので、閲覧は `3dgs-lab/viewer/index.html` をそのまま使い回せます。

## 使い方

1. `colab_pipeline.ipynb` を [Google Colab](https://colab.research.google.com/) で開く(アップロードするか、GitHub経由で開く)
2. メニュー **ランタイム > ランタイムのタイプを変更** で **GPU** (T4など)を選択
3. 上から順にセルを実行:
   - GPU確認（CPUランタイムなら即停止）
   - 設定とGoogle Driveの書き込み検証
   - セットアップ(CUDA PyCOLMAP/ffmpeg/gsplat導入)
   - 動画 or 画像のアップロード
   - 有効キーフレーム選別 → GPU SfM → 軽量gsplat学習
   - Drive上の `.ply` とmanifestを検証（ブラウザダウンロードは任意）
4. Driveから取得した `.ply` を `3dgs-lab/viewer/index.html` にドラッグ&ドロップして見る
   (または `3dgs-lab/output/<シーン名>/<シーン名>.ply` に置いて `python3 splat.py --name <シーン名> --only view`)

## ローカル版との対応

| ローカル版(splat.py) | Colab版 |
|---|---|
| `--preset` | `PROFILE` (`quick` / `balanced` / `quality`) |
| Brush(wgpu/Metal) | gsplat(CUDA、`mcmc`戦略 = Brushの`--max-splats`と同じMCMC方式) |
| COLMAP CPUモード | `pycolmap-cuda12`（SIFT抽出・照合をGPU化、Global Mapper） |
| `viewer/index.html` | 同じものをそのまま使用 |

推奨の `balanced`: 120キーフレーム・長辺1280px・8,000ステップ・スプラット上限60万・SH degree 2。
前回のT4実測（旧設定）は86分でしたが、旧設定はSfMをCPUで動かし、15,000ステップの多くを約150万Gaussianで処理していました。新設定の目標は **20〜35分程度** です。ただしGPU割当、撮影内容、登録率、Drive速度で変動し、まだ同一動画での完走実測前です。

### Driveに残るもの

各実行は `MyDrive/3dgs-lab/<scene>/<scene>_<実行時刻>/` に保存されます。

- `selected_images.zip`: 選別済み画像。動画を再アップロードせず再開可能
- `sfm_checkpoint.zip`: `sparse/0` のカメラ姿勢・疎点群
- `sfm_manifest.json`: 登録枚数と登録率
- `ply/point_cloud_*.ply`: 学習中間点と最終成果物
- `result_manifest.json`: 設定、サイズ、SHA-256、学習時間

## 注意・既知の制約

- ノートブックのJSON・全Pythonセルの構文とローカル側CLIは検証済みです。CUDA版PyCOLMAP 4.1.1のAPIに合わせていますが、新しい高速経路は同一動画・T4での完走実測前です。
- 無料版ColabのT4はセッション時間制限・切断があります。長時間の学習は要注意。
- 元のアップロード動画だけはランタイム上の一時データです（元動画はMacにある前提）。その後の再開可能な中間物とPLYはDriveに段階保存されます。
- `balanced` は4,000 stepにもPLYを保存します。4,000 stepより前にランタイム自体が失われた場合でも、選別画像とSfMチェックポイントは残ります。
- 撮影ガイド・トラブルシュートは [3dgs-lab/README.md](../3dgs-lab/README.md) を参照してください(共通)。
