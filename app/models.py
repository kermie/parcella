"""
Database models for the allotment garden association management app.

Design principles:
- All tables use UUID as primary key (production-ready, no rate-guessing)
- Soft-delete where sensible (deleted_at instead of a real delete)
- Audit fields (created_at, updated_at) everywhere
"""

import uuid
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import (
    String, Integer, Boolean, Date, DateTime, Text, Numeric,
    ForeignKey, Enum as SAEnum, UniqueConstraint, Index, CheckConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import enum

from app.database import Base


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ParcelStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    TERMINATED = "TERMINATED"
    DELETED = "DELETED"


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    BOARD = "board"
    TREASURER = "treasurer"
    READONLY = "readonly"


class InvitationStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# System users (app users, not club members)
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole), default=UserRole.READONLY, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    invitations: Mapped[List["Invitation"]] = relationship("Invitation", back_populates="invited_by")

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole), default=UserRole.READONLY, nullable=False
    )
    status: Mapped[InvitationStatus] = mapped_column(
        SAEnum(InvitationStatus), default=InvitationStatus.PENDING, nullable=False
    )
    invited_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    invited_by: Mapped[Optional["User"]] = relationship("User", back_populates="invitations")


# ---------------------------------------------------------------------------
# Groups: a simple ACL layer governing what TREASURER/READONLY users can
# read/write/delete per module. ADMIN/BOARD always bypass this entirely
# (unchanged from their existing behavior) -- see app/permissions.py for
# the module list and permission-check helpers, and ADR 0038 for why.
# ---------------------------------------------------------------------------

class Group(Base):
    """
    A named group of users (e.g. "Work Hours Coordinators"). A user can
    belong to several groups at once (see GroupMembership); their
    effective permission on a module is the most permissive value
    across every group they're in (see app/permissions.py).
    """
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    permissions: Mapped[List["GroupModulePermission"]] = relationship(
        "GroupModulePermission", back_populates="group", cascade="all, delete-orphan"
    )
    memberships: Mapped[List["GroupMembership"]] = relationship(
        "GroupMembership", back_populates="group", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Group {self.name!r}>"


class GroupMembership(Base):
    """A user's membership in a group (m:n -- see Group docstring)."""
    __tablename__ = "group_memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")
    group: Mapped["Group"] = relationship("Group", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("user_id", "group_id", name="uq_group_membership"),
    )


class GroupModulePermission(Base):
    """
    What a group can do in one module: read, write, delete -- each
    independent (a group could in principle have delete without write,
    though the admin UI doesn't offer that combination since it isn't
    a meaningful real-world scenario). `module` is a key from
    app/permissions.py's MODULES list, not a DB-enforced enum, so
    adding a new governed module never needs a migration.
    """
    __tablename__ = "group_module_permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    can_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    group: Mapped["Group"] = relationship("Group", back_populates="permissions")

    __table_args__ = (
        UniqueConstraint("group_id", "module", name="uq_group_module_permission"),
    )


# ---------------------------------------------------------------------------
# Club members
# ---------------------------------------------------------------------------

class Member(Base):
    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Personal data
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Address
    street: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Bank details
    iban: Mapped[Optional[str]] = mapped_column(String(34), nullable=True)

    # Membership
    member_since: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    member_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Communication
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Notes (internal)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    phone_numbers: Mapped[List["MemberPhone"]] = relationship(
        "MemberPhone", back_populates="member", cascade="all, delete-orphan"
    )
    email_addresses: Mapped[List["MemberEmail"]] = relationship(
        "MemberEmail", back_populates="member", cascade="all, delete-orphan"
    )
    parcel_assignments: Mapped[List["MemberParcel"]] = relationship(
        "MemberParcel", back_populates="member"
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def is_active(self) -> bool:
        return self.deleted_at is None and (
            self.member_until is None or self.member_until >= date.today()
        )

    def __repr__(self) -> str:
        return f"<Member {self.full_name}>"


class MemberPhone(Base):
    """Multiple phone numbers per member."""
    __tablename__ = "member_phones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g. "Mobile", "Landline"
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    member: Mapped["Member"] = relationship("Member", back_populates="phone_numbers")


class MemberEmail(Base):
    """Multiple email addresses per member."""
    __tablename__ = "member_emails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g. "Personal", "Work"
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    member: Mapped["Member"] = relationship("Member", back_populates="email_addresses")


# ---------------------------------------------------------------------------
# Parcels
# ---------------------------------------------------------------------------

class Parcel(Base):
    __tablename__ = "parcels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Plot number (e.g. "G093", "G26/27")
    plot_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)

    # Area
    area_sqm: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Status
    status: Mapped[ParcelStatus] = mapped_column(
        SAEnum(ParcelStatus), default=ParcelStatus.ACTIVE, nullable=False
    )

    # Termination (who terminated and when)
    termination_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    member_assignments: Mapped[List["MemberParcel"]] = relationship(
        "MemberParcel", back_populates="parcel"
    )
    metering_points: Mapped[List["MeteringPoint"]] = relationship(
        "MeteringPoint", back_populates="parcel"
    )
    cloud_folders: Mapped[List["ParcelCloudFolder"]] = relationship(
        "ParcelCloudFolder", back_populates="parcel",
        order_by="ParcelCloudFolder.created_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<Parcel {self.plot_number}>"


# ---------------------------------------------------------------------------
# Assignment table Member <-> Parcel (m:n with metadata)
# ---------------------------------------------------------------------------

class MemberParcel(Base):
    """
    Connects members with parcels.
    Enables double gardens (one member, multiple parcels)
    as well as community gardens (multiple members, one parcel).
    """
    __tablename__ = "member_parcels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parcel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Is this member's address used as the parcel's invoice address?
    is_invoice_address: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Assignment period
    assigned_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    assigned_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship("Member", back_populates="parcel_assignments")
    parcel: Mapped["Parcel"] = relationship("Parcel", back_populates="member_assignments")

    __table_args__ = (
        UniqueConstraint("member_id", "parcel_id", name="uq_member_parcel"),
        # A former tenant (assigned_until set) can never be the invoice
        # address -- annual invoices must not go to someone who's moved on.
        CheckConstraint(
            "NOT is_invoice_address OR assigned_until IS NULL",
            name="ck_invoice_address_only_for_current_tenants",
        ),
    )


# ---------------------------------------------------------------------------
# Cloud storage: per-parcel folder path in a connected cloud backend
# (Nextcloud today). See app/cloud_storage.py and app/parcel_cloud_folders.py.
# ---------------------------------------------------------------------------

class ParcelCloudFolder(Base):
    """
    The cloud-storage folder path currently assigned to a parcel (e.g.
    a Nextcloud path holding the current tenants' lease paperwork).

    Scoped to the parcel, not to a single MemberParcel row: a parcel can
    have several co-tenants at once (couples, families), each with their
    own MemberParcel row for the same lease period, and this folder is
    shared by all of them -- one folder per tenancy period, not one per
    person.

    Only one row per parcel should have is_active=True at a time; older
    rows are kept (is_active=False) as history rather than deleted, same
    principle as ended MemberParcel assignments. is_active is flipped to
    False automatically when the parcel's last active resident's tenancy
    ends (see app.parcel_cloud_folders.deactivate_if_vacant) -- so a
    fresh set of tenants moving in after a full turnover never inherits
    the previous tenants' folder; a board member must deliberately set a
    new one.
    """
    __tablename__ = "parcel_cloud_folders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parcel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    set_by_user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    parcel: Mapped["Parcel"] = relationship("Parcel", back_populates="cloud_folders")
    set_by_user: Mapped[Optional["User"]] = relationship("User")

    __table_args__ = (
        Index(
            "uq_parcel_cloud_folders_one_active_per_parcel",
            "parcel_id", unique=True,
            postgresql_where=(is_active == True),  # noqa: E712
        ),
    )


# ---------------------------------------------------------------------------
# Club settings (key-value for flexibility)
# ---------------------------------------------------------------------------

class ClubSetting(Base):
    """
    Flexible settings table for club master data.
    Enables later extension without a schema change.
    """
    __tablename__ = "club_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Known keys (for documentation):
    # verein_name, verein_strasse, verein_plz, verein_ort
    # flaeche_gesamt_qm, flaeche_a_qm, flaeche_b_qm, flaeche_c_qm
    # vereinsnummer, registergericht


class SampleDataRecord(Base):
    """
    Tracks every row created by the admin "add sample data" feature
    (see app/sample_data.py), so "remove all sample data" can delete
    exactly what was generated -- never guessed by naming pattern, and
    never anything an admin entered themselves. entity_type is a model
    class name (e.g. "Member", "WorkSession"); removal looks each one
    up via a fixed model registry, not dynamic imports.
    """
    __tablename__ = "sample_data_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    module: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_sample_data_record"),
    )


# ---------------------------------------------------------------------------
# Work hours configuration (year-based)
# ---------------------------------------------------------------------------

class WorkHoursMode(str, enum.Enum):
    PER_PARCEL = "PER_PARCEL"    # Hours apply per parcel (default)
    PER_MEMBER = "PER_MEMBER"    # Hours apply per member


class WorkHoursConfiguration(Base):
    """
    Annual configuration of the required work hours.
    Historized -- old values are kept for evaluating past years.
    """
    __tablename__ = "work_hours_configuration"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    year: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    hours_required: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    rate_per_hour_eur: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    mode: Mapped[WorkHoursMode] = mapped_column(
        SAEnum(WorkHoursMode), default=WorkHoursMode.PER_PARCEL, nullable=False
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<WorkHoursConfiguration {self.year}: {self.hours_required}h @ {self.rate_per_hour_eur}€>"


# ---------------------------------------------------------------------------
# Club roles (extended board, etc.)
# ---------------------------------------------------------------------------

class ExemptionReason(str, enum.Enum):
    BOARD = "BOARD"
    EXTENDED_BOARD = "EXTENDED_BOARD"
    ILLNESS = "ILLNESS"
    AGE = "AGE"
    OTHER = "OTHER"


class ClubRole(Base):
    """
    Roles within the club (board, extended board, assessor, etc.).
    Separate from the app's user system (UserRole) -- this is about
    club offices, not access permissions.
    """
    __tablename__ = "club_roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hours_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exemption_reason: Mapped[Optional[ExemptionReason]] = mapped_column(
        SAEnum(ExemptionReason), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    assignments: Mapped[List["MemberClubRole"]] = relationship(
        "MemberClubRole", back_populates="club_role"
    )

    def __repr__(self) -> str:
        return f"<ClubRole {self.name}>"


class MemberClubRole(Base):
    """
    Assignment of Member -> ClubRole for a given year.
    The exemption always applies to the entire calendar year (even if
    the role is relinquished partway through the year).
    """
    __tablename__ = "member_club_roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    club_role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("club_roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship("Member")
    club_role: Mapped["ClubRole"] = relationship("ClubRole", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("member_id", "club_role_id", "year", name="uq_member_club_role_year"),
    )


# ---------------------------------------------------------------------------
# Sponsorships
# ---------------------------------------------------------------------------

class Sponsorship(Base):
    """
    A member takes on sponsorship of an area (e.g. hedge, playground).
    The sponsorship counts as a flat fulfillment of required work hours.
    """
    __tablename__ = "sponsorships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    area: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    credited_hours: Mapped[float] = mapped_column(
        Numeric(5, 1), nullable=False,
        comment="Flat hours credited per year"
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="NULL = still ongoing")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    member: Mapped[Optional["Member"]] = relationship("Member")

    def __repr__(self) -> str:
        return f"<Sponsorship {self.area} → {self.member_id}>"


# ---------------------------------------------------------------------------
# Work Sessions
# ---------------------------------------------------------------------------

class SessionType(str, enum.Enum):
    STANDARD = "STANDARD"    # Scheduled date, signup possible
    SPECIAL = "SPECIAL"      # Spontaneous/unplanned (painting a garden bench, etc.)


class ParticipationStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"    # Signed up
    ATTENDED = "ATTENDED"        # Showed up, hours get credited
    NO_SHOW = "NO_SHOW"          # Signed up but didn't show


class WorkSession(Base):
    """
    A scheduled or spontaneous work session in the club.
    """
    __tablename__ = "work_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[SessionType] = mapped_column(
        SAEnum(SessionType), default=SessionType.STANDARD, nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    time_from: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)   # "08:00"
    time_until: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # "12:00"
    max_participants: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hours_per_participant: Mapped[Optional[float]] = mapped_column(
        Numeric(4, 1), nullable=True,
        comment="Default value; can be overridden per participation"
    )
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    participations: Mapped[List["SessionParticipation"]] = relationship(
        "SessionParticipation", back_populates="session", cascade="all, delete-orphan"
    )
    created_by: Mapped[Optional["User"]] = relationship("User")

    @property
    def available_spots(self) -> Optional[int]:
        if self.max_participants is None:
            return None
        registered = sum(1 for t in self.participations if t.status != ParticipationStatus.NO_SHOW)
        return max(0, self.max_participants - registered)

    def __repr__(self) -> str:
        return f"<WorkSession {self.date} {self.title}>"


class SessionParticipation(Base):
    """
    A member's participation in a work session.
    """
    __tablename__ = "session_participations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("work_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ParticipationStatus] = mapped_column(
        SAEnum(ParticipationStatus), default=ParticipationStatus.REGISTERED, nullable=False
    )
    hours_completed: Mapped[Optional[float]] = mapped_column(
        Numeric(4, 1), nullable=True,
        comment="Overrides the session's hours_per_participant when set"
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["WorkSession"] = relationship("WorkSession", back_populates="participations")
    member: Mapped["Member"] = relationship("Member")

    __table_args__ = (
        UniqueConstraint("session_id", "member_id", name="uq_session_member"),
    )


class TaskWorkload(str, enum.Enum):
    """How physically demanding a task is. This exists so whoever is
    coordinating a work session can match tasks to the people who signed
    up for it -- the app itself never stores or infers anything about a
    member's health, age, or ability; that judgment call stays entirely
    with the human coordinator, who knows the people involved."""
    LIGHT = "LIGHT"
    MODERATE = "MODERATE"
    DEMANDING = "DEMANDING"


class WorkTask(Base):
    """
    A task for the work-hours program: something that needs doing,
    optionally scheduled to a specific work session, and optionally
    assigned to one specific person who signed up for that session.

    Deliberately a three-stage lifecycle, each stage optional:
    1. Backlog: session_id is NULL -- "things we know need doing,"
       not yet tied to a specific date.
    2. Scheduled: session_id is set, assigned_participation_id is NULL --
       this session will cover the task, but no specific person yet.
    3. Assigned: assigned_participation_id is set -- one specific
       signed-up participant is doing this specific task.
    """
    __tablename__ = "work_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    workload: Mapped[TaskWorkload] = mapped_column(
        SAEnum(TaskWorkload), default=TaskWorkload.MODERATE, nullable=False
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("work_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_participation_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("session_participations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[Optional["WorkSession"]] = relationship("WorkSession")
    assigned_participation: Mapped[Optional["SessionParticipation"]] = relationship("SessionParticipation")
    created_by: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return f"<WorkTask {self.title!r}>"




# ---------------------------------------------------------------------------
# Change history (generic audit log for field changes)
# ---------------------------------------------------------------------------

class ChangeHistory(Base):
    """
    Generic audit log: logs field changes on arbitrary entities (e.g.
    Parcel.area_sqm). Enables traceability without needing a separate
    history table for every table.
    """
    __tablename__ = "change_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    changed_by: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return f"<ChangeHistory {self.entity_type}:{self.entity_id} {self.field_name}>"



# ---------------------------------------------------------------------------
# Metering: generic module for water AND electricity meters
#
# A MeteringPoint has a "medium" (WATER or ELECTRICITY) and a "type"
# (main meter, parcel, club connection). The consumption logic is
# identical for both media -- only unit, display rounding, and icon
# differ (see app/routers/metering.py).
# ---------------------------------------------------------------------------

class MeteringMedium(str, enum.Enum):
    WATER = "WATER"
    ELECTRICITY = "ELECTRICITY"


class MeteringPointType(str, enum.Enum):
    MAIN_METER = "MAIN_METER"  # Handover point from the public utility
    PARCEL = "PARCEL"          # Connection at a parcel
    CLUB = "CLUB"              # Club-owned connection point (clubhouse, wash area, etc.)


class MeteringPoint(Base):
    """
    A metering point for a medium (water or electricity). Either
    coupled to a parcel, a club-owned connection point, or the main
    meter for the overall supply from the public utility.

    A parcel can have both a water and an electricity MeteringPoint
    (two rows, distinguished via "medium").
    """
    __tablename__ = "metering_points"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    medium: Mapped[MeteringMedium] = mapped_column(SAEnum(MeteringMedium), nullable=False)
    type: Mapped[MeteringPointType] = mapped_column(SAEnum(MeteringPointType), nullable=False)

    parcel_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("parcels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # For MAIN_METER/CLUB metering points (no parcel): free-form name.
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    parcel: Mapped[Optional["Parcel"]] = relationship("Parcel", back_populates="metering_points")
    meters: Mapped[List["Meter"]] = relationship(
        "Meter", back_populates="metering_point", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        if self.parcel:
            return f"Parcel {self.parcel.plot_number}"
        return self.label or "Unbenannter Zählpunkt"

    @property
    def current_meter(self) -> Optional["Meter"]:
        active = [z for z in self.meters if z.is_active]
        return active[0] if active else None

    def __repr__(self) -> str:
        return f"<MeteringPoint {self.medium.value}:{self.display_name}>"


class Meter(Base):
    """
    The physical meter (water meter or electricity meter) on a
    MeteringPoint. When swapped, the old meter is deactivated
    (removed_at set) and a new one with a new number is created -- the
    history stays fully intact.
    """
    __tablename__ = "meters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    metering_point_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("metering_points.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    calibrated_until: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Year until which calibration is valid (water typically +6, electricity typically +8 years)"
    )
    installed_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    removed_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="NULL = still installed")
    initial_reading: Mapped[float] = mapped_column(Numeric(12, 1), default=0, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    metering_point: Mapped["MeteringPoint"] = relationship("MeteringPoint", back_populates="meters")
    readings: Mapped[List["MeterReading"]] = relationship(
        "MeterReading", back_populates="meter", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Meter {self.number}>"


class MeterReading(Base):
    """
    An annual reading of a meter (water or electricity).
    """
    __tablename__ = "meter_readings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    meter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("meters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    reading: Mapped[float] = mapped_column(Numeric(12, 1), nullable=False)
    recorded_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    meter: Mapped["Meter"] = relationship("Meter", back_populates="readings")
    recorded_by: Mapped[Optional["User"]] = relationship("User")

    __table_args__ = (
        UniqueConstraint("meter_id", "year", name="uq_meter_year"),
    )

# ---------------------------------------------------------------------------
# Insurance module: property and accident insurance per parcel
# ---------------------------------------------------------------------------

class PropertyInsurancePackage(Base):
    """
    A selectable property insurance package for a given year (e.g.
    "Package 1" = 40 €, "Package 2" = 60 € etc.). The number and
    amounts of packages are freely configurable and can change yearly.
    """
    __tablename__ = "property_insurance_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_eur: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<PropertyInsurancePackage {self.year} {self.name}: {self.amount_eur}€>"


class InsuranceConfiguration(Base):
    """
    Annual configuration of the accident insurance amounts. Property
    insurance is configured separately via PropertyInsurancePackage
    (several packages per year); accident insurance has exactly one
    base and additional amount per year.
    """
    __tablename__ = "insurance_configuration"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    year: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    accident_base_amount_eur: Mapped[float] = mapped_column(
        Numeric(8, 2), nullable=False,
        comment="Covers all members in the same household (same address)"
    )
    accident_additional_amount_eur: Mapped[float] = mapped_column(
        Numeric(8, 2), nullable=False,
        comment="Per additional co-insured person outside the household"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<InsuranceConfiguration {self.year}>"


class ParcelInsurance(Base):
    """
    A parcel's insurance status for a given year: property insurance
    (optional, with a chosen package) and accident insurance (optional,
    the base amount covers the automatically detected household --
    see household_grouping() in app/insurance_utils.py).
    """
    __tablename__ = "parcel_insurance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parcel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)

    has_property_insurance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    property_package_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("property_insurance_packages.id", ondelete="SET NULL"), nullable=True
    )

    has_accident_insurance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    parcel: Mapped["Parcel"] = relationship("Parcel")
    property_package: Mapped[Optional["PropertyInsurancePackage"]] = relationship("PropertyInsurancePackage")
    additional_persons: Mapped[List["AccidentInsuranceAdditionalPerson"]] = relationship(
        "AccidentInsuranceAdditionalPerson", back_populates="parcel_insurance", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("parcel_id", "year", name="uq_parcel_insurance_year"),
    )

    def __repr__(self) -> str:
        return f"<ParcelInsurance {self.parcel_id} {self.year}>"


class AccidentInsuranceAdditionalPerson(Base):
    """
    A member added to a parcel's accident insurance for an extra fee,
    in addition to the automatically detected household (e.g. a
    resident who doesn't live at the same address as the parcel's
    other residents).
    """
    __tablename__ = "accident_insurance_additional_persons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parcel_insurance_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parcel_insurance.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    parcel_insurance: Mapped["ParcelInsurance"] = relationship(
        "ParcelInsurance", back_populates="additional_persons"
    )
    member: Mapped["Member"] = relationship("Member")

    __table_args__ = (
        UniqueConstraint("parcel_insurance_id", "member_id", name="uq_insurance_member"),
    )


# ---------------------------------------------------------------------------
# Ticket system
# ---------------------------------------------------------------------------

class TicketStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ASSIGNED = "ASSIGNED"
    WAITING = "WAITING"        # waiting for the sender's reply
    POSTPONED = "POSTPONED"    # postponed until postponed_until
    CLOSED = "CLOSED"
    DELETED = "DELETED"        # soft-delete, like Member.deleted_at


class MessageDirection(str, enum.Enum):
    INCOMING = "INCOMING"   # From the sender/customer (later by email, stage 1: manual)
    OUTGOING = "OUTGOING"   # A user's reply (later sent as email, stage 2)
    INTERNAL = "INTERNAL"   # Internal note, never sent to the sender


class Ticket(Base):
    """
    A support ticket = one concern from a sender. In stage 2, tickets
    are created automatically from incoming emails; in stage 1 they can
    be created manually to test the basic scaffolding.
    """
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus), default=TicketStatus.ACTIVE, nullable=False, index=True
    )
    assigned_to_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    postponed_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Automatic matching via sender email; overridable/manually
    # correctable if the address belongs to multiple members or is unknown.
    member_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sender_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Preparation for stage 3 (spam interface): fields already exist so
    # no further migration is needed later. The actual check is a no-op
    # in stage 1 (see app/spam_filter.py).
    spam_suspected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spam_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    spam_reasoning: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Traceable reasoning for why it was flagged as spam (transparency)"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    assigned_to: Mapped[Optional["User"]] = relationship("User")
    member: Mapped[Optional["Member"]] = relationship("Member")
    messages: Mapped[List["TicketMessage"]] = relationship(
        "TicketMessage", back_populates="ticket", cascade="all, delete-orphan",
        order_by="TicketMessage.created_at",
    )

    @property
    def is_due(self) -> bool:
        """True if a postponed ticket has reached its date and should be
        treated as active again. The actual status change (POSTPONED ->
        ACTIVE/ASSIGNED) happens lazily on the next load of the ticket
        list (see _reaktiviere_faellige_tickets in
        app/routers/tickets.py), not via a background job -- this
        property is just the pure calculation, in case it's needed
        elsewhere (e.g. a badge display).
        """
        if self.status != TicketStatus.POSTPONED:
            return False
        return self.postponed_until is not None and self.postponed_until <= date.today()

    def __repr__(self) -> str:
        return f"<Ticket {self.subject!r} ({self.status.value})>"


class TicketMessage(Base):
    """A single message within a ticket's history."""
    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[MessageDirection] = mapped_column(SAEnum(MessageDirection), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Only set for INCOMING messages whose email had a text/html part --
    # already sanitized (see app/html_sanitizer.py) BEFORE it's stored
    # here, so it can be safely rendered with {{ ... | safe }}.
    # `content` always remains the plain-text version alongside it
    # (search, notifications, fallback display if no HTML part existed).
    content_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    authored_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # For email threading (stage 2): this message's Message-ID, or the
    # Message-ID it's replying to. Enables matching incoming replies to
    # the right ticket instead of just guessing from the subject.
    message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="messages")
    authored_by: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return f"<TicketMessage {self.direction.value} @ {self.ticket_id}>"


# ---------------------------------------------------------------------------
# Purchase Requests (four-eyes principle for club expenses)
# ---------------------------------------------------------------------------

class PurchaseRequestStatus(str, enum.Enum):
    OPEN = "OPEN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PurchaseRequest(Base):
    """
    A request for a club expense. Must be approved by two different
    board members before the purchase may proceed -- the requester
    themselves doesn't count as an approver (four-eyes principle).
    """
    __tablename__ = "purchase_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    estimated_cost_eur: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    status: Mapped[PurchaseRequestStatus] = mapped_column(
        SAEnum(PurchaseRequestStatus), default=PurchaseRequestStatus.OPEN, nullable=False, index=True
    )

    # Requester: either a system user (requested_by_id) OR an external
    # person without a login (requester_name/-email), e.g. when the
    # board creates a request on someone else's behalf.
    requested_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requester_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    requester_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Deep-link confirmation by the (external) requester
    confirmation_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    confirmed_by_requester: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed_by_requester_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejected_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    requested_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[requested_by_id])
    created_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_id])
    rejected_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[rejected_by_id])
    approvals: Mapped[List["PurchaseRequestApproval"]] = relationship(
        "PurchaseRequestApproval", back_populates="purchase_request", cascade="all, delete-orphan"
    )

    @property
    def requester_display_name(self) -> str:
        if self.requested_by:
            return self.requested_by.name
        return self.requester_name or self.requester_email or "Unbekannt"

    @property
    def approval_count(self) -> int:
        return len(self.approvals)

    def __repr__(self) -> str:
        return f"<PurchaseRequest {self.title!r} ({self.status.value})>"


class PurchaseRequestApproval(Base):
    """A single approval by a board member for a PurchaseRequest."""
    __tablename__ = "purchase_request_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    purchase_request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("purchase_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    purchase_request: Mapped["PurchaseRequest"] = relationship("PurchaseRequest", back_populates="approvals")
    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        UniqueConstraint("purchase_request_id", "user_id", name="uq_purchase_request_approval"),
    )


class CalendarEventType(str, enum.Enum):
    """What kind of manually-created community calendar entry this is.
    Work sessions are NOT part of this enum -- they already have their
    own date/time on the WorkSession model, and the community calendar
    reads them directly rather than duplicating them into a second table
    (see docs/module-calendar.md for the reasoning)."""
    MEMBER_MEETING = "MEMBER_MEETING"
    PARCEL_INSPECTION = "PARCEL_INSPECTION"
    OTHER = "OTHER"


class CalendarEvent(Base):
    """
    A manually-created entry on the community calendar: a full-member
    meeting, a parcel inspection by the board, or anything else worth
    announcing. Deliberately separate from WorkSession -- work sessions
    already have their own date/time and are merged into the community
    calendar view/ICS feed at read time instead of being copied here.
    """
    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    event_type: Mapped[CalendarEventType] = mapped_column(
        SAEnum(CalendarEventType), default=CalendarEventType.OTHER, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # "HH:MM"
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    created_by: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        return f"<CalendarEvent {self.title!r} on {self.start_date}>"


class CouncilPresence(Base):
    """
    A scheduled slot where a specific board/council member will be
    on-site (e.g. office hours for members with questions). One row per
    person per slot -- if two council members cover the same time
    together, that's two rows.
    """
    __tablename__ = "council_presence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    time_from: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    time_until: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"<CouncilPresence {self.user_id} on {self.date}>"


class CouncilAbsence(Base):
    """
    A self-reported absence period (e.g. vacation) for anyone with a
    system account -- not restricted to the board/council despite the
    name, matching the original request that "everybody with access to
    the system" can log their own absence. Named for its primary use
    case (knowing when a council member is unreachable), not as an
    access restriction.
    """
    __tablename__ = "council_absence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"<CouncilAbsence {self.user_id} {self.start_date}-{self.end_date}>"


class AnnouncementStatus(str, enum.Enum):
    """Lifecycle of an announcement itself (not of any individual channel
    delivery -- see AnnouncementDelivery for per-channel state)."""
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class AnnouncementChannel(str, enum.Enum):
    BLOG = "BLOG"
    EMAIL = "EMAIL"
    PRINT = "PRINT"


class AnnouncementDeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class Announcement(Base):
    """
    A single piece of club news/information, authored once here and
    delivered out to up to three channels (blog draft via CMS API,
    member email, printable PDF one-pager). See docs/module-announcements.md
    for the overall design.

    body_markdown is the single canonical source text, used as-is for
    both the blog draft and the email (same container, per product
    decision -- no separate email override). body_html is derived from
    it at save time (markdown -> HTML -> sanitized) and cached here so
    it isn't re-rendered on every read; it is NOT hand-edited directly.

    print_text_override is the one deliberate exception: nullable
    Markdown that, when set, is used for the PDF instead of
    body_markdown. It starts empty (full text is used), gets
    auto-filled with a shortened version if the full text doesn't fit
    on one printed page, and remains freely hand-editable afterward --
    it is a real editable field, not just a computed preview.
    """
    __tablename__ = "announcements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    print_text_override: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[AnnouncementStatus] = mapped_column(
        SAEnum(AnnouncementStatus), default=AnnouncementStatus.DRAFT, nullable=False
    )
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    created_by: Mapped[Optional["User"]] = relationship("User")
    deliveries: Mapped[List["AnnouncementDelivery"]] = relationship(
        "AnnouncementDelivery", back_populates="announcement", cascade="all, delete-orphan"
    )

    @property
    def image_url(self) -> Optional[str]:
        # Must match UPLOAD_DIR in app/routers/announcements.py
        # ("app/static/uploads/announcements/") -- unlike the singleton
        # club logo (served straight from /static/uploads/), each
        # announcement's image lives in its own subfolder.
        return f"/static/uploads/announcements/{self.image_filename}" if self.image_filename else None

    def delivery_for(self, channel: "AnnouncementChannel") -> Optional["AnnouncementDelivery"]:
        return next((d for d in self.deliveries if d.channel == channel), None)

    def __repr__(self) -> str:
        return f"<Announcement {self.title!r} ({self.status.value})>"


class AnnouncementDelivery(Base):
    """
    Tracks one channel's delivery attempt for an announcement. One row
    per (announcement, channel) -- upserted, not appended, so retrying a
    failed send updates the existing row rather than creating a history
    of attempts.

    external_reference holds a human-facing pointer for the admin UI --
    currently only used by BLOG, where it's the WordPress **edit** URL
    (wp-admin/post.php?post=...&action=edit), not a public one, since a
    draft doesn't have a public URL yet.

    external_id holds a raw external identifier for re-querying the
    system of record later, rather than trusting a value cached at
    delivery time -- currently only used by BLOG, storing the
    WordPress post ID as a string. The PRINT channel uses this to ask
    WordPress directly, at PDF-generation time, whether the post has
    since been published and what its current public URL is, rather
    than storing (and risking a stale) public URL here on the BLOG
    delivery row itself.
    """
    __tablename__ = "announcement_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    announcement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[AnnouncementChannel] = mapped_column(SAEnum(AnnouncementChannel), nullable=False)
    status: Mapped[AnnouncementDeliveryStatus] = mapped_column(
        SAEnum(AnnouncementDeliveryStatus), default=AnnouncementDeliveryStatus.PENDING, nullable=False
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Despite the name, this doubles as a general status-detail field,
    # not strictly an error: while SENDING it holds a progress note
    # ("12 of 800 sent so far"), and on a partial SENT it holds the
    # failure count. It's only ever a true error message when the
    # status is FAILED. Kept as one field rather than adding a second
    # column, since only one of these is ever relevant at a time.
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    announcement: Mapped["Announcement"] = relationship("Announcement", back_populates="deliveries")

    __table_args__ = (
        UniqueConstraint("announcement_id", "channel", name="uq_announcement_delivery_channel"),
    )

    def __repr__(self) -> str:
        return f"<AnnouncementDelivery {self.announcement_id} {self.channel.value} ({self.status.value})>"


# ---------------------------------------------------------------------------
# Inventory module: what the club owns (and what members store on club
# property), plus a lending system for borrowable items.
# ---------------------------------------------------------------------------

class InventoryOwnerType(str, enum.Enum):
    CLUB = "CLUB"
    MEMBER = "MEMBER"


class InventoryCategory(Base):
    """
    A freely-created grouping for inventory items (e.g. "Playground",
    "Fences", "Locks & Keys", "Water Infrastructure") -- deliberately
    NOT a fixed enum, since the original request was explicit that
    these need to be configurable by the club itself, not by a code
    change. Same lookup-table shape as ClubRole.
    """
    __tablename__ = "inventory_categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items: Mapped[List["InventoryItem"]] = relationship("InventoryItem", back_populates="category")

    def __repr__(self) -> str:
        return f"<InventoryCategory {self.name!r}>"


class InventoryItem(Base):
    """
    A thing the club owns, or a thing a member owns personally but
    stores on club property (see owner_type/owner_member_id) -- both
    get the same financial fields (purchase price, current value,
    replacement cost), per explicit product decision: personally-owned
    items stored here are still useful to have on record for
    insurance/liability purposes, not just club-owned assets.

    quantity_total is how many physical units of this item exist (e.g.
    "3" for three wheelbarrows bought together and tracked as one
    entry rather than three separate rows) -- see
    available_quantity/is_available below for how loans reduce this.

    retired_at marks an item as no longer owned/in service without
    deleting it -- financial and loan history for a real asset
    register needs to survive disposal, not disappear with it. A
    genuinely mistaken entry can still be hard-deleted; retirement is
    for "we sold/scrapped/lost this," not for undoing data-entry
    errors.
    """
    __tablename__ = "inventory_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    category_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("inventory_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owner_type: Mapped[InventoryOwnerType] = mapped_column(
        SAEnum(InventoryOwnerType), default=InventoryOwnerType.CLUB, nullable=False
    )
    owner_member_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="SET NULL"), nullable=True,
        comment="Set only when owner_type = MEMBER"
    )

    storage_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    current_value: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 2), nullable=True,
        comment="Manually entered/updated -- no automatic depreciation calculation, by design"
    )
    current_value_updated_at: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, comment="When current_value was last checked/updated, so staleness is visible"
    )
    replacement_cost: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    quantity_total: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_borrowable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_loan_fee: Mapped[Optional[float]] = mapped_column(
        Numeric(8, 2), nullable=True, comment="Suggested fee when checking this item out; editable per loan"
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    category: Mapped[Optional["InventoryCategory"]] = relationship("InventoryCategory", back_populates="items")
    owner_member: Mapped[Optional["Member"]] = relationship("Member", foreign_keys=[owner_member_id])
    created_by: Mapped[Optional["User"]] = relationship("User")
    loans: Mapped[List["ItemLoan"]] = relationship(
        "ItemLoan", back_populates="item", cascade="all, delete-orphan"
    )

    @property
    def quantity_on_loan(self) -> int:
        return sum(loan.quantity for loan in self.loans if loan.returned_date is None)

    @property
    def available_quantity(self) -> int:
        return max(0, self.quantity_total - self.quantity_on_loan)

    def __repr__(self) -> str:
        return f"<InventoryItem {self.name!r}>"


class ItemLoan(Base):
    """
    One borrowing of (some quantity of) an item by a member.
    returned_date IS NULL means it's still checked out. quantity lets
    one loan cover more than one unit of an item at once (e.g.
    borrowing 2 of the club's 5 tents for a weekend).
    """
    __tablename__ = "item_loans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    borrowed_date: Mapped[date] = mapped_column(Date, nullable=False)
    returned_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    fee_charged: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    item: Mapped["InventoryItem"] = relationship("InventoryItem", back_populates="loans")
    member: Mapped["Member"] = relationship("Member")
    created_by: Mapped[Optional["User"]] = relationship("User")

    def __repr__(self) -> str:
        status = "returned" if self.returned_date else "outstanding"
        return f"<ItemLoan {self.item_id} x{self.quantity} ({status})>"


# ---------------------------------------------------------------------------
# Task board: general-purpose kanban for club business (not tied to a work
# session -- see WorkTask above for that). Admin/board only, per explicit
# product decision -- this is internal club-business tracking, not
# something every member needs visibility into. Fixed three-column
# workflow (TODO/IN_PROGRESS/DONE); no per-club column configuration in v1.
# ---------------------------------------------------------------------------

class TaskStatus(str, enum.Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class Task(Base):
    """
    A single kanban card. `position` orders cards within their column
    (0-based, no gaps) and is fully rewritten for the affected column(s)
    on every create/move/delete -- simple and correct at the card counts
    a club's task board will ever realistically have, avoids the
    fractional-position bookkeeping a high-write-volume board would need.
    """
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus), default=TaskStatus.TODO, nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    assigned_to_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    assigned_to: Mapped[Optional["User"]] = relationship("User", foreign_keys=[assigned_to_id])
    created_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<Task {self.title!r} ({self.status.value})>"
