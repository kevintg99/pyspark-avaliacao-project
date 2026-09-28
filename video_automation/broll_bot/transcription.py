"""Transcrição com timestamps por palavra e alinhamento com o roteiro.

A transcrição serve só para sincronizar os cortes com a fala — nunca vira
legenda. Se o faster-whisper não estiver instalado (ou falhar), o tempo de
cada frase é estimado proporcionalmente ao número de caracteres.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from .cache import SQLiteCache, make_key
from .config import TranscriptionConfig
from .log import get_logger
from .models import Sentence, Word
from .text import normalize, tokens

log = get_logger("transcription")

_MD_NOISE = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M), ""),
    (re.compile(r"^[ \t]*(?:[-*+>]|\d+[.)])[ \t]+", re.M), ""),
    (re.compile(r"[*_`~]+"), ""),
    (re.compile(r"\[(?:pausa|pause|b-?roll|musica|música)[^\]]*\]", re.I), " "),
]
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\"”')\]]*\s+(?=[\"“'(\[]?[A-ZÀ-Ý0-9])")


def parse_script(text: str) -> list[Sentence]:
    """Limpa markdown e divide o roteiro em frases, preservando parágrafos."""
    for pattern, repl in _MD_NOISE:
        text = pattern.sub(repl, text)
    sentences: list[Sentence] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for p_idx, paragraph in enumerate(paragraphs):
        flat = re.sub(r"\s+", " ", paragraph)
        for piece in _SENTENCE_SPLIT.split(flat):
            piece = piece.strip()
            if tokens(piece):
                sentences.append(Sentence(index=len(sentences), text=piece, paragraph=p_idx))
    return sentences


def transcribe(audio: Path, cfg: TranscriptionConfig, cache: SQLiteCache | None = None) -> list[Word]:
    if not cfg.enabled:
        return []
    stat = audio.stat()
    key = make_key("whisper", str(audio.resolve()), stat.st_size, stat.st_mtime, cfg.model, cfg.language)
    if cache is not None and (hit := cache.get("transcription", key)) is not None:
        log.info("transcrição reaproveitada do cache", extra={"stage": "transcription", "words": len(hit)})
        return [Word(**w) for w in hit]
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError:
        log.warning("faster-whisper não instalado; usando estimativa proporcional", extra={"stage": "transcription"})
        return []
    try:
        model = WhisperModel(cfg.model, device=cfg.device, compute_type=cfg.compute_type)
        segments, _info = model.transcribe(str(audio), language=cfg.language, word_timestamps=True, vad_filter=True)
        words = [Word(w.word.strip(), float(w.start), float(w.end)) for seg in segments for w in (seg.words or []) if w.word.strip()]
    except Exception as exc:  # noqa: BLE001 - qualquer falha cai no plano B
        log.warning("transcrição falhou; usando estimativa proporcional", extra={"stage": "transcription", "error": str(exc)})
        return []
    if cache is not None:
        cache.set("transcription", key, [{"text": w.text, "start": w.start, "end": w.end} for w in words], ttl_seconds=90 * 86400)
    log.info("transcrição concluída", extra={"stage": "transcription", "words": len(words)})
    return words


def align(sentences: list[Sentence], words: list[Word], audio_duration: float) -> list[Sentence]:
    """Define start/end de cada frase casando os tokens do roteiro com os da fala."""
    if not sentences:
        return sentences
    if not words:
        return _proportional(sentences, audio_duration)

    script_tokens: list[tuple[int, str]] = [(s.index, t) for s in sentences for t in tokens(s.text)]
    word_tokens: list[tuple[int, str]] = []
    for w_idx, word in enumerate(words):
        for tok in tokens(word.text):
            word_tokens.append((w_idx, tok))

    matcher = SequenceMatcher(None, [t for _, t in script_tokens], [t for _, t in word_tokens], autojunk=False)
    token_time: dict[int, float] = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            token_time[block.a + k] = words[word_tokens[block.b + k][0]].start

    first_token: dict[int, int] = {}
    for pos, (s_idx, _) in enumerate(script_tokens):
        first_token.setdefault(s_idx, pos)

    starts: list[float | None] = []
    for sentence in sentences:
        pos = first_token.get(sentence.index)
        # usa o primeiro token casado das 4 primeiras palavras da frase
        t = None
        if pos is not None:
            for off in range(4):
                idx = pos + off
                if idx < len(script_tokens) and script_tokens[idx][0] == sentence.index and idx in token_time:
                    # estimativa: ~0,25s por palavra não reconhecida no início da frase
                    t = token_time[idx] - 0.25 * off
                    break
        starts.append(t)

    matched = sum(1 for s in starts if s is not None)
    if matched < max(1, len(sentences) // 3):
        log.warning("alinhamento fraco; usando estimativa proporcional",
                    extra={"stage": "alignment", "matched": matched, "sentences": len(sentences)})
        return _proportional(sentences, audio_duration)

    filled = _interpolate(starts, sentences, audio_duration)
    filled[0] = 0.0
    for i in range(1, len(filled)):  # garante monotonicidade
        filled[i] = max(filled[i], filled[i - 1] + 0.05)
    for i, sentence in enumerate(sentences):
        sentence.start = round(filled[i], 3)
        sentence.end = round(filled[i + 1] if i + 1 < len(filled) else audio_duration, 3)
    log.info("roteiro alinhado à fala", extra={"stage": "alignment", "matched": matched, "sentences": len(sentences)})
    return sentences


def _interpolate(starts: list[float | None], sentences: list[Sentence], total: float) -> list[float]:
    weights = [max(1, len(s.text)) for s in sentences]
    out: list[float] = [0.0] * len(starts)
    known = [(i, t) for i, t in enumerate(starts) if t is not None]
    anchors = [(0, 0.0), *known, (len(starts), total)]
    for (i0, t0), (i1, t1) in zip(anchors, anchors[1:]):
        if i1 <= i0:
            continue
        span_w = sum(weights[i0:i1]) or 1
        acc = 0
        for i in range(i0, i1):
            out[i] = t0 + (t1 - t0) * acc / span_w if starts[i] is None else float(starts[i])  # type: ignore[arg-type]
            acc += weights[i]
    return out


def _proportional(sentences: list[Sentence], total: float) -> list[Sentence]:
    weights = [max(1, len(normalize(s.text))) for s in sentences]
    span = sum(weights)
    cursor = 0.0
    for sentence, w in zip(sentences, weights):
        sentence.start = round(cursor, 3)
        cursor += total * w / span
        sentence.end = round(cursor, 3)
    sentences[-1].end = round(total, 3)
    return sentences
