"""Pure text extraction from an uploaded architecture document. No I/O beyond
reading the given bytes — the extracted text is handed to the *existing*
ingest_patches conversational pipeline exactly like a large pasted message
(see DECISIONS.md's "PRD-bump override" entry); this module's only job is
turning a PDF/DOCX/text file into that plain-text input.
"""

from __future__ import annotations

import io

from docx import Document
from pypdf import PdfReader


class DocumentExtractionError(Exception):
    """The file couldn't be read as one of the supported formats."""


_TEXT_EXTENSIONS = (".txt", ".md", ".yml", ".yaml", ".json", ".env")


def extract_text(filename: str, content: bytes) -> str:
    lowered = filename.lower()

    if lowered.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise DocumentExtractionError(f"could not read '{filename}' as a PDF: {exc}") from exc
        text = "\n\n".join(p for p in pages if p.strip())

    elif lowered.endswith(".docx"):
        try:
            document = Document(io.BytesIO(content))
        except Exception as exc:
            raise DocumentExtractionError(f"could not read '{filename}' as a DOCX: {exc}") from exc
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
        # Tables carry real architecture content in practice (component
        # inventories, dependency matrices) — skipping them would silently
        # drop exactly the structured detail this feature exists to capture.
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    paragraphs.append(" | ".join(cells))
        text = "\n".join(paragraphs)

    elif lowered.endswith(_TEXT_EXTENSIONS) or "." not in lowered.rsplit("/", 1)[-1]:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentExtractionError(f"'{filename}' is not valid UTF-8 text") from exc

    else:
        raise DocumentExtractionError(
            f"unsupported file type for '{filename}' — supported: .pdf, .docx, .txt, .md, .yml, .yaml, .json"
        )

    if not text.strip():
        raise DocumentExtractionError(f"no extractable text found in '{filename}'")
    return text
