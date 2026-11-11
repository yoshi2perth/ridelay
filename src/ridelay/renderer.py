"""1 フレームの合成."""

from __future__ import annotations

from typing import Optional

import numpy as np
import skia

from .config import RenderConfig
from .processing import Telemetry
from .widgets import CourseMap, MetricsText, SpeedGauge, load_typeface


class Renderer:
    def __init__(self, cfg: RenderConfig, tel: Telemetry):
        self.cfg = cfg
        self.tel = tel
        typeface = load_typeface(cfg)
        self.gauge = SpeedGauge(cfg, typeface) if cfg.show_gauge else None
        self.metrics = MetricsText(cfg, typeface, cfg.fields) if cfg.fields else None
        self.map = CourseMap(cfg, tel) if cfg.show_map else None

        self.surface = skia.Surface(cfg.width, cfg.height)
        # ffmpeg / PNG にはストレートアルファ (非乗算済み) の RGBA で渡す
        self.out_info = skia.ImageInfo.Make(cfg.width, cfg.height,
                                            skia.kRGBA_8888_ColorType,
                                            skia.kUnpremul_AlphaType)
        self.buf = np.empty((cfg.height, cfg.width, 4), dtype=np.uint8)

    def draw(self, canvas: skia.Canvas, ts: float) -> None:
        canvas.clear(skia.ColorTRANSPARENT)
        if self.map:
            self.map.draw(canvas, ts)
        if self.gauge:
            self.gauge.draw(canvas, float(self.tel.interp("speed", ts)))
        if self.metrics:
            self.metrics.draw(canvas, self.tel, ts)

    def render_rgba(self, ts: float) -> np.ndarray:
        self.draw(self.surface.getCanvas(), ts)
        if not self.surface.readPixels(self.out_info, self.buf, self.cfg.width * 4):
            raise RuntimeError("フレームの読み出しに失敗しました")
        return self.buf

    def save_png(self, ts: float, path: str, background: Optional[int] = None) -> None:
        self.draw(self.surface.getCanvas(), ts)
        image = self.surface.makeImageSnapshot()
        if background is not None:
            bg = skia.Surface(self.cfg.width, self.cfg.height)
            c = bg.getCanvas()
            c.clear(background)
            c.drawImage(image, 0, 0)
            image = bg.makeImageSnapshot()
        image.save(path, skia.kPNG)
