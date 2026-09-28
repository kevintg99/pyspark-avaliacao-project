"""Divisão da narração em cenas semânticas + análise visual de cada cena.

Dois analisadores com a mesma interface:

* ``ClaudeSceneAnalyzer`` (recomendado): uma única chamada à API do Claude
  agrupa as frases por ideia e devolve, para cada cena, o resumo visual,
  consultas curtas em inglês, termos a evitar e o tom. Resultado em cache.
* ``HeuristicSceneAnalyzer``: plano B offline (sem LLM), agrupa por duração e
  parágrafo e extrai palavras-chave no idioma do roteiro.
"""

from __future__ import annotations

from collections import Counter
from typing import Protocol

from pydantic import BaseModel, Field

from .cache import SQLiteCache, make_key
from .config import AnalysisConfig
from .log import get_logger
from .models import Scene, SceneAnalysis, Sentence
from .text import STOPWORDS, keywords, tokens

log = get_logger("scenes")


class SceneAnalyzer(Protocol):
    def analyze(self, sentences: list[Sentence], audio_duration: float) -> list[Scene]: ...


# ---------------------------------------------------------------- Claude
class _SceneOut(BaseModel):
    first_sentence: int = Field(description="Índice da primeira frase da cena")
    last_sentence: int = Field(description="Índice da última frase da cena (inclusive)")
    visual_summary: str = Field(description="What the viewer should SEE, concrete and filmable, in English")
    queries: list[str] = Field(description="2 to 4 short stock-footage search queries in English (2-4 words each)")
    fallback_query: str = Field(description="One broader, generic query in English used if the others fail")
    negative_terms: list[str] = Field(description="Visual things to avoid for this scene, in English")
    tone: str = Field(description="One or two words, e.g. serious, upbeat, technological, calm")


class _ScenePlan(BaseModel):
    scenes: list[_SceneOut]


SYSTEM_PROMPT = """You are a senior video editor who picks stock b-roll for narrated videos.
You receive a narration script split into numbered sentences with their timing.
Group consecutive sentences into scenes by IDEA (a change of subject, example or argument starts a new scene), not by sentence count.

For every scene:
- visual_summary: describe the concrete shot a viewer should see while hearing this part. Prefer literal, filmable imagery that stock libraries actually have (people, places, objects, actions). For abstract ideas, choose a clear visual metaphor that still makes sense with the narration.
- queries: 2-4 short English search queries (2-4 words each), ordered from most specific to more general. Name subjects and actions, not concepts. No quotes, no hashtags.
- fallback_query: one broader English query that still fits the scene.
- negative_terms: things that would look wrong or off-topic for this scene (e.g. wrong setting, text overlays, logos, cartoon).
- tone: the emotional/visual tone.

Scenes must be contiguous, cover every sentence exactly once and keep the original order."""


class ClaudeSceneAnalyzer:
    def __init__(self, cfg: AnalysisConfig, cache: SQLiteCache | None = None) -> None:
        import anthropic  # import tardio: só é necessário neste modo

        self.cfg = cfg
        self.cache = cache
        self.client = anthropic.Anthropic()

    def _prompt(self, sentences: list[Sentence]) -> str:
        lines = [
            f"[{s.index}] ({s.start:.1f}s-{s.end:.1f}s, {s.end - s.start:.1f}s) {s.text}" for s in sentences
        ]
        return (
            f"Script language: {self.cfg.script_language}. "
            f"Target scene length: about {self.cfg.target_scene_seconds:.0f}s "
            f"(between {self.cfg.min_scene_seconds:.0f}s and {self.cfg.max_scene_seconds:.0f}s when the ideas allow).\n\n"
            "Sentences:\n" + "\n".join(lines)
        )

    def analyze(self, sentences: list[Sentence], audio_duration: float) -> list[Scene]:
        import anthropic

        prompt = self._prompt(sentences)
        key = make_key("scene-plan", self.cfg.model, SYSTEM_PROMPT, prompt)
        plan_data = self.cache.get("scene_plan", key) if self.cache else None
        if plan_data is None:
            try:
                response = self.client.beta.messages.parse(
                    model=self.cfg.model,
                    max_tokens=self.cfg.max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    output_format=_ScenePlan,
                    output_config={"effort": self.cfg.effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except anthropic.APIError as exc:
                raise AnalysisError(f"falha na API do Claude: {exc}") from exc
            if response.stop_reason == "refusal":
                raise AnalysisError("o modelo recusou a análise do roteiro")
            if response.stop_reason == "max_tokens" or response.parsed_output is None:
                raise AnalysisError("resposta incompleta do modelo (aumente analysis.max_tokens)")
            plan_data = response.parsed_output.model_dump()
            if self.cache:
                self.cache.set("scene_plan", key, plan_data, ttl_seconds=30 * 86400)
            log.info("plano de cenas gerado pelo Claude", extra={"stage": "scenes", "scenes": len(plan_data["scenes"])})
        else:
            log.info("plano de cenas reaproveitado do cache", extra={"stage": "scenes"})
        return _plan_to_scenes(_ScenePlan.model_validate(plan_data), sentences, audio_duration)


class AnalysisError(RuntimeError):
    pass


def _clean_queries(queries: list[str], limit: int = 4) -> list[str]:
    out: list[str] = []
    for q in queries:
        q = " ".join(q.replace('"', " ").replace("#", " ").split()[:5]).strip()
        if q and q.lower() not in {o.lower() for o in out}:
            out.append(q)
    return out[:limit]


def _plan_to_scenes(plan: _ScenePlan, sentences: list[Sentence], audio_duration: float) -> list[Scene]:
    """Converte o plano em cenas, reparando lacunas/sobreposições se houver."""
    n = len(sentences)
    by_start: dict[int, _SceneOut] = {}
    for item in sorted(plan.scenes, key=lambda s: s.first_sentence):
        start = min(max(item.first_sentence, 0), n - 1)
        by_start.setdefault(start, item)
    starts = sorted(by_start)
    if not starts or starts[0] != 0:
        first = by_start.get(starts[0]) if starts else None
        starts = [0, *[s for s in starts if s != 0]]
        if first is not None:
            by_start[0] = first
    scenes: list[Scene] = []
    for i, start in enumerate(starts):
        stop = starts[i + 1] if i + 1 < len(starts) else n
        chunk = sentences[start:stop]
        item = by_start[start]
        fallback = _clean_queries([item.fallback_query])
        queries = _clean_queries(item.queries) or fallback or _clean_queries([item.visual_summary])
        analysis = SceneAnalysis(
            visual_summary=item.visual_summary.strip(),
            queries=queries,
            fallback_query=fallback[0] if fallback else queries[-1],
            negative_terms=[t.strip() for t in item.negative_terms if t.strip()][:8],
            tone=item.tone.strip() or "neutral",
            query_language="en",
        )
        scenes.append(Scene(len(scenes), " ".join(s.text for s in chunk), chunk[0].start,
                            chunk[-1].end if stop < n else audio_duration, analysis))
    return scenes


# -------------------------------------------------------------- heurística
class HeuristicSceneAnalyzer:
    def __init__(self, cfg: AnalysisConfig) -> None:
        self.cfg = cfg

    def analyze(self, sentences: list[Sentence], audio_duration: float) -> list[Scene]:
        groups: list[list[Sentence]] = []
        current: list[Sentence] = []
        for sentence in sentences:
            if current:
                dur = current[-1].end - current[0].start
                new_paragraph = sentence.paragraph != current[-1].paragraph
                if dur >= self.cfg.target_scene_seconds or (new_paragraph and dur >= self.cfg.min_scene_seconds) \
                        or dur + (sentence.end - sentence.start) > self.cfg.max_scene_seconds:
                    groups.append(current)
                    current = []
            current.append(sentence)
        if current:
            if groups and current[-1].end - current[0].start < self.cfg.min_scene_seconds:
                groups[-1].extend(current)
            else:
                groups.append(current)

        doc_freq = Counter(k for s in sentences for k in set(keywords(s.text)))
        scenes = []
        for group in groups:
            text = " ".join(s.text for s in group)
            analysis = self._analysis(text, doc_freq, len(groups))
            scenes.append(Scene(len(scenes), text, group[0].start, group[-1].end, analysis))
        if scenes:
            scenes[0].start = 0.0
            scenes[-1].end = audio_duration
        log.info("cenas definidas pela heurística", extra={"stage": "scenes", "scenes": len(scenes)})
        return scenes

    def _analysis(self, text: str, doc_freq: Counter, n_docs: int) -> SceneAnalysis:
        counts = Counter(keywords(text, min_len=4))
        # TF-IDF simples: prioriza palavras marcantes da cena
        ranked = sorted(counts, key=lambda k: counts[k] * (1 + n_docs / (1 + doc_freq.get(k, 0))), reverse=True)
        top = ranked[:6] or [t for t in tokens(text) if t not in STOPWORDS][:3] or ["abstract"]
        queries = _clean_queries([" ".join(top[:2]), " ".join(top[1:3]), top[0], " ".join(top[2:4])])
        return SceneAnalysis(
            visual_summary=text[:300],
            queries=queries,
            fallback_query=top[0],
            negative_terms=["text", "logo", "watermark"],
            tone="neutral",
            query_language=self.cfg.script_language,
        )


def merge_short_scenes(scenes: list[Scene], min_seconds: float) -> list[Scene]:
    """Cenas curtíssimas viram parte da anterior (evita cortes de 0,5s)."""
    merged: list[Scene] = []
    for scene in scenes:
        if merged and scene.duration < min_seconds:
            prev = merged[-1]
            prev.end = scene.end
            prev.text = f"{prev.text} {scene.text}"
            continue
        merged.append(scene)
    if len(merged) > 1 and merged[0].duration < min_seconds:
        first = merged.pop(0)
        merged[0].start = first.start
        merged[0].text = f"{first.text} {merged[0].text}"
    for i, scene in enumerate(merged):
        scene.index = i
    return merged


def build_analyzer(cfg: AnalysisConfig, cache: SQLiteCache | None) -> SceneAnalyzer:
    if cfg.provider == "anthropic":
        try:
            return ClaudeSceneAnalyzer(cfg, cache)
        except Exception as exc:  # noqa: BLE001 - sem SDK/credencial: heurística
            log.warning("Claude indisponível; usando heurística", extra={"stage": "scenes", "error": str(exc)})
    return HeuristicSceneAnalyzer(cfg)

