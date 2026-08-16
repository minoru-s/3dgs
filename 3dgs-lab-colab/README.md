# 3dgs-lab-colab

[3dgs-lab](../3dgs-lab/)(Mac + Brush + COLMAP CPU)と同じ入力(動画/画像)から、**Google Colab の NVIDIA GPU(CUDA)** を使って
[FastGS](https://github.com/fastgs/FastGS) または [gsplat](https://github.com/nerfstudio-project/gsplat) で学習し、`.ply` を持ち帰るための構成です。

ローカルでずっと動かし続けたくない/CUDAで速く済ませたい場合に使います。学習エンジンが変わるだけで、
**出力はローカル版と同じ標準的な3DGS `.ply`形式**なので、閲覧は `3dgs-lab/viewer/index.html` をそのまま使い回せます。

## 使い方

1. `colab_pipeline.ipynb` を [Google Colab](https://colab.research.google.com/) で開く(アップロードするか、GitHub経由で開く)
2. メニュー **ランタイム > ランタイムのタイプを変更** で **GPU** (T4など)を選択
3. 上から順にセルを実行:
   - GPU確認（CPUランタイムなら即停止）
   - 設定とGoogle Driveの書き込み検証
   - セットアップ(CUDA PyCOLMAP/ffmpeg/FastGS導入)
   - 動画 or 画像のアップロード
   - 高被覆キーフレーム選別 → GPU SfM → FastGS高品質学習
   - Drive上の `.ply`、品質指標、manifestを検証（ブラウザダウンロードは任意）
4. Driveから取得した `.ply` を `3dgs-lab/viewer/index.html` にドラッグ&ドロップして見る
   (または `3dgs-lab/output/<シーン名>/<シーン名>.ply` に置いて `python3 splat.py --name <シーン名> --only view`)

## ローカル版との対応

| ローカル版(splat.py) | Colab版 |
|---|---|
| `--preset` | `PROFILE` (`fast_robust` / `fast_quality` / `fast_detail` / `quick` / `balanced` / `quality`) |
| Brush(wgpu/Metal) | `fast_robust` / `fast_quality` / `fast_detail`: FastGS(CUDA)、ほか: gsplat(CUDA/MCMC) |
| COLMAP CPUモード | `pycolmap-cuda12`（SIFT抽出・照合をGPU化、Global Mapper） |
| `viewer/index.html` | 同じものをそのまま使用 |

推奨の `fast_robust`: 180キーフレーム・長辺1600px・30,000 iteration・SH degree 3。FastGSのレンダリング回数は増やさず、次を変更します。

- min-max正規化した残差ではなく、露出差補正後の絶対誤差・中央値/MAD・上位quantileを併用する
- ランダム10視点ではなく、カメラ位置を広く覆う10視点を選ぶ
- 同じGaussianに3視点以上かつ可視視点の40%以上から誤差支持がある場合だけdensify/prune対象にする
- 高次SHを1,000/2,000/3,000ではなく2,000/5,000/8,000 iterationで段階的に有効化し、学習率も0.02→0.01へ下げる
- 観測視点が少ない空間領域をphotometric pruningから保護する

これにより、スマホ動画のセンサーノイズや露出差を「再現すべき細部」と誤認しにくくしつつ、入力枚数・解像度・反復数は削りません。T4での初回実測は全工程29.1分（151/180枚登録、131,667 Gaussian）で30分目標を満たしました。ただし広い室内の目視評価では細部不足があり、多視点合意によるdensify抑制と実効視点被覆は引き続き改善対象です。

比較用の `fast_quality`: 公式FastGS room/base寄りの設定です。入力条件は `fast_robust` と同じですが、公式のmin-max残差、ランダム10視点、早いSH有効化を使います。

実験用の `fast_detail`: 入力枚数・解像度・iteration・SH degreeは同じまま、densify間隔を500→100、絶対勾配閾値を0.0008→0.0004、loss thresholdを0.10→0.06にします。細部だけでなく入力ノイズも鮮明化する実測例があるため、通常は推奨しません。

### ロバスト版を実行する

設定セルで次を選ぶだけです。

```python
PROFILE = "fast_robust"
```

ノートブックはFastGS公式commit `44e02a5` のclone直後に [`fastgs_robust.patch`](fastgs_robust.patch) を適用します。パッチは埋め込みbytesのSHA-256と `git apply --check` を検証してから適用されます。固定commitと差分が合わなければ、誤った条件で学習を始めずセットアップ時点で停止します。

### 手動で旧高精細版を使う場合

コードを丸ごと差し替えずに試すなら、設定セルの `PROFILE` を `"fast_detail"` に変えるだけです。旧ノートブックへ手作業で移植する場合は、FastGS設定を次の3箇所だけ変更し、学習コマンドに `--loss_thresh` を追加します。

```python
FASTGS_DENSIFICATION_INTERVAL = 100
FASTGS_GRAD_ABS_THRESH = 0.0004
FASTGS_LOSS_THRESH = 0.06
```

```python
"--loss_thresh", str(FASTGS_LOSS_THRESH),
```

`fast_detail` でCUDA out of memoryになった場合は、まず `loss_thresh` を `0.08`、次に `densification_interval` を `200` へ戻します。画像枚数・解像度・SH degreeを先に下げると細部を失いやすいので、最後の手段にします。

目標は **全工程30分未満** です。ただし割当GPU、初回CUDA extensionビルド、撮影内容、登録率、Drive速度で変動します。ノートブックは全工程時間、Gaussian数、固定参照viewのPSNR/SSIM/LPIPSを毎回記録します。180枚はすべて学習に使うため、この値は未知視点への汎化ではなく再構成忠実度の比較値です。ノイズと空間保持は同じビューア経路で比較してください。

### Driveに残るもの

各実行は `MyDrive/3dgs-lab/<scene>/<scene>_<実行時刻>/` に保存されます。

- `selected_images.zip`: 選別済み画像。動画を再アップロードせず再開可能
- `sfm_checkpoint.zip`: `sparse/0` のカメラ姿勢・疎点群
- `sfm_manifest.json`: 登録枚数と登録率
- `ply/point_cloud_*.ply`: 学習中間点と最終成果物
- `fastgs_model/`: 15,000 iterationチェックポイント、30,000 iterationモデル、評価レンダリング
- `result_manifest.json`: 設定、サイズ、Gaussian数、SHA-256、学習/評価/全工程時間、PSNR/SSIM/LPIPS、ロバストパッチ条件

同じrunの学習セルを再実行した場合は、最終モデルがあれば再利用し、途中なら15,000 iterationチェックポイントから自動再開します。

## 注意・既知の制約

- ノートブックのJSON・全Pythonセルの構文は検証済みです。CUDA版PyCOLMAP 4.1.1とFastGS公式commit `44e02a5` に固定し、T4で完走を確認しています。30分達成は速度の実測であり、広い空間の画質保証ではありません。
- 無料版ColabのT4はセッション時間制限・切断があります。長時間の学習は要注意。
- FastGSのpaper-quality推奨VRAMは24GBで、T4は通常16GBです。FastGS系は入力画像をCPU側に置いてGaussian用VRAMを確保します（画質は変えず、画像転送分だけわずかに遅くなります）。`fast_detail` はGaussian数が増えやすく、T4でのOOMリスクは高めです。
- 元のアップロード動画だけはランタイム上の一時データです（元動画はMacにある前提）。その後の再開可能な中間物とPLYはDriveに段階保存されます。
- FastGS系は15,000 iterationにもPLYとチェックポイントを保存します。それより前にランタイム自体が失われても、選別画像とSfMチェックポイントは残ります。
- 撮影ガイド・トラブルシュートは [3dgs-lab/README.md](../3dgs-lab/README.md) を参照してください(共通)。
