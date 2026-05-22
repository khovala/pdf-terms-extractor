from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from rag_system.db.models import Document, ExtractedItem, ProcessingEvent


def get_document_by_hash(session: Session, file_hash: str) -> Document | None:
    stmt = select(Document).where(Document.file_hash == file_hash)
    return session.execute(stmt).scalar_one_or_none()


def create_document(session: Session, file_name: str, file_hash: str) -> Document:
    document = Document(file_name=file_name, file_hash=file_hash, status="NEW")
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def update_document_status(session: Session, document_id: int, status: str) -> None:
    document = session.get(Document, document_id)
    if document is None:
        return
    document.status = status
    session.commit()


def add_event(session: Session, document_id: int, stage: str, message: str) -> None:
    event = ProcessingEvent(
        document_id=document_id,
        stage=stage,
        level="INFO",
        message=message,
    )
    session.add(event)
    session.commit()


def reset_stale_processing(
    session: Session, timeout_minutes: int = 30
) -> int:
    """Move documents stuck in PROCESSING back to NEW after a timeout."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
    stmt = (
        update(Document)
        .where(Document.status == "PROCESSING", Document.updated_at < cutoff)
        .values(status="NEW", updated_at=datetime.now(timezone.utc))
    )
    result = session.execute(stmt)
    session.commit()
    return result.rowcount


def prepare_reprocess(session: Session, document_id: int) -> None:
    """Delete existing extracted items and reset a FAILED document for reprocessing."""
    session.execute(
        delete(ExtractedItem).where(ExtractedItem.document_id == document_id)
    )
    update_document_status(session, document_id, "NEW")
