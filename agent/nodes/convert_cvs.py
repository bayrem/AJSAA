"""Convert PDF CVs in query/resume/ to Markdown files."""
import logging
from pathlib import Path

from agent.state import AgentState

logger = logging.getLogger(__name__)


def _pdf_to_markdown(pdf_path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"<!-- page {i + 1} -->\n{text.strip()}")
    return "\n\n".join(pages)


def run(state: AgentState) -> AgentState:
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

    max_cvs = state["config"].get("scoring", {}).get("max_cvs", 5)
    if len(cvs) > max_cvs:
        cvs = cvs[:max_cvs]

    return {**state, "cvs": cvs, "errors": errors, "run_log": run_log}
