from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, declarative_base

from .config import settings


DATABASE_URL = (
    f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    """
    FastAPI dependency that yields a SQLAlchemy session.
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

