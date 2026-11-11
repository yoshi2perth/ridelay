"""FFmpeg によるアルファ付き動画のエンコード."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import List, Optional

FORMATS = ("prores", "webm", "hevc", "png")
EXTENSIONS = {"prores": ".mov", "webm": ".webm", "hevc": ".mov", "png": ""}


class EncoderError(RuntimeError):
    pass


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        raise EncoderError("ffmpeg が見つかりません (brew install ffmpeg)") from e


def _fps_str(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


def codec_args(fmt: str, threads: Optional[int] = None) -> List[str]:
    bt709 = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    t = ["-threads", str(threads)] if threads else []
    if fmt == "prores":
        # ProRes 4444 + 16bit アルファ。Final Cut Pro で透過として扱われる
        return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                "-alpha_bits", "16", "-vendor", "apl0", *t, *bt709]
    if fmt == "webm":
        return ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30",
                "-row-mt", "1", "-auto-alt-ref", "0", *t, *bt709]
    if fmt == "hevc":
        # Apple VideoToolbox の HEVC with Alpha (macOS のみ)
        return ["-c:v", "hevc_videotoolbox", "-alpha_quality", "0.75", "-q:v", "65",
                "-tag:v", "hvc1", "-pix_fmt", "bgra", *t, *bt709]
    raise EncoderError(f"未対応の形式: {fmt}")


def start_encoder(ffmpeg: str, fmt: str, width: int, height: int, fps: Fraction,
                  out_path: Path, threads: Optional[int] = None) -> subprocess.Popen:
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
        "-r", _fps_str(fps), "-i", "-",
        *codec_args(fmt, threads),
        "-r", _fps_str(fps), str(out_path),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


def finish_encoder(proc: subprocess.Popen) -> None:
    assert proc.stdin is not None
    proc.stdin.close()
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise EncoderError(f"ffmpeg がエラー終了しました:\n{err}")


def concat(ffmpeg: str, segments: List[Path], out_path: Path, list_file: Path) -> None:
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
           "-i", str(list_file), "-c", "copy", str(out_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise EncoderError(f"セグメントの結合に失敗しました:\n{res.stderr}")


@dataclass
class ProbeResult:
    stream_line: str
    has_alpha: bool
    duration: Optional[str]


def probe(ffmpeg: str, path: Path) -> ProbeResult:
    """ffprobe が無い環境でも動くよう ffmpeg -i の出力を解析する."""
    res = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    text = res.stderr
    stream = next((ln.strip() for ln in text.splitlines() if "Video:" in ln), "")
    dur = re.search(r"Duration: ([\d:.]+)", text)
    has_alpha = bool(re.search(r"yuva|rgba|bgra|argb", stream)) or "alpha_mode" in text
    return ProbeResult(stream, has_alpha, dur.group(1) if dur else None)
