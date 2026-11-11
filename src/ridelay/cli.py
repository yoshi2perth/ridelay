"""ridelay CLI."""


import time
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.progress import (BarColumn, MofNCompleteColumn, Progress, TextColumn,
                           TimeElapsedColumn, TimeRemainingColumn)
from rich.table import Table

from . import encoder
from .config import RenderConfig, parse_color
from .gpx_parser import GpxError, parse_gpx
from .processing import FIELDS, Telemetry, available_fields, build_telemetry
from .render import default_jobs, render_video
from .renderer import Renderer
from .timeutil import (TimeSpecError, format_hms, local_tz, parse_duration, parse_fps,
                       parse_size, parse_time_spec)

try:  # Python 3.9+
    from typing import Annotated
except ImportError:  # pragma: no cover
    from typing_extensions import Annotated

app = typer.Typer(
    help="GPX のテレメトリーを、アルファチャネル付きの透過動画として描画します。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console(stderr=True)


class Format(str, Enum):
    prores = "prores"
    webm = "webm"
    hevc = "hevc"
    png = "png"


class MapScope(str, Enum):
    full = "full"
    segment = "segment"


# ---------------------------------------------------------------- 共通オプション

GpxArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="GPX ファイル")]
SizeOpt = Annotated[str, typer.Option("--size", "-s", help="出力解像度 (3840x2160 / 4k / 2.7k / 1080p)")]
FpsOpt = Annotated[str, typer.Option("--fps", "-r", help="フレームレート (30, 60, 29.97 など)")]
StartOpt = Annotated[Optional[str], typer.Option(
    "--start", help="開始位置。経過時間 (00:12:30, 750, 12m30s)、時刻 (@09:15:30)、"
                    "日時 (2026-11-03T09:15:30) のいずれか")]
EndOpt = Annotated[Optional[str], typer.Option("--end", help="終了位置 (--start と同じ書式)")]
DurationOpt = Annotated[Optional[str], typer.Option("--duration", "-d", help="長さ (10m, 00:10:00, 600)")]
WeightOpt = Annotated[Optional[float], typer.Option("--weight", "-w", help="体重 kg (W/kg の計算に使用)")]
FieldsOpt = Annotated[str, typer.Option(
    "--fields", help=f"表示する数値と順番 (カンマ区切り: {','.join(FIELDS)})")]
NoGaugeOpt = Annotated[bool, typer.Option("--no-gauge", help="速度ゲージを描画しない")]
NoMapOpt = Annotated[bool, typer.Option("--no-map", help="コースマップを描画しない")]
MapScopeOpt = Annotated[MapScope, typer.Option(
    "--map-scope", help="full: GPX 全体のコース / segment: 書き出し区間のみのコース")]
MapSizeOpt = Annotated[str, typer.Option("--map-size", help="マップ領域の最大サイズ (1080p 基準 px, 幅x高さ)")]
ScaleOpt = Annotated[float, typer.Option("--scale", help="オーバーレイ全体の拡大率")]
MaxSpeedOpt = Annotated[float, typer.Option("--max-speed", help="速度ゲージの上限 km/h")]
TextColorOpt = Annotated[str, typer.Option("--text-color", help="文字色 #RRGGBB[AA]")]
DotColorOpt = Annotated[str, typer.Option("--dot-color", help="ドットの色 #RRGGBB[AA]")]
MapColorOpt = Annotated[str, typer.Option("--map-color", help="コース線の色 #RRGGBB[AA]")]
FontOpt = Annotated[Optional[str], typer.Option("--font", help="フォント名 または .ttf/.otf のパス")]
SmoothOpt = Annotated[float, typer.Option("--smooth", help="速度の平滑化 (秒)")]
PowerSmoothOpt = Annotated[float, typer.Option("--power-smooth", help="パワーの平滑化 (秒, 3 = 3秒平均)")]


def _fail(msg: str) -> "typer.Exit":
    console.print(f"[bold red]エラー:[/] {msg}")
    return typer.Exit(code=1)


def _load(gpx: Path, smooth: float, power_smooth: float) -> Telemetry:
    try:
        points = parse_gpx(gpx)
    except GpxError as e:
        raise _fail(str(e))
    return build_telemetry(points, smooth_speed=smooth, smooth_power=power_smooth)


def _resolve_range(tel: Telemetry, start: Optional[str], end: Optional[str],
                   duration: Optional[str]) -> "tuple[float, float]":
    try:
        s = parse_time_spec(start, tel.start) if start else 0.0
        if end and duration:
            raise _fail("--end と --duration は同時に指定できません")
        if end:
            e = parse_time_spec(end, tel.start)
        elif duration:
            e = s + parse_duration(duration)
        else:
            e = tel.duration
    except TimeSpecError as ex:
        raise _fail(str(ex))
    if e <= s:
        raise _fail("終了位置が開始位置より前です")
    if s < 0 or s > tel.duration:
        console.print(f"[yellow]注意:[/] 開始位置が GPX の記録範囲外です "
                      f"(記録は 0:00:00 〜 {format_hms(tel.duration)})")
    return s, e


def _build_config(tel: Telemetry, size: str, fps: str, start: Optional[str], end: Optional[str],
                  duration: Optional[str], weight: Optional[float], fields: str,
                  no_gauge: bool, no_map: bool, map_scope: MapScope, map_size: str,
                  scale: float, max_speed: float, text_color: str, dot_color: str,
                  map_color: str, font: Optional[str], quiet: bool = False) -> RenderConfig:
    try:
        w, h = parse_size(size)
        fps_v = parse_fps(fps)
        mw, mh = (float(v) for v in map_size.lower().split("x"))
    except (TimeSpecError, ValueError) as e:
        raise _fail(str(e))
    requested = [f.strip() for f in fields.split(",") if f.strip()]
    unknown = [f for f in requested if f not in FIELDS]
    if unknown:
        raise _fail(f"不明なフィールド: {', '.join(unknown)} (使用可能: {', '.join(FIELDS)})")
    ok_fields, notes = available_fields(tel, requested, weight)
    if not quiet:
        for n in notes:
            console.print(f"[yellow]注意:[/] {n}")
    s, e = _resolve_range(tel, start, end, duration)
    try:
        colors = dict(text_color=parse_color(text_color), dot_color=parse_color(dot_color),
                      map_color=parse_color(map_color))
    except ValueError as ex:
        raise _fail(str(ex))
    font_kw = {}
    if font:
        if Path(font).suffix.lower() in (".ttf", ".otf", ".ttc"):
            font_kw["font_path"] = font
        else:
            font_kw["font_family"] = font
    return RenderConfig(
        width=w, height=h, fps=fps_v, start_s=s, end_s=e, fields=ok_fields, weight=weight,
        scale=scale, max_speed=max_speed, show_gauge=not no_gauge, show_map=not no_map,
        map_scope=map_scope.value, map_width=mw, map_height=mh, **colors, **font_kw,
    )


DEFAULT_FIELDS = ",".join(FIELDS)


# ---------------------------------------------------------------- commands

@app.command()
def render(
    gpx: GpxArg,
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="出力先 (省略時は GPX と同じ場所)")] = None,
    fmt: Annotated[Format, typer.Option("--format", "-f", help="出力形式。Final Cut Pro なら prores 推奨")] = Format.prores,
    size: SizeOpt = "3840x2160",
    fps: FpsOpt = "30",
    start: StartOpt = None,
    end: EndOpt = None,
    duration: DurationOpt = None,
    weight: WeightOpt = None,
    fields: FieldsOpt = DEFAULT_FIELDS,
    no_gauge: NoGaugeOpt = False,
    no_map: NoMapOpt = False,
    map_scope: MapScopeOpt = MapScope.full,
    map_size: MapSizeOpt = "440x560",
    scale: ScaleOpt = 1.0,
    max_speed: MaxSpeedOpt = 70.0,
    text_color: TextColorOpt = "#FFFFFF",
    dot_color: DotColorOpt = "#3DDC97",
    map_color: MapColorOpt = "#FF5A1F",
    font: FontOpt = None,
    smooth: SmoothOpt = 3.0,
    power_smooth: PowerSmoothOpt = 3.0,
    jobs: Annotated[int, typer.Option("--jobs", "-j", help="並列プロセス数")] = default_jobs(),
) -> None:
    """GPX からアルファ付きのオーバーレイ動画を書き出す."""
    tel = _load(gpx, smooth, power_smooth)
    cfg = _build_config(tel, size, fps, start, end, duration, weight, fields, no_gauge, no_map,
                        map_scope, map_size, scale, max_speed, text_color, dot_color,
                        map_color, font)
    out = output or gpx.with_name(gpx.stem + "_overlay" + encoder.EXTENSIONS[fmt.value])
    if fmt != Format.png and out.suffix.lower() != encoder.EXTENSIONS[fmt.value]:
        console.print(f"[yellow]注意:[/] {fmt.value} の推奨拡張子は {encoder.EXTENSIONS[fmt.value]} です")

    total = cfg.frame_count
    console.print(
        f"[bold]出力:[/] {out}  [dim]({fmt.value}, {cfg.width}x{cfg.height}, "
        f"{float(cfg.fps):g}fps, {format_hms(cfg.duration)}, {total} フレーム, jobs={jobs})[/]"
    )
    console.print(f"[bold]表示:[/] {', '.join(cfg.fields) or '(数値なし)'}"
                  f"{' + ゲージ' if cfg.show_gauge else ''}{' + マップ' if cfg.show_map else ''}")

    t0 = time.time()
    with Progress(TextColumn("[progress.description]{task.description}"), BarColumn(),
                  MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn(),
                  console=console) as prog:
        task = prog.add_task("描画中", total=total)
        try:
            render_video(cfg, tel, fmt.value, out, jobs,
                         progress=lambda n: prog.advance(task, n))
        except (encoder.EncoderError, ValueError) as e:
            raise _fail(str(e))
    elapsed = time.time() - t0
    console.print(f"[green]完了[/] {elapsed:.1f} 秒 ({total / max(elapsed, 1e-9):.1f} fps)")

    if fmt != Format.png:
        res = encoder.probe(encoder.find_ffmpeg(), out)
        mark = "[green]✔ アルファチャネルあり[/]" if res.has_alpha else "[red]✘ アルファチャネルを確認できません[/]"
        console.print(f"{mark}  [dim]{res.stream_line}[/]")


@app.command()
def preview(
    gpx: GpxArg,
    at: Annotated[Optional[str], typer.Option("--at", help="描画する位置 (--start と同じ書式)。省略時は --start")] = None,
    output: Annotated[Path, typer.Option("--output", "-o", help="出力 PNG")] = Path("preview.png"),
    background: Annotated[Optional[str], typer.Option("--background", help="背景色 (確認用, 例 #808080)。省略時は透過")] = None,
    size: SizeOpt = "3840x2160",
    start: StartOpt = None,
    end: EndOpt = None,
    duration: DurationOpt = None,
    weight: WeightOpt = None,
    fields: FieldsOpt = DEFAULT_FIELDS,
    no_gauge: NoGaugeOpt = False,
    no_map: NoMapOpt = False,
    map_scope: MapScopeOpt = MapScope.full,
    map_size: MapSizeOpt = "440x560",
    scale: ScaleOpt = 1.0,
    max_speed: MaxSpeedOpt = 70.0,
    text_color: TextColorOpt = "#FFFFFF",
    dot_color: DotColorOpt = "#3DDC97",
    map_color: MapColorOpt = "#FF5A1F",
    font: FontOpt = None,
    smooth: SmoothOpt = 3.0,
    power_smooth: PowerSmoothOpt = 3.0,
) -> None:
    """1 フレームだけ PNG に書き出して見た目を確認する."""
    tel = _load(gpx, smooth, power_smooth)
    cfg = _build_config(tel, size, "30", start, end, duration, weight, fields, no_gauge, no_map,
                        map_scope, map_size, scale, max_speed, text_color, dot_color,
                        map_color, font)
    try:
        ts = parse_time_spec(at, tel.start) if at else cfg.start_s
        bg = parse_color(background) if background else None
    except (TimeSpecError, ValueError) as e:
        raise _fail(str(e))
    Renderer(cfg, tel).save_png(ts, str(output), bg)
    console.print(f"[green]保存しました:[/] {output}  [dim](経過 {format_hms(ts)})[/]")


@app.command()
def info(gpx: GpxArg) -> None:
    """GPX の概要 (記録時間・含まれるデータ) を表示する."""
    import numpy as np

    tel = _load(gpx, 3.0, 3.0)
    start_local = tel.start.astimezone(local_tz())
    fmt_dt = "%Y-%m-%d %H:%M:%S %Z"

    t = Table(show_header=False, box=None)
    t.add_row("開始", start_local.strftime(fmt_dt))
    t.add_row("終了", (start_local + timedelta(seconds=tel.duration)).strftime(fmt_dt))
    t.add_row("記録時間", format_hms(tel.duration))
    t.add_row("距離", f"{tel.dist[-1] / 1000:.2f} km")
    t.add_row("速度", f"平均 {np.mean(tel.speed):.1f} / 最高 {np.max(tel.speed):.1f} km/h")

    def stat(label: str, name: str, unit: str) -> None:
        if tel.has[name]:
            arr = getattr(tel, name)
            t.add_row(label, f"平均 {np.nanmean(arr):.0f} / 最大 {np.nanmax(arr):.0f} {unit}")
        else:
            t.add_row(label, "[dim]データなし[/]")

    stat("心拍", "hr", "bpm")
    stat("ケイデンス", "cadence", "rpm")
    stat("パワー", "power", "W")
    if tel.has["ele"]:
        t.add_row("標高", f"{np.nanmin(tel.ele):.0f} 〜 {np.nanmax(tel.ele):.0f} m")
        t.add_row("斜度", f"{np.nanmin(tel.grade):.1f} 〜 {np.nanmax(tel.grade):.1f} %")
    else:
        t.add_row("標高", "[dim]データなし[/]")
    console.print(t)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
