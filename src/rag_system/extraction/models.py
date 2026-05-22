from dataclasses import dataclass


@dataclass
class ExtractedEntity:
    item_type: str
    term: str
    definition: str
    page: int | None = None
    confidence: float | None = None
    raw_fragment: str | None = None
    source: str | None = None  # 'text_layer' | 'ocr'
