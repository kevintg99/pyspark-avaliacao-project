"""Wrappers de ffprobe/ffmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .log import get_logger

log = get_logger("ffmpeg")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}


class FFmpegError(RuntimeError):
    pass


@dataclass(slots=True)
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    is_image: bool
    has_video: bool


def require_ffmpeg() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise FFmpegError(f"binários não encontrados no PATH: {', '.join(missing)}")


def run(cmd: list[str], timeout: float | None = None, stage: str = "ffmpeg") -> None:
    log.debug("executando", extra={"stage": stage, "cmd": " ".join(cmd)})
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"timeout após {timeout}s: {' '.join(cmd[:6])}…") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise FFmpegError(f"ffmpeg falhou (código {proc.returncode}):\n{tail}")


def probe(path: Path | str) -> MediaInfo:
    """Valida o arquivo com ffprobe. Levanta FFmpegError se estiver corrompido."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise FFmpegError(f"arquivo inexistente ou vazio: {path}")
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_entries", "format=duration:stream=codec_type,width,height,duration,nb_frames",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe rejeitou {path.name}: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    is_image = path.suffix.lower() in IMAGE_EXTS
    duration = 0.0
    for source in (data.get("format", {}).get("duration"), (video or {}).get("duration")):
        try:
            duration = max(duration, float(source))
        except (TypeError, ValueError):
            pass
    if video is None or not video.get("width"):
        raise FFmpegError(f"sem stream de vídeo/imagem válido: {path.name}")
    if not is_image and duration < 0.5:
        raise FFmpegError(f"vídeo curto demais ou corrompido ({duration:.2f}s): {path.name}")
    return MediaInfo(path, duration, int(video["width"]), int(video["height"]), is_image, True)


def audio_duration(path: Path | str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise FFmpegError(f"não foi possível ler a duração do áudio {path}: {proc.stderr[:200]}") from exc


def extract_frame(src: Path, dest: Path, at: float, width: int = 480) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1",
         "-vf", f"scale={width}:-2", str(dest)], timeout=60, stage="thumb")
    return dest


def supported_xfade_transitions() -> set[str]:
    """Lê as transições do xfade suportadas pelo ffmpeg instalado."""
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    names: set[str] = set()
    in_enum = False
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("transition"):
            in_enum = True
            continue
        if in_enum:
            parts = stripped.split()
            if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
                names.add(parts[0])
            elif parts and not parts[0].isidentifier():
                continue
            else:
                break
    names.discard("custom")
    return names
