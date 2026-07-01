import re
from statistics import stdev

COMMON_FUNCTION_WORDS = {
    "the", "a", "an", "of", "to", "and", "in", "is", "it", "that", "for",
    "on", "with", "as", "was", "at", "by", "this", "be", "are", "from",
}


def _words(text):
    return re.findall(r"\b[a-zA-Z]+\b", text.lower())


def _ttr_score(words):
    """
    Type-token ratio (unique words / total words).
    AI text tends to repeat a narrow, "safe" vocabulary across a passage,
    producing a lower TTR than human writing of the same length.
    Low TTR -> AI -> score near 1.0.
    """
    ttr = len(set(words)) / len(words)
    # Human TTR typically 0.55-0.75; AI TTR typically 0.35-0.55.
    # Map ttr>=0.75 -> 0.0, ttr<=0.35 -> 1.0.
    return max(0.0, min(1.0, (0.75 - ttr) / 0.40))


def _word_length_uniformity_score(words):
    """
    Standard deviation of word length across the passage.
    AI prose favors consistent, moderate-complexity word choice; human
    writing swings between short filler words and occasional long words.
    Low stdev -> AI -> score near 1.0.
    """
    lengths = [len(w) for w in words]
    variance = stdev(lengths)
    # Human stdev typically 2.6-3.4; AI stdev typically 1.8-2.4.
    # Map stdev<=1.8 -> 1.0, stdev>=3.2 -> 0.0.
    return max(0.0, min(1.0, (3.2 - variance) / 1.4))


def compute_lexical_score(text: str) -> dict:
    """
    Signal 3 -- lexical diversity heuristics.
    Returns {"lexical_score": float | None} where 1.0 = strong AI signal
    and None = abstain (not enough text to measure reliably).
    Blend: 0.60 * ttr_score + 0.40 * word_length_uniformity_score

    TTR in particular is a function of sample length (shorter passages have
    fewer chances to repeat any given word, so TTR trends high regardless of
    authorship below roughly 80 words). Rather than returning a fabricated
    "neutral" 0.5 -- which the ensemble would otherwise treat as a real vote
    and use to drag confidence toward "uncertain" even when the other two
    signals strongly agree -- this signal abstains (returns None) below that
    length, and scoring.py excludes abstaining signals from the vote.
    """
    words = _words(text)

    if len(words) < 80:
        return {"lexical_score": None}

    ttr = _ttr_score(words)
    uniformity = _word_length_uniformity_score(words)

    lexical_score = round(0.60 * ttr + 0.40 * uniformity, 4)

    return {"lexical_score": lexical_score}
