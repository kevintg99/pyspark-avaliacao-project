"""Planejamento da linha do tempo: cortes, durações e transições.

Regras principais:

* Cada clipe renderizado (incluindo a sobreposição das transições) tem no
  máximo ``max_clip_seconds`` (7s) e duração com milissegundos "quebrados"
  (nunca 3.000 ou 4.500), sem repetir valores — o ritmo não parece template.
* Os cortes internos de uma cena caem, quando possível, nas pausas da fala.
* Os pontos de troca são convertidos em frames a partir de posições
  acumuladas, então não há deriva de sincronia por arredondamento.
"""

from __future__ import annotations

import bisect
import random

from .config import TimelineConfig
from .models import MediaType, Scene, Shot, Transition, Word

ORGANIC_NUDGES = (-7, -3, -1, 1, 3, 7, -9, 9, -13, 13)


def is_round_ms(ms: int) -> bool:
    """Redondo = múltiplo de 10 ms ou colado num décimo de segundo (3.001, 4.498)."""
    return ms % 10 == 0 or min(ms % 100, 100 - ms % 100) < 5


def organic(value: float, rng: random.Random, used: set[int] | None = None) -> float:
    """Arredonda para ms e desloca alguns ms se ficar "redondo" ou repetido."""
    ms = round(value * 1000)
    for _ in range(64):
        if not is_round_ms(ms) and (used is None or ms not in used):
            break
        ms += rng.choice(ORGANIC_NUDGES)
    return ms / 1000


def word_boundaries(words: list[Word]) -> list[tuple[float, float]]:
    """Instantes bons para cortar (meio do intervalo entre palavras) e o tamanho da pausa."""
    out = []
    for a, b in zip(words, words[1:]):
        gap = max(0.0, b.start - a.end)
        out.append(((a.end + b.start) / 2 if gap else b.start, gap))
    return out


def _snap(t: float, boundaries: list[tuple[float, float]], tolerance: float, lo: float, hi: float) -> float:
    """Leva o corte para a maior pausa dentro da tolerância (desempate: a mais próxima).

    Sem pausa perto, usa ao menos uma fronteira entre palavras (nunca corta no meio
    de uma palavra)."""
    if not boundaries:
        return t
    times = [b[0] for b in boundaries]
    i = bisect.bisect_left(times, t - tolerance)
    best: tuple[float, float] | None = None
    while i < len(boundaries) and times[i] <= t + tolerance:
        bt, gap = boundaries[i]
        if lo <= bt <= hi:
            key = (round(gap, 2), -abs(bt - t))
            if best is None or key > best[1]:
                best = (bt, key)  # type: ignore[assignment]
        i += 1
    return best[0] if best else t


def split_scene(start: float, end: float, gmin: float, gmax: float, rng: random.Random,
                boundaries: list[tuple[float, float]], tolerance: float) -> list[float]:
    """Devolve os pontos de corte internos de uma cena."""
    cuts: list[float] = []
    cursor = start
    while end - cursor > gmax:
        remaining = end - cursor
        g = rng.uniform(gmin, gmax)
        if remaining - g < gmin:
            g = remaining / 2
        lo = cursor + gmin
        hi = min(cursor + gmax, end - gmin)
        t = _snap(cursor + g, boundaries, tolerance, lo, hi)
        cuts.append(t)
        cursor = t
    return cuts


def _pick_transition(cfg: TimelineConfig, available: list[str], prev_kind: str | None, scene_change: bool,
                     max_dur: float, rng: random.Random) -> Transition:
    p_cut = cfg.hard_cut_probability_scene_change if scene_change else cfg.hard_cut_probability_in_scene
    if not available or max_dur < 0.12 or rng.random() < p_cut:
        return Transition("cut", 0.0)
    options = [k for k in available if k != prev_kind] or available
    dur = min(rng.uniform(cfg.transition_min, cfg.transition_max), max_dur)
    return Transition(rng.choice(options), organic(dur, rng))


def plan_timeline(
    scenes: list[Scene],
    words: list[Word],
    audio_duration: float,
    cfg: TimelineConfig,
    fps: int,
    rng: random.Random,
    transitions: list[str],
    image_ratio: float = 0.0,
) -> list[Shot]:
    gmax = cfg.max_clip_seconds - cfg.transition_max - 0.03
    gmin = cfg.min_clip_seconds
    boundaries = word_boundaries(words) if cfg.snap_to_words else []

    # 1) pontos de troca
    cuts: list[float] = [0.0]
    scene_of_segment: list[int] = []
    scene_change_at: set[int] = set()
    for pos, scene in enumerate(scenes):
        start = cuts[-1]
        end = audio_duration if pos == len(scenes) - 1 else max(scene.end, start + 0.1)
        if pos > 0:
            scene_change_at.add(len(cuts) - 1)
        internal = split_scene(start, end, gmin, gmax, rng, boundaries, cfg.snap_tolerance)
        for t in internal:
            cuts.append(t)
            scene_of_segment.append(scene.index)
        cuts.append(end)
        scene_of_segment.append(scene.index)
    cuts[-1] = audio_duration
    n = len(cuts) - 1

    # 2) transições (índice k = transição de entrada do shot k)
    trans: list[Transition] = [Transition("cut", 0.0)]
    prev_kind: str | None = None
    for k in range(1, n):
        g_prev = cuts[k] - cuts[k - 1]
        g_next = cuts[k + 1] - cuts[k]
        t = _pick_transition(cfg, transitions, prev_kind, k in scene_change_at, 0.45 * min(g_prev, g_next), rng)
        trans.append(t)
        if not t.is_cut:
            prev_kind = t.kind

    # 3) durações "orgânicas": ajusta o próximo ponto de troca em poucos ms
    used_ms: set[int] = set()
    durations: list[float] = []
    for i in range(n):
        t_in = trans[i].duration
        t_out = trans[i + 1].duration if i + 1 < n else 0.0
        raw = cuts[i + 1] - cuts[i] + t_in / 2 + t_out / 2
        target = organic(raw, rng, used_ms)
        cuts[i + 1] = round(cuts[i + 1] + (target - raw), 4)
        used_ms.add(round(target * 1000))
        durations.append(target)

    # 4) frames a partir de posições acumuladas (sem deriva)
    shots: list[Shot] = []
    prefer_prev_image = False
    for i in range(n):
        t_in = trans[i].duration
        t_out = trans[i + 1].duration if i + 1 < n else 0.0
        start_frame = round((cuts[i] - t_in / 2) * fps)
        end_frame = round((cuts[i + 1] + t_out / 2) * fps)
        want_image = not prefer_prev_image and rng.random() < image_ratio
        prefer_prev_image = want_image
        shots.append(Shot(
            index=i,
            scene_index=scene_of_segment[i],
            cut_in=round(cuts[i], 3),
            cut_out=round(cuts[i + 1], 3),
            clip_duration=durations[i],
            transition_in=trans[i],
            start_frame=start_frame,
            end_frame=end_frame,
            prefer_media=MediaType.IMAGE if want_image else MediaType.VIDEO,
        ))
    # transições que viraram 0 frame após quantização viram corte seco
    for prev, cur in zip(shots, shots[1:]):
        if prev.end_frame <= cur.start_frame and not cur.transition_in.is_cut:
            cur.transition_in = Transition("cut", 0.0)
            cur.start_frame = prev.end_frame
    return shots
