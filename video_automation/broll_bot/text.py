"""Utilitários de texto: normalização, tokenização e stopwords (PT/EN)."""

from __future__ import annotations

import re
import unicodedata

_WORD_RE = re.compile(r"[a-z0-9]+")

STOPWORDS_PT = set(
    """
    a à às ao aos as o os um uma uns umas de da das do dos dum duma em na nas no nos num numa
    por pela pelas pelo pelos para pra pro com sem sob sobre entre até após desde contra
    e ou mas porém contudo todavia que se como quando onde porque porquê pois então logo assim
    também já ainda só apenas mais menos muito muita muitos muitas pouco pouca bem mal
    eu tu ele ela nós vós eles elas você vocês me te se nos lhe lhes meu minha meus minhas
    teu tua seu sua seus suas nosso nossa nossos nossas isso isto aquilo esse essa esses essas
    este esta estes estas aquele aquela aqueles aquelas ser é são era eram foi foram será serão
    seja sejam sido estar está estão estava estavam esteve ter tem têm tinha tinham teve há
    haver fazer faz fazem fez vai vão ir vamos pode podem poder deve devem dever cada todo toda
    todos todas outro outra outros outras qual quais quem cujo cuja aqui ali lá hoje agora sempre
    nunca não sim tão tanto tanta coisa coisas vez vezes dia dias ano anos gente ponto tipo
    """.split()
)

STOPWORDS_EN = set(
    """
    a an the and or but if then else of to in on at by for with without from into onto over under
    is are was were be been being am do does did done have has had having it its this that these
    those there here i you he she we they me him her us them my your his our their what which who
    whom when where why how all any both each few more most other some such no nor not only own
    same so than too very can will just should now also up down out about as again very shot
    """.split()
)

STOPWORDS = STOPWORDS_PT | STOPWORDS_EN

FILENAME_NOISE = {"mixkit", "hd", "ready", "4k", "uhd", "large", "medium", "small", "preview", "free", "stock", "video", "footage", "clip"}


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text: str) -> str:
    return strip_accents(text.lower())


def tokens(text: str) -> list[str]:
    return _WORD_RE.findall(normalize(text))


def stem(word: str) -> str:
    """Stem bem leve (plural) — suficiente para casar tags."""
    for suffix in ("oes", "aes", "ies"):
        if word.endswith(suffix) and len(word) > 4:
            return word[: -len(suffix)] + ("ao" if suffix != "ies" else "y")
    if word.endswith("s") and len(word) > 3 and not word.endswith("ss"):
        return word[:-1]
    return word


def keywords(text: str, min_len: int = 3) -> list[str]:
    return [stem(t) for t in tokens(text) if len(t) >= min_len and t not in STOPWORDS and not t.isdigit()]


def overlap_score(query_terms: list[str], doc_terms: list[str]) -> float:
    """Fração dos termos da consulta presentes no documento (0..1)."""
    q = {stem(t) for t in query_terms}
    if not q:
        return 0.0
    d = {stem(t) for t in doc_terms}
    return len(q & d) / len(q)
