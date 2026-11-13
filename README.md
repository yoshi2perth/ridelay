# ridelay

Garmin などの GPX / TCX からテレメトリー（速度ゲージ・速度・心拍・ケイデンス・パワー・W/kg・斜度・コースマップ）を描画し、**背景が透明（アルファチャネル付き）な動画**を書き出す CLI です。
Final Cut Pro などで、アクションカメラの映像の上にそのまま重ねて使えます。

## セットアップ

```bash
cd ridelay
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

FFmpeg は `pip` で入る `imageio-ffmpeg` に同梱されているものを自動で使います（`PATH` に `ffmpeg` があればそちらを優先します）。

## 使い方

```bash
# GPX または TCX の概要（記録時間・含まれているデータ）を確認
ridelay info ride.gpx
ridelay info activity.tcx

# 1 フレームだけ PNG で見た目を確認（--background で背景色を付けると見やすい）
ridelay preview ride.gpx --at 00:12:30 --weight 65 --background "#606060" -o check.png

# 4K / 30fps の ProRes 4444（Final Cut Pro 向け）で書き出し
ridelay render ride.gpx --weight 65 -o overlay.mov

# 区間を指定（カメラで撮影していた時間だけを書き出す）
ridelay render ride.gpx --start @09:15:30 --duration 10m --weight 65
ridelay render ride.gpx --start 00:42:00 --end 00:55:00 --size 2.7k --fps 60
```

### 時刻の指定方法（`--start` / `--end` / `--at`）

| 書き方 | 意味 |
|---|---|
| `00:12:30`, `750`, `12m30s` | GPX の記録開始からの経過時間 |
| `@09:15:30` | ライド当日のローカル時刻（アクションカメラの撮影時刻を合わせるときに便利） |
| `2026-11-03T09:15:30` | 日時（タイムゾーンを書かなければローカル時刻） |

### 主なオプション

| オプション | 既定値 | 説明 |
|---|---|---|
| `-f, --format` | `prores` | `prores`（ProRes 4444 / .mov）・`webm`（VP9）・`hevc`（HEVC with Alpha・実験的）・`png`（連番） |
| `-s, --size` | `3840x2160` | `4k` / `2.7k` / `1080p` / `幅x高さ` |
| `-r, --fps` | `30` | `29.97`、`60` なども指定できます |
| `-w, --weight` | なし | 体重 (kg)。指定したときだけ W/kg を表示します |
| `--fields` | `speed,hr,cadence,power,wkg,grade` | 表示する数値とその順番 |
| `--no-gauge` / `--no-map` | | 速度ゲージ／コースマップを描画しない |
| `--map-scope` | `full` | `segment` にすると、書き出す区間のコースだけをマップに描きます |
| `--scale` | `1.0` | オーバーレイ全体の拡大率 |
| `--max-speed` | `70` | 速度ゲージの上限 (km/h) |
| `--text-color` / `--dot-color` / `--map-color` | 白 / 緑 / オレンジ | `#RRGGBB[AA]` 形式 |
| `--font` | Helvetica Neue Bold | フォント名、または `.ttf` / `.otf` のパス |
| `--interpolate-metrics` / `--smooth-metrics` | なし（オフ） | 数値表示を1秒ごとの固定更新ではなく、フレーム間で線形補間して滑らかに表示 |
| `-j, --jobs` | CPU コア数 − 1（最大 8） | 並列で描画するプロセス数 |

### データが無い項目の扱い

- パワーデータ（GPX の `<power>` や TCX の `<Watts>`）が無いファイルでは、**W と W/kg は描画しません**（理由を表示してスキップします）。
- 心拍・ケイデンス・標高（斜度）も同じで、データが無ければその行を詰めて表示します。

## Final Cut Pro での使い方

1. `overlay.mov` を読み込み、アクションカメラのクリップの**上のレーン**（接続クリップ）に置きます。
2. 合成モードは「標準」のままで、テレメトリーの部分以外は透けて見えます。
3. 時刻のずれは、クリップを左右にずらして調整してください（再出力する場合は `--start` を変えます）。

## 計算の仕様

- **速度**: GPX に速度の値は含まれないため、座標と時刻から距離を計算し、`--smooth` 秒（既定 3 秒）で平滑化します。
- **斜度**: 標高を 5 秒で平滑化したうえで、前後 25 m（計 50 m）の区間の高低差から計算します。
- **パワー**: `--power-smooth` 秒の移動平均（既定 3 秒）。
- **数値の更新**: 既定ではサイコンと同じく 1 秒ごとに更新します（`--interpolate-metrics` または `--smooth-metrics` を指定するとフレーム間で線形補間して滑らかに更新）。ゲージとマップのドットは常にフレームごとに補間して滑らかに動かします。
- **記録の途切れ**: 10 秒を超える空白（オートポーズなど）の間は、速度・パワー・ケイデンスを 0 として扱います。

## 性能の目安（Apple M1・8 コアで計測）

| 解像度 | 書き出し速度 | ProRes 4444 のビットレート |
|---|---|---|
| 1080p30 | 約 44 fps（実時間の約 1.5 倍の速さ） | 約 70 Mbps |
| 4K30 | 約 13 fps（実時間の約 0.4 倍の速さ） | 約 160 Mbps（1 時間で約 70 GB） |

時間がかかっているのはほぼ ProRes のエンコード（ソフトウェア処理）で、描画自体は 4K でも 1 フレーム約 9 ms です。長いライドは、`--start` / `--end` で撮影していた区間だけを書き出すのがおすすめです。

## 開発

```bash
.venv/bin/pytest
.venv/bin/python scripts/make_sample_gpx.py samples/sample.gpx --minutes 5   # サンプル GPX の生成
```
