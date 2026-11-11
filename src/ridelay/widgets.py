"""描画ウィジェット.

座標はすべて「高さ 1080px」を基準に設計し、RenderConfig.unit で拡大縮小する。
"""

from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
import skia

from .config import RenderConfig
from .processing import Telemetry, moving_average


# ---------------------------------------------------------------- helpers

def load_typeface(cfg: RenderConfig) -> skia.Typeface:
    mgr = skia.FontMgr()
    if cfg.font_path:
        tf = skia.Typeface.MakeFromFile(cfg.font_path)
        if tf is None:
            raise ValueError(f"フォントを読み込めません: {cfg.font_path}")
        return tf
    tf = mgr.matchFamilyStyle(cfg.font_family, skia.FontStyle.Bold())
    if tf is None:
        tf = mgr.legacyMakeTypeface("", skia.FontStyle.Bold())
    return tf


class TextPainter:
    """縁取り付きテキスト."""

    def __init__(self, cfg: RenderConfig, typeface: skia.Typeface, size: float,
                 outline: float):
        self.font = skia.Font(typeface, size)
        self.font.setEdging(skia.Font.Edging.kAntiAlias)
        self.fill = skia.Paint(AntiAlias=True, Color=cfg.text_color)
        self.stroke = skia.Paint(
            AntiAlias=True, Color=cfg.outline_color, Style=skia.Paint.kStroke_Style,
            StrokeWidth=outline, StrokeJoin=skia.Paint.kRound_Join,
        )
        self.size = size

    def width(self, text: str) -> float:
        return self.font.measureText(text)

    def draw(self, canvas: skia.Canvas, text: str, x: float, y: float,
             align: str = "left") -> None:
        if align == "right":
            x -= self.width(text)
        elif align == "center":
            x -= self.width(text) / 2
        canvas.drawString(text, x, y, self.font, self.stroke)
        canvas.drawString(text, x, y, self.font, self.fill)


def _dot(canvas: skia.Canvas, x: float, y: float, r: float, color: int,
         outline_color: int, outline: float) -> None:
    canvas.drawCircle(x, y, r + outline, skia.Paint(AntiAlias=True, Color=outline_color))
    canvas.drawCircle(x, y, r, skia.Paint(AntiAlias=True, Color=color))


# ---------------------------------------------------------------- speed gauge

class SpeedGauge:
    """縦型の速度ゲージ (0 〜 max_speed km/h)."""

    def __init__(self, cfg: RenderConfig, typeface: skia.Typeface):
        u = cfg.unit
        self.cfg = cfg
        self.x = 185 * u
        self.top = 300 * u
        self.bottom = 690 * u
        self.major_len = 30 * u
        self.minor_len = 16 * u
        self.dot_r = 13 * u
        self.label = TextPainter(cfg, typeface, 22 * u, 5 * u)
        self.line = skia.Paint(AntiAlias=True, Color=cfg.text_color,
                               Style=skia.Paint.kStroke_Style, StrokeWidth=3 * u,
                               StrokeCap=skia.Paint.kRound_Cap)
        self.line_outline = skia.Paint(AntiAlias=True, Color=cfg.outline_color,
                                       Style=skia.Paint.kStroke_Style, StrokeWidth=7 * u,
                                       StrokeCap=skia.Paint.kRound_Cap)
        self.static = self._build_static()

    def _y(self, v: float) -> float:
        v = min(max(v, 0.0), self.cfg.max_speed)
        return self.bottom - (v / self.cfg.max_speed) * (self.bottom - self.top)

    def _build_static(self) -> skia.Picture:
        rec = skia.PictureRecorder()
        canvas = rec.beginRecording(skia.Rect(0, 0, self.cfg.width, self.cfg.height))
        max_v = self.cfg.max_speed
        segments = []
        segments.append((self.x, self.top, self.x, self.bottom))
        v = 0.0
        while v <= max_v + 1e-6:
            major = abs(v / 10 - round(v / 10)) < 1e-6
            half = (self.major_len if major else self.minor_len) / 2
            y = self._y(v)
            segments.append((self.x - half, y, self.x + half, y))
            v += 2.0
        for paint in (self.line_outline, self.line):
            for x0, y0, x1, y1 in segments:
                canvas.drawLine(x0, y0, x1, y1, paint)
        v = 0.0
        while v <= max_v + 1e-6:
            y = self._y(v)
            self.label.draw(canvas, f"{v:.1f} km/h", self.x - self.major_len / 2 - 10 * self.cfg.unit,
                            y + self.label.size * 0.36, align="right")
            v += 10.0
        return rec.finishRecordingAsPicture()

    def draw(self, canvas: skia.Canvas, speed: float) -> None:
        canvas.drawPicture(self.static)
        _dot(canvas, self.x, self._y(speed), self.dot_r, self.cfg.dot_color,
             self.cfg.outline_color, 2 * self.cfg.unit)


# ---------------------------------------------------------------- metrics text

_UNITS = {"speed": "km/h", "hr": "bpm", "cadence": "rpm", "power": "W",
          "wkg": "W/kg", "grade": "%"}


class MetricsText:
    """数値テキスト (値は右揃え・単位は左揃えで、桁が変わっても位置がぶれない)."""

    def __init__(self, cfg: RenderConfig, typeface: skia.Typeface, fields: List[str]):
        u = cfg.unit
        self.cfg = cfg
        self.fields = fields
        self.text = TextPainter(cfg, typeface, 50 * u, 9 * u)
        self.value_right = 400 * u
        self.unit_left = 414 * u
        self.first_baseline = 405 * u
        self.line_height = 72 * u

    def format(self, field: str, tel: Telemetry, ts: float) -> str:
        if field == "wkg":
            p = tel.held("power", ts)
            v = p / self.cfg.weight if self.cfg.weight else float("nan")
        else:
            v = tel.held(field, ts)
        if not math.isfinite(v):
            return "--"
        if field in ("speed", "wkg"):
            return f"{v:.1f}"
        if field == "grade":
            v = round(v, 1)
            return f"{0.0 if v == 0 else v:.1f}"
        return f"{v:.0f}"

    def draw(self, canvas: skia.Canvas, tel: Telemetry, ts: float) -> None:
        for i, field in enumerate(self.fields):
            y = self.first_baseline + i * self.line_height
            self.text.draw(canvas, self.format(field, tel, ts), self.value_right, y, align="right")
            self.text.draw(canvas, _UNITS[field], self.unit_left, y)


# ---------------------------------------------------------------- course map

class CourseMap:
    """コース全体の線と現在地のドット (北が上)."""

    def __init__(self, cfg: RenderConfig, tel: Telemetry):
        u = cfg.unit
        self.cfg = cfg
        self.tel = tel
        if cfg.map_scope == "segment":
            sl = tel.index_range(cfg.start_s, cfg.end_s)
        else:
            sl = slice(None)
        lat, lon = tel.lat[sl], tel.lon[sl]
        self.lat0 = float(np.mean(lat))
        self.kx = math.cos(math.radians(self.lat0))
        # コース線は GPS のブレで細かくギザつくため軽く平滑化する (ドットの位置は生値)
        lat_s = moving_average(lat, 5) if len(lat) > 10 else lat
        lon_s = moving_average(lon, 5) if len(lon) > 10 else lon
        x, y = self._project(lon_s, lat_s)

        box_w, box_h = cfg.map_width * u, cfg.map_height * u
        right = cfg.width - 60 * u
        top = 30 * u
        self.line_w = 10 * u
        pad = self.line_w
        span_x = max(float(x.max() - x.min()), 1e-12)
        span_y = max(float(y.max() - y.min()), 1e-12)
        self.k = min((box_w - 2 * pad) / span_x, (box_h - 2 * pad) / span_y)
        draw_w, draw_h = span_x * self.k, span_y * self.k
        # 右上に寄せる
        self.ox = right - pad - draw_w - float(x.min()) * self.k
        self.oy = top + pad + float(y.max()) * self.k

        self.static = self._build_static(x, y)

    def _project(self, lon, lat):
        return np.asarray(lon) * self.kx, np.asarray(lat)

    def _to_px(self, x, y):
        return self.ox + x * self.k, self.oy - y * self.k

    def _build_static(self, x: np.ndarray, y: np.ndarray) -> skia.Picture:
        px, py = self._to_px(x, y)
        path = skia.Path()
        path.moveTo(float(px[0]), float(py[0]))
        lx, ly = px[0], py[0]
        for xi, yi in zip(px[1:], py[1:]):
            if (xi - lx) ** 2 + (yi - ly) ** 2 >= 1.0:  # 1px 未満の点は間引く
                path.lineTo(float(xi), float(yi))
                lx, ly = xi, yi
        rec = skia.PictureRecorder()
        canvas = rec.beginRecording(skia.Rect(0, 0, self.cfg.width, self.cfg.height))
        u = self.cfg.unit
        outline = skia.Paint(AntiAlias=True, Color=(self.cfg.outline_color & 0x00FFFFFF) | 0x60000000,
                             Style=skia.Paint.kStroke_Style, StrokeWidth=self.line_w + 4 * u,
                             StrokeJoin=skia.Paint.kRound_Join, StrokeCap=skia.Paint.kRound_Cap)
        line = skia.Paint(AntiAlias=True, Color=self.cfg.map_color, Style=skia.Paint.kStroke_Style,
                          StrokeWidth=self.line_w, StrokeJoin=skia.Paint.kRound_Join,
                          StrokeCap=skia.Paint.kRound_Cap)
        canvas.drawPath(path, outline)
        canvas.drawPath(path, line)
        return rec.finishRecordingAsPicture()

    def draw(self, canvas: skia.Canvas, ts: float) -> None:
        canvas.drawPicture(self.static)
        x, y = self._project(self.tel.interp("lon", ts), self.tel.interp("lat", ts))
        px, py = self._to_px(float(x), float(y))
        _dot(canvas, px, py, 14 * self.cfg.unit, self.cfg.dot_color,
             self.cfg.outline_color, 3 * self.cfg.unit)
