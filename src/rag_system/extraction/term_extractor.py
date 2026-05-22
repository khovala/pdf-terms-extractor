import re

from rag_system.extraction.models import ExtractedEntity

SECTION_TITLE_RE = re.compile(
    r"^\s*(\d+(\.\d+)*)?\s*"
    r"(сокращени[яй]|термины?\s+и\s+определени[яй]|термины?|определени[яй])"
    r"\s*$",
    flags=re.IGNORECASE,
)
PAIR_RE = re.compile(
    r"^\s*(?:\d+[\)\.]?\s+)?"
    r"([A-Za-zА-Яа-яЁё0-9\-\(\)\"'«»/.,\s]{2,120}?)"
    r"\s*[-—:]\s*(.{3,})\s*$"
)
ABBREVIATION_RE = re.compile(r"^[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9\-.\s]{1,24}$")


_SECTION_TYPE_MAP: dict[str, str] = {
    "сокращения": "abbreviation",
    "сокращений": "abbreviation",
    "сокращениям": "abbreviation",
    "сокращениями": "abbreviation",
    "сокращениях": "abbreviation",
}


def _map_section_title(title: str) -> str:
    base = re.sub(r"\s+", " ", title.strip().lower())
    return _SECTION_TYPE_MAP.get(base, "term_definition")


def _normalize_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line]


def _split_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Return list of (section_type, body_lines) for each detected section."""
    section_starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = SECTION_TITLE_RE.match(line)
        if match and match.group(3):
            section_type = _map_section_title(match.group(3))
            section_starts.append((index, section_type))

    if not section_starts:
        return [("term_definition", lines)]

    sections: list[tuple[str, list[str]]] = []
    for idx, (start, section_type) in enumerate(section_starts):
        end = (
            section_starts[idx + 1][0]
            if idx + 1 < len(section_starts)
            else len(lines)
        )
        body = lines[start + 1 : end]
        if body:
            sections.append((section_type, body))
    return sections or [("term_definition", lines)]


def _guess_item_type(term: str) -> str:
    compact = re.sub(r"\s+", " ", term.strip())
    if ABBREVIATION_RE.match(compact):
        return "abbreviation"
    return "term_definition"


def _clean_definition(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .;-")


def _compute_confidence(term: str, definition: str, source: str) -> float:
    if source == "text_layer":
        base = 0.85
    elif source == "ocr":
        base = 0.55
    else:
        base = 0.5

    if len(term) >= 3 and len(definition) >= 10:
        base += 0.05
    if len(definition) >= 40:
        base += 0.05
    if len(term) >= 8:
        base += 0.05

    return min(base, 0.95)


def extract_entities_from_text(
    page_texts: list[str], source: str = "text_layer"
) -> list[ExtractedEntity]:
    """
    Extract abbreviation / term-definition pairs from per-page document text.

    Each entity is assigned the page number it was found on.
    """
    entities: list[ExtractedEntity] = []
    seen_pairs: set[tuple[str, str]] = set()

    for page_idx, page_text in enumerate(page_texts):
        page_number = page_idx + 1
        lines = _normalize_lines(page_text)
        if not lines:
            continue

        sections = _split_sections(lines)

        for section_type, body_lines in sections:
            for line in body_lines:
                match = PAIR_RE.match(line)
                if not match:
                    continue

                term = re.sub(r"\s+", " ", match.group(1)).strip(" .;")
                definition = _clean_definition(match.group(2))
                if len(term) < 2 or len(definition) < 3:
                    continue

                key = (term.lower(), definition.lower())
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)

                entities.append(
                    ExtractedEntity(
                        item_type=section_type
                        if section_type == "abbreviation"
                        else _guess_item_type(term),
                        term=term,
                        definition=definition,
                        page=page_number,
                        confidence=_compute_confidence(term, definition, source),
                        raw_fragment=line,
                        source=source,
                    )
                )

    return entities
