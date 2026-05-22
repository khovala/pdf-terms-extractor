from sqlalchemy import delete
from sqlalchemy.orm import Session

from rag_system.db.models import ExtractedItem
from rag_system.extraction.models import ExtractedEntity


class ExtractedItemsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_for_document(
        self, document_id: int, items: list[ExtractedEntity]
    ) -> int:
        self.session.execute(
            delete(ExtractedItem).where(ExtractedItem.document_id == document_id)
        )

        if not items:
            self.session.commit()
            return 0

        try:
            rows = [
                ExtractedItem(
                    document_id=document_id,
                    item_type=item.item_type,
                    term=item.term,
                    definition=item.definition,
                    page=item.page,
                    confidence=item.confidence,
                    raw_fragment=item.raw_fragment,
                )
                for item in items
            ]
            self.session.add_all(rows)
            self.session.commit()
            return len(rows)
        except Exception:
            self.session.rollback()
            raise
