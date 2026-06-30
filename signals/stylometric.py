import re
from statistics import stdev

# Phrases overrepresented in LLM output due to RLHF training
HEDGE_PHRASES = [
    "it is worth noting", "in conclusion", "it is important to",
    "as mentioned", "it should be noted", "furthermore", "moreover",
    "in summary", "to summarize", "it goes without saying",
    "needless to say", "as previously mentioned", "on the other hand",
    "in other words", "having said that", "at the end of the day",
    "last but not least", "all things considered", "it is clear that",
    "delve into", "it is crucial", "it is essential",
]


def _sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s for s in parts if s.strip()]


def _words(text):
    return re.findall(r"\b[a-zA-Z]+\b", text.lower())


def _slv_score(sentences):
    """Sentence-length variance score. Low variance → AI → score near 1.0."""
    lengths = [len(_words(s)) for s in sentences]
    if len(lengths) < 3:
        return 0.5  # too few sentences to measure reliably — neutral
    variance = stdev(lengths)
    # Human stdev typically 6–16; AI stdev typically 2–7.
    # Map stdev=0 → 1.0, stdev≥15 → 0.0.
    return max(0.0, min(1.0, 1.0 - variance / 15.0))


def _hedge_score(text, sentences):
    """Hedge/filler density score. High density → AI → score near 1.0."""
    count = sum(1 for phrase in HEDGE_PHRASES if phrase in text.lower())
    density = count / max(len(sentences), 1)
    # 0 phrases/sentence → 0.0; ≥0.5 phrases/sentence → 1.0
    return min(1.0, density * 2.0)


def _punct_score(text, words):
    """Varied-punctuation density score. Low density → AI → score near 1.0."""
    varied = len(re.findall(r"[;:\-—…\"\'()]", text))
    density = varied / max(len(words), 1)
    # Human density ~0.05–0.15; AI density ~0.01–0.05.
    # Map density=0 → 1.0, density≥0.10 → 0.0.
    return max(0.0, min(1.0, 1.0 - density / 0.10))


def compute_style_score(text: str) -> dict:
    """
    Signal 1 — stylometric heuristics.
    Returns {"style_score": float} where 1.0 = strong AI signal.
    Spec: style_score = 0.40 * slv + 0.40 * hedge + 0.20 * punct
    """
    sentences = _sentences(text)
    words = _words(text)

    slv = _slv_score(sentences)
    hedge = _hedge_score(text, sentences)
    punct = _punct_score(text, words)

    style_score = round(0.40 * slv + 0.40 * hedge + 0.20 * punct, 4)

    return {"style_score": style_score}
