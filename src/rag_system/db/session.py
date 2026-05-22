from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rag_system.config import settings

engine = create_engine(
    settings.postgres_dsn,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_pool_overflow,
    pool_recycle=settings.db_pool_recycle_seconds,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def get_session() -> Session:
    return SessionLocal()
