from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    DateTime,
    Boolean,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


def utcnow():
    # helper so SQLAlchemy gets a callable
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)

    active_project_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    currency = Column(String, default="GBP")
    is_archived = Column(Boolean, default=False)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Room(Base):
    __tablename__ = "rooms"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    name = Column(String, nullable=False)
    floor = Column(String, nullable=True)
    status = Column(String, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_room_project_name"),
    )


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    room_id = Column(String, ForeignKey("rooms.id"), nullable=True)

    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    due_date = Column(Date, nullable=True)
    priority = Column(Integer, default=3)
    status = Column(String, default="todo")  # todo/doing/blocked/done

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    room_id = Column(String, ForeignKey("rooms.id"), nullable=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=True)

    purchase_date = Column(Date, nullable=False)
    gross_amount = Column(Numeric(12, 2), nullable=False)
    description = Column(String, nullable=False)

    vat_rate = Column(Numeric(6, 3), nullable=True)
    vat_amount = Column(Numeric(12, 2), nullable=True)
    net_amount = Column(Numeric(12, 2), nullable=True)

    payment_method = Column(String, nullable=True)
    vendor = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    # FIX: DB has NOT NULL is_refund
    is_refund = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)

    room_id = Column(String, ForeignKey("rooms.id"), nullable=True)
    expense_id = Column(String, ForeignKey("expenses.id"), nullable=True)

    # doc links to MANY tasks via document_tasks
    doc_type = Column(String, default="receipt")  # receipt/photo/warranty/paperwork/recipe
    photo_group = Column(String, nullable=True)   # before/during/after (photo only)

    title = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    # comma-separated list, e.g. "kitchen,plaster,invoice"
    tags = Column(Text, nullable=True)

    original_filename = Column(String, nullable=True)
    content_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    s3_key = Column(String, nullable=False)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    tasks = relationship("Task", secondary="document_tasks", backref="documents")


class DocumentTask(Base):
    __tablename__ = "document_tasks"

    document_id = Column(String, ForeignKey("documents.id"), primary_key=True)
    task_id = Column(String, ForeignKey("tasks.id"), primary_key=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
