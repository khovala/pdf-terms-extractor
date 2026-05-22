from rag_system.extraction.models import ExtractedEntity


def validate_entities(
    entities: list[ExtractedEntity],
    min_confidence: float = 0.4,
) -> tuple[list[ExtractedEntity], int]:
    validated: list[ExtractedEntity] = []
    rejected = 0

    for entity in entities:
        if entity.confidence is not None and entity.confidence < min_confidence:
            rejected += 1
            continue
        if entity.term.strip().lower() == entity.definition.strip().lower():
            rejected += 1
            continue
        if len(entity.definition.strip()) < 3:
            rejected += 1
            continue
        validated.append(entity)

    return validated, rejected
