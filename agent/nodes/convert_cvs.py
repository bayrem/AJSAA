"""Convert PDF CVs in ``query/resume/`` to Markdown so the scoring pipeline can use them.

The pipeline only knows how to score Markdown CVs — PDFs aren't directly
parseable by an LLM prompt. Conversion is a plain text extraction; we don't
try to preserve formatting. The original PDF is left in place and a sibling
``.md`` file is written alongside it.
"""
import logging
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)


def _pdf_to_markdown(pdf_path: str) -> str:
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


def run(state: AgentState) -> AgentState:
    """Convert every queued PDF to MD and add it to the CV list."""
    errors = list(state.get("errors", []))
    run_log = list(state.get("run_log", []))
    cvs = list(state.get("cvs", []))

    for pdf_path_str in state.get("pdf_paths", []):
        pdf_path = Path(pdf_path_str)
        md_path = pdf_path.with_suffix(".md")
        try:
            content = _pdf_to_markdown(pdf_path_str)
            md_path.write_text(content, encoding="utf-8")
            cvs.append({"name": pdf_path.stem, "content": content, "path": str(md_path)})
            run_log.append(f"Converted PDF → MD: {pdf_path.name}")
            logger.info("Converted %s to %s", pdf_path.name, md_path.name)
        except Exception as e:
            errors.append(f"PDF conversion failed for {pdf_path}: {e}")
            logger.error("PDF conversion failed for %s: %s", pdf_path, e)

    # Enforce max_cvs again here in case the user dropped many PDFs at once —
    # load_context already applies this cap to MD files.
    max_cvs = state["config"].get("scoring", {}).get("max_cvs", 5)
    if len(cvs) > max_cvs:
        cvs = cvs[:max_cvs]

    return {**state, "cvs": cvs, "errors": errors, "run_log": run_log}
