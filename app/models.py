"""
Datenbankmodelle für die Gartenverein-Verwaltung.

Designprinzipien:
- Alle Tabellen haben UUID als Primärschlüssel (produktionsreif, kein Rate-Guessing)
- Soft-Delete wo sinnvoll (deleted_at statt echtem Löschen)
- Audit-Felder (created_at, updated_at) überall
"""

import uuid
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import (
    String, Integer, Boolean, Date, DateTime, Text, Numeric,
    ForeignKey, Enum as SAEnum, UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import enum

from app.database import Base


# ---------------------------------------------------------------------------
# Hilfsfunktionen
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
# Systembenutzer (Anwendungsnutzer, nicht Vereinsmitglieder)
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

    # Beziehungen
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
# Vereinsmitglieder (Members)
# ---------------------------------------------------------------------------

class Member(Base):
    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Persönliche Daten
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Adresse
    street: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Bankdaten
    iban: Mapped[Optional[str]] = mapped_column(String(34), nullable=True)

    # Mitgliedschaft
    member_since: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    member_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Kommunikation
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Notizen (intern)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Beziehungen
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
    """Mehrere Telefonnummern pro Member."""
    __tablename__ = "member_phones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # z.B. "Mobil", "Festnetz"
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    member: Mapped["Member"] = relationship("Member", back_populates="phone_numbers")


class MemberEmail(Base):
    """Mehrere E-Mail-Adressen pro Member."""
    __tablename__ = "member_emails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # z.B. "Privat", "Arbeit"
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    member: Mapped["Member"] = relationship("Member", back_populates="email_addresses")


# ---------------------------------------------------------------------------
# Parzellen (Parcels)
# ---------------------------------------------------------------------------

class Parcel(Base):
    __tablename__ = "parcels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Gartennummer (z.B. "G093", "G26/27")
    plot_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)

    # Fläche
    area_sqm: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Status
    status: Mapped[ParcelStatus] = mapped_column(
        SAEnum(ParcelStatus), default=ParcelStatus.ACTIVE, nullable=False
    )

    # Kündigung (wer hat wann gekündigt)
    termination_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Notizen
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Beziehungen
    member_assignments: Mapped[List["MemberParcel"]] = relationship(
        "MemberParcel", back_populates="parcel"
    )
    metering_points: Mapped[List["MeteringPoint"]] = relationship(
        "MeteringPoint", back_populates="parcel"
    )

    def __repr__(self) -> str:
        return f"<Parcel {self.plot_number}>"


# ---------------------------------------------------------------------------
# Zuordnungstabelle Member <-> Parcel (m:n mit Metadaten)
# ---------------------------------------------------------------------------

class MemberParcel(Base):
    """
    Verbindet Mitglieder mit Parzellen.
    Ermöglicht Doppelgärten (ein Member, mehrere Parzellen)
    sowie Gemeinschaftsgärten (mehrere Mitglieder, eine Parcel).
    """
    __tablename__ = "member_parcels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    member_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parcel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Zeitraum der Zuordnung
    assigned_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    assigned_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship("Member", back_populates="parcel_assignments")
    parcel: Mapped["Parcel"] = relationship("Parcel", back_populates="member_assignments")

    __table_args__ = (
        UniqueConstraint("member_id", "parcel_id", name="uq_member_parcel"),
    )




# ---------------------------------------------------------------------------
# Vereinseinstellungen (Key-Value für Flexibilität)
# ---------------------------------------------------------------------------

class ClubSetting(Base):
    """
    Flexible Einstellungstabelle für Vereins-Stammdaten.
    Ermöglicht spätere Erweiterung ohne Schemaänderung.
    """
    __tablename__ = "club_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Bekannte Schlüssel (zur Dokumentation):
    # verein_name, verein_strasse, verein_plz, verein_ort
    # flaeche_gesamt_qm, flaeche_a_qm, flaeche_b_qm, flaeche_c_qm
    # vereinsnummer, registergericht


# ---------------------------------------------------------------------------
# Pflichtstunden-Konfiguration (jahresbasiert)
# ---------------------------------------------------------------------------

class WorkHoursMode(str, enum.Enum):
    PER_PARCEL = "PER_PARCEL"    # Stunden gelten pro Parcel (Standard)
    PER_MEMBER = "PER_MEMBER"    # Stunden gelten pro Member


class WorkHoursConfiguration(Base):
    """
    Jährliche Konfiguration der Pflichtstunden.
    Historisiert – alte Werte bleiben erhalten für Auswertungen vergangener Jahre.
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
# Club-Rollen (erweiterter Vorstand etc.)
# ---------------------------------------------------------------------------

class ExemptionReason(str, enum.Enum):
    BOARD = "BOARD"
    EXTENDED_BOARD = "EXTENDED_BOARD"
    ILLNESS = "ILLNESS"
    AGE = "AGE"
    OTHER = "OTHER"


class ClubRole(Base):
    """
    Rollen im Verein (Vorstand, erweiterter Vorstand, Beisitzer etc.).
    Getrennt vom App-Benutzersystem (UserRole) – hier geht es um
    Vereinsämter, nicht um Zugriffsrechte.
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
    Zuordnung Member → ClubRole für ein bestimmtes Jahr.
    Die Befreiung gilt immer für das gesamte Kalenderjahr (auch wenn die
    Rolle unterjährig niedergelegt wird).
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
# Patenschaften (Sponsorships)
# ---------------------------------------------------------------------------

class Sponsorship(Base):
    """
    Ein Member übernimmt die Patenschaft für einen Bereich (z.B. Hecke,
    Spielplatz). Die Patenschaft gilt pauschal als Pflichtstunden-Erfüllung.
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
        comment="Pauschale Stunden die pro Jahr angerechnet werden"
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="NULL = läuft noch")
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
# Arbeitseinsätze (Work Sessions)
# ---------------------------------------------------------------------------

class SessionType(str, enum.Enum):
    STANDARD = "STANDARD"    # Geplanter Termin, Anmeldung möglich
    SPECIAL = "SPECIAL"      # Spontan/ungeplant (Gartenbank streichen etc.)


class ParticipationStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"    # Hat sich angemeldet
    ATTENDED = "ATTENDED"        # War da, Stunden werden angerechnet
    NO_SHOW = "NO_SHOW"          # Angemeldet aber nicht erschienen


class WorkSession(Base):
    """
    Geplanter oder spontaner Arbeitseinsatz im Verein.
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
        comment="Standardwert; kann pro Teilnahme überschrieben werden"
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
    Teilnahme eines Mitglieds an einem Arbeitseinsatz.
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
        comment="Überschreibt hours_per_participant des Einsatzes wenn gesetzt"
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
# Änderungshistorie (generisches Audit-Log für Feldänderungen)
# ---------------------------------------------------------------------------

class ChangeHistory(Base):
    """
    Generisches Audit-Log: protokolliert Feldänderungen an beliebigen
    Entitäten (z.B. Parcel.area_sqm). Ermöglicht Nachvollziehbarkeit
    ohne für jede Tabelle eine eigene Historie-Tabelle zu brauchen.
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
# Zählerwesen: generisches Modul für Wasser- UND Stromzähler
#
# Ein Zaehlpunkt hat ein "medium" (WASSER oder STROM) und einen "typ"
# (Hauptzähler, Parcel, Vereinsanschluss). Die Verbrauchslogik ist für
# beide Medien identisch – nur Einheit, Anzeige-Rundung und Icon
# unterscheiden sich (siehe app/routers/zaehlerwesen.py).
# ---------------------------------------------------------------------------

class MeteringMedium(str, enum.Enum):
    WATER = "WATER"
    ELECTRICITY = "ELECTRICITY"


class MeteringPointType(str, enum.Enum):
    MAIN_METER = "MAIN_METER"  # Übergabepunkt vom öffentlichen Versorger
    PARCEL = "PARCEL"          # Anschluss an einer Parcel
    CLUB = "CLUB"              # Vereinseigene Anschlussstelle (Vereinsheim, Waschplatz etc.)


class MeteringPoint(Base):
    """
    Ein Zählpunkt für ein Medium (Wasser oder Strom). Entweder an eine
    Parcel gekoppelt, eine vereinseigene Anschlussstelle, oder der
    Hauptzähler der Gesamtversorgung vom öffentlichen Versorger.

    Eine Parcel kann sowohl einen Wasser- als auch einen Strom-MeteringPoint
    haben (zwei Zeilen, unterschieden über "medium").
    """
    __tablename__ = "metering_points"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    medium: Mapped[MeteringMedium] = mapped_column(SAEnum(MeteringMedium), nullable=False)
    type: Mapped[MeteringPointType] = mapped_column(SAEnum(MeteringPointType), nullable=False)

    parcel_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("parcels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Für MAIN_METER/CLUB-Zählpunkte (keine Parcel): freier Name.
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
        aktive = [z for z in self.meters if z.is_active]
        return aktive[0] if aktive else None

    def __repr__(self) -> str:
        return f"<MeteringPoint {self.medium.value}:{self.display_name}>"


class Meter(Base):
    """
    Der physische Zähler (Wasseruhr oder Stromzähler) an einem MeteringPoint.
    Beim Tausch wird der alte Zähler deaktiviert (removed_at gesetzt)
    und ein neuer mit neuer Nummer angelegt – die Historie bleibt
    vollständig erhalten.
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
        comment="Jahr, bis zu dem die Eichung gültig ist (Wasser i.d.R. +6, Strom i.d.R. +8 Jahre)"
    )
    installed_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    removed_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="NULL = noch verbaut")
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
    Eine jährliche Ablesung eines Zählers (Wasser oder Strom).
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
# Versicherungsmodul: Sach- und Unfallversicherung pro Parcel
# ---------------------------------------------------------------------------

class PropertyInsurancePackage(Base):
    """
    Ein wählbares Sachversicherungs-Paket (property insurance) für ein
    bestimmtes Jahr (z.B. "Paket 1" = 40 €, "Paket 2" = 60 € usw.). Anzahl
    und Beträge der Pakete sind frei konfigurierbar und können sich
    jährlich ändern.
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
    Jährliche Konfiguration der Unfallversicherungs-Beträge (accident
    insurance). Sachversicherung (property insurance) wird separat über
    PropertyInsurancePackage konfiguriert (mehrere Pakete pro Jahr),
    Unfallversicherung hat pro Jahr genau einen Grund- und Zusatzbetrag.
    """
    __tablename__ = "insurance_configuration"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    year: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    accident_base_amount_eur: Mapped[float] = mapped_column(
        Numeric(8, 2), nullable=False,
        comment="Deckt alle Mitglieder im selben Haushalt (gleiche Adresse) ab"
    )
    accident_additional_amount_eur: Mapped[float] = mapped_column(
        Numeric(8, 2), nullable=False,
        comment="Pro zusätzlich mitversicherter Person außerhalb des Haushalts"
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
    Versicherungsstatus einer Parcel für ein bestimmtes Jahr:
    Sachversicherung/property insurance (optional, mit gewähltem Paket)
    und Unfallversicherung/accident insurance (optional, Grundbetrag
    deckt den automatisch erkannten Haushalt ab -- siehe
    household_grouping() in app/insurance_utils.py).
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
    Ein Member, das zusätzlich zum automatisch erkannten Haushalt gegen
    Aufpreis in die Unfallversicherung (accident insurance) der Parcel
    aufgenommen wurde (z.B. ein Bewohner, der nicht am selben Wohnort
    lebt wie die übrigen Bewohner der Parcel).
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
# Ticketsystem
# ---------------------------------------------------------------------------

class TicketStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ASSIGNED = "ASSIGNED"
    WAITING = "WAITING"        # wartet auf Antwort des Absenders
    POSTPONED = "POSTPONED"    # zurückgestellt bis postponed_until
    CLOSED = "CLOSED"
    DELETED = "DELETED"        # Soft-Delete, wie bei Member.deleted_at


class MessageDirection(str, enum.Enum):
    INCOMING = "INCOMING"   # Vom Absender/Kunden (später per E-Mail, Etappe 1: manuell)
    OUTGOING = "OUTGOING"   # Antwort eines Benutzers (später als E-Mail versendet, Etappe 2)
    INTERNAL = "INTERNAL"   # Interne Notiz, nie an den Absender gesendet


class Ticket(Base):
    """
    Ein Support-Ticket = ein Anliegen eines Absenders. In Etappe 2 werden
    Tickets automatisch aus eingehenden E-Mails erzeugt; in Etappe 1 können
    sie manuell angelegt werden, um das Grundgerüst zu testen.
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

    # Automatischer Abgleich per Absender-E-Mail; überschreibbar/manuell korrigierbar,
    # falls die Adresse mehreren Mitgliedern gehört oder unbekannt ist.
    member_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sender_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Vorbereitung für Etappe 3 (Spam-Schnittstelle): Felder existieren bereits,
    # damit später keine weitere Migration nötig ist. Die eigentliche Prüfung
    # ist in Etappe 1 ein No-Op (siehe app/spam_filter.py).
    spam_suspected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spam_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    spam_reasoning: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Nachvollziehbare Begründung, warum als Spam eingestuft (Transparenz)"
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
        """True, wenn ein zurückgestelltes Ticket sein Datum erreicht hat und
        wieder als aktiv behandelt werden soll. Der eigentliche Statuswechsel
        (POSTPONED -> ACTIVE/ASSIGNED) passiert lazy beim nächsten Laden der
        Ticketliste (siehe _reaktiviere_faellige_tickets in app/routers/tickets.py),
        nicht über einen Hintergrundjob -- diese Property ist nur die reine
        Berechnung, falls sie anderswo (z.B. Badge-Anzeige) gebraucht wird.
        """
        if self.status != TicketStatus.POSTPONED:
            return False
        return self.postponed_until is not None and self.postponed_until <= date.today()

    def __repr__(self) -> str:
        return f"<Ticket {self.subject!r} ({self.status.value})>"


class TicketMessage(Base):
    """Eine einzelne Nachricht innerhalb eines Ticket-Verlaufs."""
    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[MessageDirection] = mapped_column(SAEnum(MessageDirection), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Nur für INCOMING-Nachrichten gesetzt, deren E-Mail einen text/html-Teil
    # hatte -- bereits bereinigt (siehe app/html_sanitizer.py) BEVOR es hier
    # gespeichert wird, damit es sicher mit {{ ... | safe }} gerendert werden
    # kann. `content` bleibt daneben immer die reine Textversion (Suche,
    # Benachrichtigungen, Fallback-Anzeige, falls kein HTML-Teil vorhanden war).
    content_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    authored_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Für E-Mail-Threading (Etappe 2): Message-ID dieser Nachricht bzw. der
    # Message-ID, auf die sie antwortet. Ermöglicht, eingehende Antworten
    # dem richtigen Ticket zuzuordnen, statt nur nach Betreff zu raten.
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
# Einkaufswünsche (Vier-Augen-Prinzip für Vereinsausgaben)
# ---------------------------------------------------------------------------

class PurchaseRequestStatus(str, enum.Enum):
    OPEN = "OPEN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PurchaseRequest(Base):
    """
    Ein Antrag auf eine Vereinsausgabe. Muss von zwei unterschiedlichen
    Vorstandsmitgliedern freigegeben werden, bevor eingekauft werden darf –
    der Antragsteller selbst zählt dabei nicht als Freigeber (Vier-Augen-Prinzip).
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

    # Antragsteller: entweder ein Systembenutzer (requested_by_id) ODER eine
    # externe Person ohne Login (requester_name/-email), z.B. wenn der
    # Vorstand stellvertretend für jemanden einen Antrag anlegt.
    requested_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requester_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    requester_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Deep-Link-Bestätigung durch den (externen) Antragsteller
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
    """Eine einzelne Freigabe eines Vorstandsmitglieds für einen PurchaseRequest."""
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
