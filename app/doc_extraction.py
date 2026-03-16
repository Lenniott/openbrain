from __future__ import annotations


def extract_text(data: bytes, filename: str) -> str:
    """Extract plain text from a file's raw bytes based on its extension."""
    lower = filename.lower()

    if lower.endswith(".pdf"):
        return _extract_pdf(data)

    if lower.endswith(".txt") or lower.endswith(".md"):
        return data.decode("utf-8", errors="replace")

    raise ValueError(f"Unsupported file type for text extraction: {filename!r}")


def _extract_pdf(data: bytes) -> str:
    import fitz  # pymupdf

    doc = fitz.open(stream=data, filetype="pdf")
    pages = [page.get_text() for page in doc]
    doc.close()
    return "\n".join(pages).strip()
