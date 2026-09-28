"""Montagem com FFmpeg: normaliza cada shot, aplica transições (xfade) e mixa a narração."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .framing import build_filter
from .log import get_logger
from .media import FFmpegError, run
from .models import MediaType, Shot

log = get_logger("assembler")


@dataclass(slots=True)
class SeqItem:
    path: Path
    start_frame: int
    end_frame: int
    transition: str  # tipo do xfade de entrada ou "cut"

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


class Assembler:
    def __init__(self, cfg: AppConfig, work_dir: Path) -> None:
        self.cfg = cfg
        self.out = cfg.output
        self.work_dir = work_dir
        work_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- shots
    def _encode_args(self, final: bool) -> list[str]:
        r = self.cfg.render
        crf, preset = (self.out.crf, self.out.preset) if final else (r.intermediate_crf, r.intermediate_preset)
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(self.out.fps)]

    def render_shot(self, shot: Shot, dest: Path) -> Path:
        if shot.media_path is None or shot.candidate is None:
            return self.render_generated(shot, dest)
        w, h, fps = self.out.width, self.out.height, self.out.fps
        is_image = shot.candidate.media_type is MediaType.IMAGE
        filt = build_filter(shot.layout, w, h, fps, shot.frames, speed=shot.speed, src_aspect=shot.candidate.aspect)
        cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
        if is_image:
            cmd += ["-loop", "1", "-framerate", str(fps), "-i", shot.media_path]
        else:
            needed = shot.frames / fps / max(shot.speed, 1e-3)
            src_dur = shot.candidate.duration or 0.0
            if src_dur and shot.source_offset + needed > src_dur + 0.05:
                cmd += ["-stream_loop", "-1"]
            cmd += ["-ss", f"{shot.source_offset:.3f}", "-i", shot.media_path]
        cmd += ["-filter_complex", filt, "-map", "[v]", "-frames:v", str(shot.frames), "-an",
                *self._encode_args(final=False), str(dest)]
        run(cmd, timeout=600, stage="render_shot")
        return dest

    def render_generated(self, shot: Shot, dest: Path) -> Path:
        """Último recurso: fundo em degradê animado e discreto (nunca derruba o vídeo)."""
        w, h, fps = self.out.width, self.out.height, self.out.fps
        dur = shot.frames / fps + 0.5
        sources = [
            f"gradients=s={w}x{h}:c0=0x0f1720:c1=0x1f2f3f:c2=0x101820:speed=0.004:r={fps}:d={dur:.3f}",
            f"color=c=0x141a22:s={w}x{h}:r={fps}:d={dur:.3f}",
        ]
        last_error: Exception | None = None
        for src in sources:
            try:
                run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "lavfi", "-i", src,
                     "-vf", "format=yuv420p", "-frames:v", str(shot.frames), "-an",
                     *self._encode_args(final=False), str(dest)], timeout=300, stage="render_shot")
                return dest
            except FFmpegError as exc:
                last_error = exc
        raise FFmpegError(f"não foi possível gerar fundo substituto: {last_error}")

    # ---------------------------------------------------------- sequence
    def render_sequence(self, items: list[SeqItem], dest: Path, audio: Path | None = None,
                        total_seconds: float | None = None) -> Path:
        """Encadeia os itens com xfade (ou concat nos cortes secos).

        ``start_frame``/``end_frame`` são posições absolutas na linha do tempo,
        então offset e duração de cada transição saem direto delas.
        """
        if not items:
            raise ValueError("sequência vazia")
        fps = self.out.fps
        final = audio is not None
        base = items[0].start_frame
        inputs: list[str] = []
        graph: list[str] = []
        for i, item in enumerate(items):
            inputs += ["-i", str(item.path)]
            # mesma base de tempo em tudo: xfade exige que as duas entradas coincidam
            graph.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={fps},format=yuv420p,"
                         f"trim=end_frame={item.frames},setpts=PTS-STARTPTS,settb=AVTB[s{i}]")
        acc = "s0"
        acc_end = items[0].end_frame - base
        for i, item in enumerate(items[1:], start=1):
            start_rel = item.start_frame - base
            overlap = acc_end - start_rel
            label = f"x{i}"
            if overlap > 0:
                kind = item.transition if item.transition != "cut" else "fade"
                graph.append(f"[{acc}][s{i}]xfade=transition={kind}:duration={overlap / fps:.6f}"
                             f":offset={start_rel / fps:.6f}[{label}]")
            else:
                graph.append(f"[{acc}][s{i}]concat=n=2:v=1:a=0[{label}]")
            acc, acc_end = label, item.end_frame - base

        cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", *inputs]
        if final:
            graph.append(f"[{acc}]tpad=stop_mode=clone:stop_duration=2,format=yuv420p[vout]")
            cmd += ["-i", str(audio), "-filter_complex", ";".join(graph),
                    "-map", "[vout]", "-map", f"{len(items)}:a:0",
                    *self._encode_args(final=True),
                    "-c:a", "aac", "-b:a", self.out.audio_bitrate,
                    "-t", f"{total_seconds:.3f}", "-movflags", "+faststart", str(dest)]
        else:
            cmd += ["-filter_complex", ";".join(graph), "-map", f"[{acc}]", "-an",
                    "-frames:v", str(acc_end), *self._encode_args(final=False), str(dest)]
        run(cmd, timeout=3600, stage="render_sequence")
        return dest

    def assemble(self, shots: list[Shot], clip_paths: list[Path], audio: Path, dest: Path, audio_seconds: float) -> Path:
        """Renderiza em blocos (grafos menores) e junta tudo no passo final com o áudio."""
        items = [SeqItem(p, s.start_frame, s.end_frame, s.transition_in.kind) for s, p in zip(shots, clip_paths)]
        size = max(2, self.cfg.render.chunk_size)
        if len(items) <= size:
            return self.render_sequence(items, dest, audio=audio, total_seconds=audio_seconds)
        chunks: list[SeqItem] = []
        for n, start in enumerate(range(0, len(items), size)):
            block = items[start:start + size]
            path = self.work_dir / f"chunk_{n:03d}.mp4"
            log.info("renderizando bloco", extra={"stage": "render", "chunk": n, "shots": len(block)})
            self.render_sequence(block, path)
            chunks.append(SeqItem(path, block[0].start_frame, block[-1].end_frame, block[0].transition))
        log.info("juntando blocos + narração", extra={"stage": "render", "chunks": len(chunks)})
        return self.render_sequence(chunks, dest, audio=audio, total_seconds=audio_seconds)
