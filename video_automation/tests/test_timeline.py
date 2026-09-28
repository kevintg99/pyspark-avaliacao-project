import random

import pytest

from broll_bot.config import TimelineConfig
from broll_bot.models import Scene, SceneAnalysis, Word
from broll_bot.timeline import is_round_ms, organic, plan_timeline, word_boundaries

A = SceneAnalysis("x", ["x"], "x")
FPS = 30


def scenes_for(bounds):
    return [Scene(i, "", s, e, A) for i, (s, e) in enumerate(zip(bounds, bounds[1:]))]


@pytest.fixture(params=[1, 7, 42, 1234])
def plan(request):
    cfg = TimelineConfig()
    bounds = [0.0, 9.3, 31.72, 33.1, 47.0, 88.456, 121.37]
    shots = plan_timeline(scenes_for(bounds), [], bounds[-1], cfg, FPS, random.Random(request.param),
                          cfg.transitions, image_ratio=0.25)
    return cfg, bounds[-1], shots


def test_no_clip_exceeds_seven_seconds(plan):
    cfg, _, shots = plan
    assert all(s.clip_duration <= 7.0 for s in shots)
    assert all(s.frames <= 7.0 * FPS for s in shots)


def test_durations_have_broken_milliseconds_and_do_not_repeat(plan):
    _, _, shots = plan
    ms = [round(s.clip_duration * 1000) for s in shots]
    assert not any(is_round_ms(m) for m in ms), ms
    assert len(ms) == len(set(ms))


def test_timeline_covers_whole_audio_without_gaps(plan):
    _, total, shots = plan
    assert shots[0].start_frame == 0
    assert abs(shots[-1].end_frame / FPS - total) < 0.05
    for prev, cur in zip(shots, shots[1:]):
        overlap = prev.end_frame - cur.start_frame
        if cur.transition_in.is_cut:
            assert overlap == 0
        else:
            assert overlap > 0
            assert cur.frames > overlap and prev.frames > overlap


def test_transitions_vary(plan):
    cfg, _, shots = plan
    kinds = [s.transition_in.kind for s in shots if not s.transition_in.is_cut]
    assert len(set(kinds)) >= 3
    for a, b in zip(kinds, kinds[1:]):
        assert a != b
    for s in shots:
        if not s.transition_in.is_cut:
            assert cfg.transition_min - 0.02 <= s.transition_in.duration <= cfg.transition_max + 0.02


def test_long_scene_gets_several_shots(plan):
    _, _, shots = plan
    per_scene = {}
    for s in shots:
        per_scene[s.scene_index] = per_scene.get(s.scene_index, 0) + 1
    assert per_scene[4] >= 6  # cena de ~41s


def test_same_seed_same_plan():
    cfg = TimelineConfig()
    sc = scenes_for([0, 20, 45.5])
    a = plan_timeline(sc, [], 45.5, cfg, FPS, random.Random(5), cfg.transitions)
    b = plan_timeline(sc, [], 45.5, cfg, FPS, random.Random(5), cfg.transitions)
    assert [(s.start_frame, s.end_frame, s.transition_in.kind) for s in a] == \
           [(s.start_frame, s.end_frame, s.transition_in.kind) for s in b]


def _speech(n_words: int, pause_every: int):
    words, t = [], 0.0
    for i in range(n_words):  # palavras de 0.35s, pausa longa a cada N palavras
        words.append(Word(f"w{i}", t, t + 0.35))
        t += 0.35 + (0.5 if i % pause_every == pause_every - 1 else 0.05)
    return words, t


def test_cuts_never_fall_inside_a_word():
    cfg = TimelineConfig(hard_cut_probability_in_scene=1.0, hard_cut_probability_scene_change=1.0)
    words, total = _speech(120, pause_every=9)
    shots = plan_timeline(scenes_for([0.0, total]), words, total, cfg, FPS, random.Random(3), cfg.transitions)
    for s in shots[1:]:
        # tolerância de alguns ms pelo ajuste "orgânico" das durações
        assert not any(w.start + 0.02 < s.cut_in < w.end - 0.02 for w in words), s.cut_in


def test_cuts_prefer_speech_pauses():
    cfg = TimelineConfig(hard_cut_probability_in_scene=1.0, hard_cut_probability_scene_change=1.0)
    words, total = _speech(120, pause_every=2)
    pauses = [(a.end, b.start) for a, b in zip(words, words[1:]) if b.start - a.end > 0.3]
    shots = plan_timeline(scenes_for([0.0, total]), words, total, cfg, FPS, random.Random(3), cfg.transitions)
    inner = [s.cut_in for s in shots[1:]]
    on_pause = sum(any(lo - 0.02 <= c <= hi + 0.02 for lo, hi in pauses) for c in inner)
    assert on_pause >= 0.8 * len(inner)
    assert word_boundaries(words)


def test_organic_avoids_round_values():
    rng = random.Random(0)
    used: set[int] = set()
    for value in (3.0, 4.5, 5.25, 6.1, 3.0):
        out = organic(value, rng, used)
        ms = round(out * 1000)
        assert not is_round_ms(ms) and ms not in used
        assert abs(out - value) < 0.05
        used.add(ms)


def test_is_round_ms():
    assert is_round_ms(3000) and is_round_ms(3001) and is_round_ms(4498) and is_round_ms(5130)
    assert not is_round_ms(3487) and not is_round_ms(5132) and not is_round_ms(6874)
