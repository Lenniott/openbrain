from .config import settings


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[tuple[int, str]]:
    """
    Returns list of (chunk_index, chunk_text).
    """
    if not text:
        return []

    size = chunk_size or settings.CHUNK_SIZE
    ov = overlap or settings.CHUNK_OVERLAP

    chunks: list[tuple[int, str]] = []
    start = 0
    index = 0

    while start < len(text):
        end = min(len(text), start + size)
        chunk = text[start:end]
        chunks.append((index, chunk))
        if end == len(text):
            break
        start = end - ov
        index += 1

    return chunks


