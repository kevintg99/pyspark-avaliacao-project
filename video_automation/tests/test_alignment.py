from broll_bot.models import Word
from broll_bot.transcription import align, parse_script


def test_parse_script_strips_markdown_and_splits_sentences():
    text = "# Título\n\nPrimeira frase. **Segunda** frase!\n\n- Terceira [pausa] frase?\n"
    sentences = parse_script(text)
    assert [s.text for s in sentences] == ["Título", "Primeira frase.", "Segunda frase!", "Terceira frase?"]
    assert sentences[1].paragraph == sentences[2].paragraph == 1
    assert sentences[3].paragraph == 2


def test_alignment_uses_word_timestamps():
    sentences = parse_script("Olá mundo bonito. Hoje falamos de tecnologia.")
    spoken = "olá mundo bonito hoje falamos de tecnologia".split()
    starts = [0.4, 0.9, 1.4, 5.0, 5.5, 6.0, 6.3]
    words = [Word(w, s, s + 0.4) for w, s in zip(spoken, starts)]
    align(sentences, words, 8.0)
    assert sentences[0].start == 0.0
    assert abs(sentences[1].start - 5.0) < 1e-6
    assert sentences[-1].end == 8.0


def test_alignment_tolerates_transcription_errors():
    sentences = parse_script("A inteligência artificial mudou tudo. Depois veio a nuvem.")
    spoken = [("a", 0.2), ("inteligencia", 0.5), ("artificiall", 1.0), ("mudou", 1.6), ("tudo", 2.0),
              ("depois", 4.1), ("veio", 4.5), ("a", 4.8), ("nuvem", 5.0)]
    words = [Word(w, s, s + 0.3) for w, s in spoken]
    align(sentences, words, 6.0)
    assert abs(sentences[1].start - 4.1) < 1e-6


def test_proportional_fallback_without_words():
    sentences = parse_script("Frase curta. Esta é uma frase bem mais longa que a primeira.")
    align(sentences, [], 10.0)
    assert sentences[0].start == 0 and sentences[-1].end == 10.0
    assert sentences[0].end < 5.0
