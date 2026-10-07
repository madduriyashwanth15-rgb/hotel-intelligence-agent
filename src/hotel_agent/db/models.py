"""Core hotel operational schema (SQLAlchemy 2 typed ORM).

Enum-like columns are VARCHAR + CHECK constraints rather than native PostgreSQL
enums: adding a value later is a one-line constraint change instead of an
ALTER TYPE migration.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class HousekeepingStatus(StrEnum):
    CLEAN = "clean"
    DIRTY = "dirty"
    INSPECTED = "inspected"
    OUT_OF_ORDER = "out_of_order"


class LoyaltyTier(StrEnum):
    NONE = "none"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


class ReservationStatus(StrEnum):
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class BookingChannel(StrEnum):
    DIRECT = "direct"
    OTA = "ota"
    CORPORATE = "corporate"
    TRAVEL_AGENT = "travel_agent"
    WALK_IN = "walk_in"


class ChargeCategory(StrEnum):
    ROOM = "room"
    FOOD_BEVERAGE = "food_beverage"
    SPA = "spa"
    MINIBAR = "minibar"
    LAUNDRY = "laundry"


class TaskType(StrEnum):
    CLEANING = "cleaning"
    TURNDOWN = "turndown"
    DEEP_CLEAN = "deep_clean"
    INSPECTION = "inspection"
    MAINTENANCE = "maintenance"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


def enum_check(column: str, enum: type[StrEnum]) -> str:
    """SQL expression restricting `column` to the enum's values."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


# Statuses that occupy a room for the stay's dates.
ACTIVE_RESERVATION_STATUSES = (
    ReservationStatus.CONFIRMED,
    ReservationStatus.CHECKED_IN,
    ReservationStatus.CHECKED_OUT,
)


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100))
    country_code: Mapped[str] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoomType(Base):
    __tablename__ = "room_types"
    __table_args__ = (
        UniqueConstraint("hotel_id", "code", name="uq_room_types_hotel_id_code"),
        CheckConstraint("max_occupancy > 0", name="max_occupancy_positive"),
        CheckConstraint("base_rate > 0", name="base_rate_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    max_occupancy: Mapped[int] = mapped_column(SmallInteger)
    base_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint("hotel_id", "room_number", name="uq_rooms_hotel_id_room_number"),
        CheckConstraint(
            enum_check("housekeeping_status", HousekeepingStatus), name="housekeeping_status_valid"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    room_type_id: Mapped[int] = mapped_column(ForeignKey("room_types.id"), index=True)
    room_number: Mapped[str] = mapped_column(String(10))
    floor: Mapped[int] = mapped_column(SmallInteger)
    housekeeping_status: Mapped[str] = mapped_column(String(20))
    status_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Guest(Base):
    __tablename__ = "guests"
    __table_args__ = (
        CheckConstraint(enum_check("loyalty_tier", LoyaltyTier), name="loyalty_tier_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    phone: Mapped[str] = mapped_column(String(32))
    country_code: Mapped[str] = mapped_column(String(2))
    loyalty_tier: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


_ACTIVE_STATUS_SQL = ", ".join(f"'{s.value}'" for s in ACTIVE_RESERVATION_STATUSES)


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("check_out_date > check_in_date", name="stay_dates_ordered"),
        CheckConstraint("adults >= 1", name="adults_positive"),
        CheckConstraint("children >= 0", name="children_non_negative"),
        CheckConstraint("rate_per_night >= 0", name="rate_non_negative"),
        CheckConstraint("total_amount >= 0", name="total_non_negative"),
        CheckConstraint(enum_check("status", ReservationStatus), name="status_valid"),
        CheckConstraint(enum_check("channel", BookingChannel), name="channel_valid"),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="cancelled_at_matches_status",
        ),
        # A room can never be double-booked for overlapping nights.
        ExcludeConstraint(
            ("room_id", "="),
            (text("daterange(check_in_date, check_out_date)"), "&&"),
            name="ex_reservations_no_room_overlap",
            using="gist",
            where=text(f"room_id IS NOT NULL AND status IN ({_ACTIVE_STATUS_SQL})"),
        ),
        Index("ix_reservations_stay_dates", "check_in_date", "check_out_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    confirmation_code: Mapped[str] = mapped_column(String(16), unique=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    guest_id: Mapped[int] = mapped_column(ForeignKey("guests.id"), index=True)
    room_type_id: Mapped[int] = mapped_column(ForeignKey("room_types.id"), index=True)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), index=True)
    check_in_date: Mapped[date] = mapped_column(Date)
    check_out_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    adults: Mapped[int] = mapped_column(SmallInteger)
    children: Mapped[int] = mapped_column(SmallInteger)
    rate_per_night: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FolioCharge(Base):
    """A posted charge on a reservation's folio; revenue is the sum of these."""

    __tablename__ = "folio_charges"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint(enum_check("category", ChargeCategory), name="category_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reservation_id: Mapped[int] = mapped_column(
        ForeignKey("reservations.id", ondelete="CASCADE"), index=True
    )
    charge_date: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class HousekeepingTask(Base):
    __tablename__ = "housekeeping_tasks"
    __table_args__ = (
        CheckConstraint(enum_check("task_type", TaskType), name="task_type_valid"),
        CheckConstraint(enum_check("priority", TaskPriority), name="priority_valid"),
        CheckConstraint(enum_check("status", TaskStatus), name="status_valid"),
        CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)",
            name="completed_at_matches_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(20))
    priority: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetMetadata(Base):
    """Key/value provenance for loaded data (e.g. synthetic=true, seed, generator version)."""

    __tablename__ = "dataset_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# --- Knowledge base (Phase 2, ADR-0011/0012) ---

KB_EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5 (ADR-0009)
KB_CATEGORIES = (
    "cancellation",
    "policy",
    "sop",
    "room_info",
    "fnb",
    "emergency",
    "service_recovery",
)
_KB_CATEGORY_SQL = ", ".join(f"'{c}'" for c in KB_CATEGORIES)


class KbDocument(Base):
    """One source document of the hotel knowledge base."""

    __tablename__ = "kb_documents"
    __table_args__ = (
        CheckConstraint(f"category IN ({_KB_CATEGORY_SQL})", name="category_valid"),
        # Only synthetic documents may be stored (no real hotel/customer content).
        CheckConstraint("is_synthetic", name="synthetic_only"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_key: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(32))
    effective_date: Mapped[date] = mapped_column(Date)
    source_path: Mapped[str] = mapped_column(String(500))
    content_sha256: Mapped[str] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KbChunk(Base):
    """A retrievable, embedded passage of a knowledge-base document."""

    __tablename__ = "kb_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_kb_chunks_document_id_chunk_index"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        Index("ix_kb_chunks_search_tsv", "search_tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector(KB_EMBEDDING_DIM))
    embedding_model: Mapped[str] = mapped_column(String(100))
    # Section headings weigh more than body text in keyword search.
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', section_path), 'A') || "
            "setweight(to_tsvector('english', content), 'B')",
            persisted=True,
        ),
    )


# --- Operations: staff, approvals, refunds (Phase 4, ADR-0019..0021) ---


class StaffRole(StrEnum):
    FRONT_DESK = "front_desk"
    HOUSEKEEPING = "housekeeping"
    HOUSEKEEPING_SUPERVISOR = "housekeeping_supervisor"
    FRONT_OFFICE_MANAGER = "front_office_manager"
    DUTY_MANAGER = "duty_manager"
    GENERAL_MANAGER = "general_manager"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class StaffMember(Base):
    """Synthetic staff principal (no real people)."""

    __tablename__ = "staff_members"
    __table_args__ = (CheckConstraint(enum_check("role", StaffRole), name="role_valid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    staff_code: Mapped[str] = mapped_column(String(16), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean)


class ApprovalRequest(Base):
    """A mutating tool call waiting for (or decided by) a human approver."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(enum_check("status", ApprovalStatus), name="status_valid"),
        # Four-eyes: nobody approves their own request.
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by", name="approver_not_requester"
        ),
        CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name="decided_iff_closed"),
        CheckConstraint(
            "status NOT IN ('executed', 'failed', 'rejected') OR decided_by IS NOT NULL",
            name="decision_has_approver",
        ),
        CheckConstraint(
            "(status IN ('executed', 'failed')) = (executed_at IS NOT NULL)",
            name="executed_iff_run",
        ),
        CheckConstraint("expires_at > requested_at", name="expiry_after_request"),
        Index("ix_approval_requests_hotel_status", "hotel_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"))
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, object]] = mapped_column(JSONB)
    arguments_sha256: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[int] = mapped_column(ForeignKey("staff_members.id"), index=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("staff_members.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class Refund(Base):
    """Simulated refund ledger entry (no payment is made). One per reservation."""

    __tablename__ = "refunds"
    __table_args__ = (CheckConstraint("amount >= 0", name="amount_non_negative"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id", ondelete="CASCADE"), index=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), unique=True)
    approval_request_id: Mapped[int] = mapped_column(
        ForeignKey("approval_requests.id"), unique=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    policy_rule: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
