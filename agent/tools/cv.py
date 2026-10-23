"""CV spoke — bring a PDF or Markdown CV into the run store as Markdown."""
from pathlib import Path

from agent.run_store import RunStore


def pdf_to_markdown(pdf_path: str) -> str:
    """Extract text from a PDF, one page per block."""
    # Imported lazily so users who never use PDF CVs don't load pypdf.
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            # Comment marker preserves the page boundary in the output so
            # downstream scoring can still see logical sections even after
            # tables/columns get flattened.
            pages.append(f"<!-- page {i + 1} -->\n{text.strip()}")
    return "\n\n".join(pages)


def convert_cv(store: RunStore, cv_path: str) -> str:
    """Store the CV at ``cv_path`` as Markdown in the run store.

    PDFs are text-extracted; ``.md`` files are copied as-is. The CV keeps its
    file stem as its name, which is what scoring reports as ``best_cv``.

    Raises:
        ValueError: unsupported extension, or no extractable text (e.g. a
            scanned image-only PDF — OCR is out of scope).
        FileNotFoundError: the path doesn't exist.
    """
    path = Path(cv_path)
    if not path.exists():
        raise FileNotFoundError(f"CV not found: {cv_path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        content = pdf_to_markdown(str(path))
    elif suffix == ".md":
        content = path.read_text(encoding="utf-8")
    else:
        raise ValueError(f"Unsupported CV format '{suffix}' — use .pdf or .md")

    if not content.strip():
        raise ValueError(f"No text could be extracted from {path.name} (scanned PDF?)")

    store.save_cv(path.stem, content)
    return f"CV '{path.stem}' stored as Markdown ({len(content)} chars, source {suffix})"
