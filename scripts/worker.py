import argparse
import hashlib
import logging
import time
from pathlib import Path

from rag_system.config import settings
from rag_system.db.crud import (
    add_event,
    create_document,
    get_document_by_hash,
    prepare_reprocess,
    reset_stale_processing,
    update_document_status,
)
from rag_system.db.models import Document
from rag_system.db.repositories.extracted_items_repository import (
    ExtractedItemsRepository,
)
from rag_system.db.session import get_session
from rag_system.extraction.term_extractor import extract_entities_from_text
from rag_system.extraction.validator import validate_entities
from rag_system.ingestion.loader import extract_pdf_text

logger = logging.getLogger(__name__)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _should_process(document: Document | None, reprocess: bool) -> bool:
    if document is None:
        return True
    if document.status in ("COMPLETED", "SKIPPED"):
        return False
    if document.status == "FAILED" and not reprocess:
        return False
    return True


def _process_document(pdf_path: Path, reprocess: bool) -> None:
    session = get_session()
    extracted_items_repo = ExtractedItemsRepository(session)
    document: Document | None = None

    try:
        file_hash = file_sha256(pdf_path)
        document = get_document_by_hash(session, file_hash)

        if not _should_process(document, reprocess):
            status = document.status if document else "NEW"
            logger.info("Skipping %s (status=%s)", pdf_path.name, status)
            return

        if document is None:
            document = create_document(
                session=session,
                file_name=pdf_path.name,
                file_hash=file_hash,
            )
        elif document.status == "FAILED" and reprocess:
            prepare_reprocess(session, document.id)

        assert document is not None
        update_document_status(session, document.id, "PROCESSING")
        add_event(session, document.id, "discover", f"Started: {pdf_path.name}")

        try:
            page_texts, source, pages_count = extract_pdf_text(
                path=pdf_path,
                ocr_languages=settings.ocr_languages,
                min_text_chars=settings.ocr_min_text_chars,
            )

            document.pages_count = pages_count
            session.commit()

            if source == "ocr":
                add_event(session, document.id, "ocr", "OCR fallback applied")

            if source == "empty" or not any(t.strip() for t in page_texts):
                add_event(
                    session,
                    document.id,
                    "ocr",
                    "Не удалось извлечь текст ни из слоя, ни через OCR.",
                )
                update_document_status(session, document.id, "SKIPPED")
                return

            add_event(
                session,
                document.id,
                "section-detection",
                f"Searching sections across {pages_count} page(s)",
            )

            extracted_items = extract_entities_from_text(
                page_texts, source=source
            )
            add_event(
                session,
                document.id,
                "entity-extraction",
                f"Raw extraction produced {len(extracted_items)} entities",
            )

            validated_items, rejected = validate_entities(
                extracted_items,
                min_confidence=settings.validation_min_confidence,
            )
            add_event(
                session,
                document.id,
                "validation",
                f"Validation: kept {len(validated_items)}, rejected {rejected}",
            )

            saved_items = extracted_items_repo.replace_for_document(
                document_id=document.id,
                items=validated_items,
            )
            add_event(
                session,
                document.id,
                "persist",
                f"Saved {saved_items} extracted_items rows",
            )
            update_document_status(session, document.id, "COMPLETED")
            logger.info(
                "Completed %s: %d items saved (source=%s)",
                pdf_path.name,
                saved_items,
                source,
            )

        except Exception as exc:
            logger.exception("Failed to process %s", pdf_path.name)
            add_event(session, document.id, "worker", f"Error: {exc}")
            update_document_status(session, document.id, "FAILED")

    finally:
        session.close()


def process_once(input_dir: str, reprocess: bool = False) -> None:
    admin_session = get_session()
    try:
        reset_count = reset_stale_processing(admin_session, timeout_minutes=30)
        if reset_count:
            logger.info(
                "Reset %d stale PROCESSING document(s) to NEW", reset_count
            )
    finally:
        admin_session.close()

    pdf_paths = sorted(Path(input_dir).glob("*.pdf"))
    logger.info("Found %d PDF(s) in %s", len(pdf_paths), input_dir)

    for pdf_path in pdf_paths:
        _process_document(pdf_path, reprocess=reprocess)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="PDF terms extraction worker")
    parser.add_argument("--input-dir", default=settings.input_dir)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Reprocess previously FAILED documents",
    )
    args = parser.parse_args()

    if args.once:
        process_once(args.input_dir, reprocess=args.reprocess)
        return

    while True:
        process_once(args.input_dir, reprocess=args.reprocess)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
