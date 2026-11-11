"""動画全体の書き出し (チャンク分割 + マルチプロセス)."""

from __future__ import annotations

import os
import shutil
import tempfile
from concurrent.futures import FIRST_EXCEPTION, ProcessPoolExecutor, wait
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import encoder
from .config import RenderConfig
from .processing import Telemetry
from .renderer import Renderer

ProgressFn = Callable[[int], None]

# ------------------------------------------------------------- worker side

_WORKER: dict = {}


def _init_worker(cfg: RenderConfig, tel: Telemetry, fmt: str, ffmpeg: str,
                 threads: Optional[int]) -> None:
    _WORKER.update(renderer=Renderer(cfg, tel), cfg=cfg, fmt=fmt, ffmpeg=ffmpeg,
                   threads=threads)


def _render_frames(renderer: Renderer, cfg: RenderConfig, fmt: str, ffmpeg: str,
                   f0: int, f1: int, out: Path, threads: Optional[int],
                   progress: Optional[ProgressFn] = None) -> int:
    if fmt == "png":
        for n in range(f0, f1):
            renderer.save_png(cfg.frame_time(n), str(out / f"frame_{n:06d}.png"))
            if progress:
                progress(1)
        return f1 - f0
    proc = encoder.start_encoder(ffmpeg, fmt, cfg.width, cfg.height, cfg.fps, out, threads)
    assert proc.stdin is not None
    try:
        for n in range(f0, f1):
            proc.stdin.write(renderer.render_rgba(cfg.frame_time(n)).data)
            if progress:
                progress(1)
    except BrokenPipeError:
        pass  # ffmpeg 側のエラーは finish_encoder で報告
    encoder.finish_encoder(proc)
    return f1 - f0


def _chunk_task(args: Tuple[int, int, str]) -> int:
    f0, f1, out = args
    w = _WORKER
    return _render_frames(w["renderer"], w["cfg"], w["fmt"], w["ffmpeg"], f0, f1,
                          Path(out), w["threads"])


# ------------------------------------------------------------- driver side

def default_jobs() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def render_video(cfg: RenderConfig, tel: Telemetry, fmt: str, out: Path, jobs: int,
                 chunk_seconds: float = 10.0,
                 progress: Optional[ProgressFn] = None) -> None:
    ffmpeg = encoder.find_ffmpeg() if fmt != "png" else ""
    total = cfg.frame_count
    if total <= 0:
        raise ValueError("書き出すフレームがありません (開始・終了時刻を確認してください)")
    if fmt == "png":
        out.mkdir(parents=True, exist_ok=True)

    chunk = max(1, int(chunk_seconds * cfg.fps))
    ranges = [(f, min(f + chunk, total)) for f in range(0, total, chunk)]

    if jobs <= 1 or len(ranges) == 1:
        renderer = Renderer(cfg, tel)
        _render_frames(renderer, cfg, fmt, ffmpeg, 0, total, out, None, progress)
        return

    if fmt == "png":
        tasks = [(f0, f1, str(out)) for f0, f1 in ranges]
        _run_pool(cfg, tel, fmt, ffmpeg, jobs, tasks, progress)
        return

    tmp = Path(tempfile.mkdtemp(prefix=".ridelay_", dir=str(out.parent)))
    try:
        ext = out.suffix
        segs: List[Path] = [tmp / f"seg_{i:05d}{ext}" for i in range(len(ranges))]
        tasks = [(f0, f1, str(seg)) for (f0, f1), seg in zip(ranges, segs)]
        _run_pool(cfg, tel, fmt, ffmpeg, jobs, tasks, progress)
        encoder.concat(ffmpeg, segs, out, tmp / "list.txt")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_pool(cfg: RenderConfig, tel: Telemetry, fmt: str, ffmpeg: str, jobs: int,
              tasks: List[Tuple[int, int, str]], progress: Optional[ProgressFn]) -> None:
    # ffmpeg 自体もマルチスレッドなので、並列時はスレッド数を絞って過剰な競合を避ける
    threads = max(1, (os.cpu_count() or 2) // jobs)
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker,
                             initargs=(cfg, tel, fmt, ffmpeg, threads)) as pool:
        futures = {pool.submit(_chunk_task, t): t for t in tasks}
        pending = set(futures)
        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_EXCEPTION)
                for fut in done:
                    n = fut.result()  # 例外があればここで送出
                    if progress:
                        progress(n)
        except BaseException:
            for fut in pending:
                fut.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
