from pathlib import Path

import fitz
import pytesseract
from PIL import Image
from pypdf import PdfReader


def load_pdf_documents(raw_dir: str) -> list[Path]:
    return sorted(Path(raw_dir).glob("**/*.pdf"))


def _extract_text_layer(path: Path) -> tuple[list[str], int]:
    reader = PdfReader(str(path))
    page_texts: list[str] = []
    for page in reader.pages:
        page_texts.append(page.extract_text() or "")
    return page_texts, len(reader.pages)


def _extract_text_ocr(path: Path, ocr_languages: str) -> tuple[list[str], int]:
    doc = fitz.open(str(path))
    try:
        page_texts: list[str] = []
        scale = 300 / 72  # render page at ~300 DPI
        matrix = fitz.Matrix(scale, scale)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(image, lang=ocr_languages)
            page_texts.append(text or "")
        return page_texts, len(doc)
    finally:
        doc.close()


def extract_pdf_text(
    path: Path, ocr_languages: str, min_text_chars: int
) -> tuple[list[str], str, int]:
    """
    Extract text from a PDF, preferring the embedded text layer.

    Returns (page_texts, source, pages_count) where:
      - page_texts: list of per-page text strings (index 0 = page 1)
      - source: 'text_layer', 'ocr', or 'empty'
      - pages_count: total number of pages in the document
    """
    page_texts, pages_count = _extract_text_layer(path)
    full_text = "\n".join(page_texts).strip()
    if len(full_text) >= min_text_chars:
        return page_texts, "text_layer", pages_count

    ocr_page_texts, ocr_pages_count = _extract_text_ocr(
        path=path, ocr_languages=ocr_languages
    )
    full_ocr_text = "\n".join(ocr_page_texts).strip()
    if full_ocr_text:
        return ocr_page_texts, "ocr", ocr_pages_count
    return [], "empty", pages_count
