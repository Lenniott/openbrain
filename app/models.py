import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from .db import Base


class Inbox(Base):
    __tablename__ = "ob_inbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_text = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    type = Column(String, nullable=True)
    fields = Column(JSONB, nullable=True)
    status = Column(String, nullable=False, default="pending")
    confidence = Column(Float, nullable=True)
    session_id = Column(String, nullable=True)
    filename = Column(String, nullable=True)
    filetype = Column(String, nullable=True)
    verified = Column(Boolean, nullable=False, default=False)
    isTemplate = Column(Boolean, nullable=False, default=False)
    isGenerated = Column(Boolean, nullable=False, default=False)
    vectorised = Column(Boolean, nullable=False, default=False)
    vectorised_at = Column(DateTime(timezone=True), nullable=True)
    retrieval_count = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=0)
    last_surfaced = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    vectors = relationship("Vector", back_populates="inbox", cascade="all, delete-orphan")


class Vector(Base):
    __tablename__ = "ob_vectors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inbox_id = Column(UUID(as_uuid=True), ForeignKey("ob_inbox.id", ondelete="CASCADE"))
    chunk_index = Column(Integer, nullable=True)
    chunk_text = Column(Text, nullable=True)
    retrieval_count = Column(Integer, nullable=False, default=0)
    last_surfaced = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    inbox = relationship("Inbox", back_populates="vectors")

