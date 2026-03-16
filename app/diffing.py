def normalise(text: str) -> list[str]:
    import re

    if not text:
        return []
    lowered = text.lower()
    stripped = re.sub(r"[^\w\s]", " ", lowered)
    tokens = re.sub(r"\s+", " ", stripped).strip().split(" ")
    return [t for t in tokens if t]


def jaccard_change_ratio(a: str, b: str) -> float:
    words_a = set(normalise(a))
    words_b = set(normalise(b))
    if not words_a and not words_b:
        return 0.0
    union = words_a | words_b
    inter = words_a & words_b
    return 1.0 - (len(inter) / len(union))

