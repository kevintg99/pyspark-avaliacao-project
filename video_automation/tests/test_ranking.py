"""Ranking com um embedder falso (mesma interface do CLIP, sem PyTorch)."""

from pathlib import Path

import numpy as np

from broll_bot.config import AppConfig
from broll_bot.models import Candidate, MediaType, Scene, SceneAnalysis
from broll_bot.ranking import CLEAN_PROMPT, TEXT_PROMPT, Ranker

# eixos: 0=cidade/noite, 1=comida, 2=texto na tela, 3=desenho animado
AXES = {"city": 0, "night": 0, "traffic": 0, "food": 1, "kitchen": 1, "text": 2, "cartoon": 3}


def vec(weights):
    v = np.zeros(6)
    for axis, w in weights.items():
        v[axis] = w
    v[5] = 0.25  # componente comum (cossenos realistas ~0.2-0.35)
    return v / np.linalg.norm(v)


class FakeEmbedder:
    def __init__(self, image_vectors):
        self.image_vectors = image_vectors

    def encode_texts(self, texts):
        out = []
        for t in texts:
            if t == TEXT_PROMPT:
                out.append(vec({2: 1.0}))
            elif t == CLEAN_PROMPT:
                out.append(vec({4: 1.0}))
            else:
                hits = {AXES[w]: 1.0 for w in t.lower().replace(":", " ").split() if w in AXES}
                out.append(vec(hits or {4: 0.3}))
        return np.stack(out)

    def encode_images(self, paths):
        return np.stack([self.image_vectors[Path(p).name] for p in paths])


def cand(i, thumb, tags="stock clip"):
    return Candidate("p", str(i), MediaType.VIDEO, 1920, 1080, 10.0, None, None, tags=tags.split(),
                     description=tags, thumbnail_path=thumb)


def test_clip_ranking_prefers_visual_match_and_penalizes_text(tmp_path):
    images = {
        "city.jpg": vec({0: 1.0}),
        "food.jpg": vec({1: 1.0}),
        "city_text.jpg": vec({0: 1.0, 2: 1.2}),
        "cartoon_city.jpg": vec({0: 0.4, 3: 1.2}),
    }
    for name in images:
        (tmp_path / name).write_bytes(b"x")
    scene = Scene(0, "", 0, 10, SceneAnalysis("city street at night with traffic", ["city night traffic"], "city",
                                              ["cartoon"], "calm"))
    # tags enganosas: o metadado do "food" diz cidade, mas a imagem mostra comida
    pool = [cand(1, str(tmp_path / "food.jpg"), "city night traffic"),
            cand(2, str(tmp_path / "city.jpg")),
            cand(3, str(tmp_path / "city_text.jpg")),
            cand(4, str(tmp_path / "cartoon_city.jpg"))]
    ranker = Ranker(AppConfig(), FakeEmbedder(images), thumbs=None)
    ranked = ranker.rank(scene, pool)
    order = [c.media_id for c in ranked]
    assert order[0] == "2"
    assert order.index("1") > order.index("2")
    by_id = {c.media_id: c for c in ranked}
    assert by_id["3"].text_penalty > 0.5 and by_id["2"].text_penalty < 0.5
    assert by_id["4"].score < by_id["2"].score
    assert by_id["2"].embedding is not None
    assert ranker.too_similar(by_id["2"], by_id["2"])
    scores = ranker.text_image_scores("city night", [by_id["2"], by_id["1"]])
    assert scores[0] > scores[1]


def test_lexical_ranking_without_clip_filters_low_quality():
    scene = Scene(0, "", 0, 10, SceneAnalysis("a cup of coffee", ["coffee cup"], "coffee", [], "calm"))
    small = cand(1, None, "coffee cup")
    small.width, small.height = 640, 360
    good = cand(2, None, "coffee cup morning")
    ranked = Ranker(AppConfig(), None, None).rank(scene, [small, good])
    assert [c.media_id for c in ranked] == ["2"]
