"""Database engine — SQLite or PostgreSQL via DATABASE_URL env var."""
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./tracker.db")
# Render uses postgres:// but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, pool_pre_ping=True)
    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.close()
else:
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=300)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    import models  # noqa
    Base.metadata.create_all(bind=engine)
