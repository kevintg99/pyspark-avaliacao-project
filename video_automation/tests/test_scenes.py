from broll_bot.config import AnalysisConfig
from broll_bot.models import Sentence
from broll_bot.scenes import HeuristicSceneAnalyzer, _plan_to_scenes, _SceneOut, _ScenePlan, merge_short_scenes


def sentences(n=6, dur=4.0):
    out = []
    for i in range(n):
        out.append(Sentence(i, f"Frase {i} sobre tecnologia e cidade", paragraph=i // 2, start=i * dur, end=(i + 1) * dur))
    return out


def item(first, last, **kw):
    base = dict(visual_summary="city at night", queries=['"city night"', "#traffic lights", "city night"],
                fallback_query="city", negative_terms=["cartoon", " "], tone="calm")
    base.update(kw)
    return _SceneOut(first_sentence=first, last_sentence=last, **base)


def test_plan_to_scenes_covers_everything_in_order():
    plan = _ScenePlan(scenes=[item(0, 1), item(2, 4), item(5, 5)])
    scenes = _plan_to_scenes(plan, sentences(), audio_duration=24.5)
    assert [s.index for s in scenes] == [0, 1, 2]
    assert scenes[0].start == 0 and scenes[-1].end == 24.5
    assert scenes[1].start == 8.0
    assert scenes[0].analysis.queries == ["city night", "traffic lights"]  # limpas e sem duplicatas
    assert scenes[0].analysis.negative_terms == ["cartoon"]


def test_plan_repairs_gaps_overlaps_and_missing_start():
    plan = _ScenePlan(scenes=[item(3, 5), item(1, 2), item(1, 3), item(99, 100)])
    scenes = _plan_to_scenes(plan, sentences(), audio_duration=24.0)
    assert scenes[0].start == 0.0
    covered = " ".join(s.text for s in scenes)
    for i in range(6):
        assert f"Frase {i} " in covered


def test_plan_with_empty_queries_uses_fallback():
    plan = _ScenePlan(scenes=[item(0, 5, queries=[], fallback_query="busy street")])
    [scene] = _plan_to_scenes(plan, sentences(), audio_duration=24.0)
    assert scene.analysis.queries == ["busy street"]


def test_heuristic_groups_by_duration_and_paragraph():
    cfg = AnalysisConfig(target_scene_seconds=8, min_scene_seconds=3, max_scene_seconds=20)
    scenes = HeuristicSceneAnalyzer(cfg).analyze(sentences(), audio_duration=24.0)
    assert len(scenes) == 3
    assert all(s.analysis.queries for s in scenes)
    assert scenes[0].analysis.query_language == "pt"


def test_merge_short_scenes():
    cfg = AnalysisConfig(target_scene_seconds=8)
    scenes = HeuristicSceneAnalyzer(cfg).analyze(sentences(), audio_duration=24.0)
    scenes[1].end = scenes[1].start + 0.5
    scenes[2].start = scenes[1].end
    merged = merge_short_scenes(scenes, min_seconds=1.0)
    assert len(merged) == 2 and [s.index for s in merged] == [0, 1]
