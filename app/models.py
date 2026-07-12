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


class BenutzerRolle(str, enum.Enum):
    ADMIN = "admin"
    VORSTAND = "vorstand"
    KASSIERER = "kassierer"
    LESEND = "lesend"


class EinladungStatus(str, enum.Enum):
    AUSSTEHEND = "ausstehend"
    ANGENOMMEN = "angenommen"
    ABGELAUFEN = "abgelaufen"


# ---------------------------------------------------------------------------
# Systembenutzer (Anwendungsnutzer, nicht Vereinsmitglieder)
# ---------------------------------------------------------------------------

class Benutzer(Base):
    __tablename__ = "benutzer"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    passwort_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rolle: Mapped[BenutzerRolle] = mapped_column(
        SAEnum(BenutzerRolle), default=BenutzerRolle.LESEND, nullable=False
    )
    ist_aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    letzter_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Beziehungen
    einladungen: Mapped[List["Einladung"]] = relationship("Einladung", back_populates="eingeladen_von")

    def __repr__(self) -> str:
        return f"<Benutzer {self.email} ({self.rolle})>"


class Einladung(Base):
    __tablename__ = "einladungen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    rolle: Mapped[BenutzerRolle] = mapped_column(
        SAEnum(BenutzerRolle), default=BenutzerRolle.LESEND, nullable=False
    )
    status: Mapped[EinladungStatus] = mapped_column(
        SAEnum(EinladungStatus), default=EinladungStatus.AUSSTEHEND, nullable=False
    )
    eingeladen_von_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
    )
    gueltig_bis: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    eingeladen_von: Mapped[Optional["Benutzer"]] = relationship("Benutzer", back_populates="einladungen")


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
    zaehlpunkte: Mapped[List["Zaehlpunkt"]] = relationship(
        "Zaehlpunkt", back_populates="parzelle"
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

    # Ist dieses Member der Hauptpächter?
    is_primary_tenant: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

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

class Vereinseinstellung(Base):
    """
    Flexible Einstellungstabelle für Vereins-Stammdaten.
    Ermöglicht spätere Erweiterung ohne Schemaänderung.
    """
    __tablename__ = "vereinseinstellungen"

    schluessel: Mapped[str] = mapped_column(String(100), primary_key=True)
    wert: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    beschreibung: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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
    Getrennt vom App-Benutzersystem (BenutzerRolle) – hier geht es um
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
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
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
    created_by: Mapped[Optional["Benutzer"]] = relationship("Benutzer")

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




# ---------------------------------------------------------------------------
# Änderungshistorie (generisches Audit-Log für Feldänderungen)
# ---------------------------------------------------------------------------

class Aenderungshistorie(Base):
    """
    Generisches Audit-Log: protokolliert Feldänderungen an beliebigen
    Entitäten (z.B. Parcel.area_sqm). Ermöglicht Nachvollziehbarkeit
    ohne für jede Tabelle eine eigene Historie-Tabelle zu brauchen.
    """
    __tablename__ = "aenderungshistorie"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    entitaet_typ: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entitaet_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    feldname: Mapped[str] = mapped_column(String(100), nullable=False)
    alter_wert: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    neuer_wert: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    geaendert_von_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
    )
    geaendert_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    geaendert_von: Mapped[Optional["Benutzer"]] = relationship("Benutzer")

    def __repr__(self) -> str:
        return f"<Aenderungshistorie {self.entitaet_typ}:{self.entitaet_id} {self.feldname}>"



# ---------------------------------------------------------------------------
# Zählerwesen: generisches Modul für Wasser- UND Stromzähler
#
# Ein Zaehlpunkt hat ein "medium" (WASSER oder STROM) und einen "typ"
# (Hauptzähler, Parcel, Vereinsanschluss). Die Verbrauchslogik ist für
# beide Medien identisch – nur Einheit, Anzeige-Rundung und Icon
# unterscheiden sich (siehe app/routers/zaehlerwesen.py).
# ---------------------------------------------------------------------------

class ZaehlerMedium(str, enum.Enum):
    WASSER = "WASSER"
    STROM = "STROM"


class ZaehlpunktTyp(str, enum.Enum):
    HAUPTZAEHLER = "HAUPTZAEHLER"  # Übergabepunkt vom öffentlichen Versorger
    PARZELLE = "PARZELLE"          # Anschluss an einer Parcel
    VEREIN = "VEREIN"              # Vereinseigene Anschlussstelle (Vereinsheim, Waschplatz etc.)


class Zaehlpunkt(Base):
    """
    Ein Zählpunkt für ein Medium (Wasser oder Strom). Entweder an eine
    Parcel gekoppelt, eine vereinseigene Anschlussstelle, oder der
    Hauptzähler der Gesamtversorgung vom öffentlichen Versorger.

    Eine Parcel kann sowohl einen Wasser- als auch einen Strom-Zaehlpunkt
    haben (zwei Zeilen, unterschieden über "medium").
    """
    __tablename__ = "zaehlpunkte"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    medium: Mapped[ZaehlerMedium] = mapped_column(SAEnum(ZaehlerMedium), nullable=False)
    typ: Mapped[ZaehlpunktTyp] = mapped_column(SAEnum(ZaehlpunktTyp), nullable=False)

    parzelle_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("parcels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Für HAUPTZAEHLER/VEREIN-Zaehlpunkte (keine Parcel): freier Name.
    bezeichnung: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    notizen: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    parzelle: Mapped[Optional["Parcel"]] = relationship("Parcel", back_populates="zaehlpunkte")
    zaehler: Mapped[List["Zaehler"]] = relationship(
        "Zaehler", back_populates="zaehlpunkt", cascade="all, delete-orphan"
    )

    @property
    def anzeigename(self) -> str:
        if self.parzelle:
            return f"Parcel {self.parzelle.plot_number}"
        return self.bezeichnung or "Unbenannter Zählpunkt"

    @property
    def aktueller_zaehler(self) -> Optional["Zaehler"]:
        aktive = [z for z in self.zaehler if z.ist_aktiv]
        return aktive[0] if aktive else None

    def __repr__(self) -> str:
        return f"<Zaehlpunkt {self.medium.value}:{self.anzeigename}>"


class Zaehler(Base):
    """
    Der physische Zähler (Wasseruhr oder Stromzähler) an einem Zaehlpunkt.
    Beim Tausch wird der alte Zähler deaktiviert (ausgebaut_am gesetzt)
    und ein neuer mit neuer Nummer angelegt – die Historie bleibt
    vollständig erhalten.
    """
    __tablename__ = "zaehler"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    zaehlpunkt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("zaehlpunkte.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nummer: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    ist_aktiv: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    geeicht_bis: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Jahr, bis zu dem die Eichung gültig ist (Wasser i.d.R. +6, Strom i.d.R. +8 Jahre)"
    )
    eingebaut_am: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    ausgebaut_am: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="NULL = noch verbaut")
    anfangsstand: Mapped[float] = mapped_column(Numeric(12, 1), default=0, nullable=False)
    notizen: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    zaehlpunkt: Mapped["Zaehlpunkt"] = relationship("Zaehlpunkt", back_populates="zaehler")
    zaehlerstaende: Mapped[List["Zaehlerstand"]] = relationship(
        "Zaehlerstand", back_populates="zaehler", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Zaehler {self.nummer}>"


class Zaehlerstand(Base):
    """
    Eine jährliche Ablesung eines Zählers (Wasser oder Strom).
    """
    __tablename__ = "zaehlerstaende"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    zaehler_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("zaehler.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jahr: Mapped[int] = mapped_column(Integer, nullable=False)
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    stand: Mapped[float] = mapped_column(Numeric(12, 1), nullable=False)
    erfasst_von_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
    )
    notiz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    zaehler: Mapped["Zaehler"] = relationship("Zaehler", back_populates="zaehlerstaende")
    erfasst_von: Mapped[Optional["Benutzer"]] = relationship("Benutzer")

    __table_args__ = (
        UniqueConstraint("zaehler_id", "jahr", name="uq_zaehler_jahr"),
    )

    def __repr__(self) -> str:
        return f"<Zaehlerstand {self.jahr}: {self.stand}>"


# ---------------------------------------------------------------------------
# Versicherungsmodul: Sach- und Unfallversicherung pro Parcel
# ---------------------------------------------------------------------------

class SachversicherungPaket(Base):
    """
    Ein wählbares Sachversicherungs-Paket für ein bestimmtes Jahr
    (z.B. "Paket 1" = 40 €, "Paket 2" = 60 € usw.). Anzahl und Beträge
    der Pakete sind frei konfigurierbar und können sich jährlich ändern.
    """
    __tablename__ = "sachversicherung_pakete"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    jahr: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bezeichnung: Mapped[str] = mapped_column(String(100), nullable=False)
    betrag_eur: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    reihenfolge: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<SachversicherungPaket {self.jahr} {self.bezeichnung}: {self.betrag_eur}€>"


class VersicherungsKonfiguration(Base):
    """
    Jährliche Konfiguration der Unfallversicherungs-Beträge. Sachversicherung
    wird separat über SachversicherungPaket konfiguriert (mehrere Pakete
    pro Jahr), Unfallversicherung hat pro Jahr genau einen Grund- und
    Zusatzbetrag.
    """
    __tablename__ = "versicherungs_konfiguration"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    jahr: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    unfall_grundbetrag_eur: Mapped[float] = mapped_column(
        Numeric(8, 2), nullable=False,
        comment="Deckt alle Mitglieder im selben Haushalt (gleiche Adresse) ab"
    )
    unfall_zusatzbetrag_eur: Mapped[float] = mapped_column(
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
        return f"<VersicherungsKonfiguration {self.jahr}>"


class ParzelleVersicherung(Base):
    """
    Versicherungsstatus einer Parcel für ein bestimmtes Jahr:
    Sachversicherung (optional, mit gewähltem Paket) und Unfallversicherung
    (optional, Grundbetrag deckt den Haushalt des Hauptpächters ab).
    """
    __tablename__ = "parzelle_versicherung"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parzelle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jahr: Mapped[int] = mapped_column(Integer, nullable=False)

    hat_sachversicherung: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sach_paket_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("sachversicherung_pakete.id", ondelete="SET NULL"), nullable=True
    )

    hat_unfallversicherung: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    parzelle: Mapped["Parcel"] = relationship("Parcel")
    sach_paket: Mapped[Optional["SachversicherungPaket"]] = relationship("SachversicherungPaket")
    zusatzpersonen: Mapped[List["UnfallversicherungZusatzperson"]] = relationship(
        "UnfallversicherungZusatzperson", back_populates="parzelle_versicherung", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("parzelle_id", "jahr", name="uq_parzelle_versicherung_jahr"),
    )

    def __repr__(self) -> str:
        return f"<ParzelleVersicherung {self.parzelle_id} {self.jahr}>"


class UnfallversicherungZusatzperson(Base):
    """
    Ein Member, das zusätzlich zum Haushalt des Hauptpächters gegen
    Aufpreis in die Unfallversicherung der Parcel aufgenommen wurde
    (z.B. ein Mitpächter, der nicht am selben Wohnort lebt).
    """
    __tablename__ = "unfallversicherung_zusatzpersonen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parzelle_versicherung_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parzelle_versicherung.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mitglied_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    parzelle_versicherung: Mapped["ParzelleVersicherung"] = relationship(
        "ParzelleVersicherung", back_populates="zusatzpersonen"
    )
    mitglied: Mapped["Member"] = relationship("Member")

    __table_args__ = (
        UniqueConstraint("parzelle_versicherung_id", "mitglied_id", name="uq_versicherung_mitglied"),
    )


# ---------------------------------------------------------------------------
# Ticketsystem
# ---------------------------------------------------------------------------

class TicketStatus(str, enum.Enum):
    NICHT_ZUGEWIESEN = "NICHT_ZUGEWIESEN"
    ZUGEWIESEN = "ZUGEWIESEN"
    ZURUECKGESTELLT = "ZURUECKGESTELLT"
    GESCHLOSSEN = "GESCHLOSSEN"


class NachrichtRichtung(str, enum.Enum):
    EINGEHEND = "EINGEHEND"   # Vom Absender/Kunden (später per E-Mail, Etappe 1: manuell)
    AUSGEHEND = "AUSGEHEND"   # Antwort eines Benutzers (später als E-Mail versendet, Etappe 2)
    INTERN = "INTERN"         # Interne Notiz, nie an den Absender gesendet


class Ticket(Base):
    """
    Ein Support-Ticket = ein Anliegen eines Absenders. In Etappe 2 werden
    Tickets automatisch aus eingehenden E-Mails erzeugt; in Etappe 1 können
    sie manuell angelegt werden, um das Grundgerüst zu testen.
    """
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    betreff: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus), default=TicketStatus.NICHT_ZUGEWIESEN, nullable=False, index=True
    )
    zugewiesen_an_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True, index=True
    )
    zurueckgestellt_bis: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Automatischer Abgleich per Absender-E-Mail; überschreibbar/manuell korrigierbar,
    # falls die Adresse mehreren Mitgliedern gehört oder unbekannt ist.
    mitglied_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    absender_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    absender_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Vorbereitung für Etappe 3 (Spam-Schnittstelle): Felder existieren bereits,
    # damit später keine weitere Migration nötig ist. Die eigentliche Prüfung
    # ist in Etappe 1 ein No-Op (siehe app/spam_filter.py).
    spam_verdacht: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spam_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    spam_begruendung: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Nachvollziehbare Begründung, warum als Spam eingestuft (Transparenz)"
    )

    erstellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    geschlossen_am: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    zugewiesen_an: Mapped[Optional["Benutzer"]] = relationship("Benutzer")
    mitglied: Mapped[Optional["Member"]] = relationship("Member")
    nachrichten: Mapped[List["TicketNachricht"]] = relationship(
        "TicketNachricht", back_populates="ticket", cascade="all, delete-orphan",
        order_by="TicketNachricht.erstellt_am",
    )

    @property
    def ist_faellig(self) -> bool:
        """True, wenn ein zurückgestelltes Ticket sein Datum erreicht hat und
        wieder als aktiv behandelt werden soll (rein berechnet, kein Hintergrundjob)."""
        if self.status != TicketStatus.ZURUECKGESTELLT:
            return False
        return self.zurueckgestellt_bis is not None and self.zurueckgestellt_bis <= date.today()

    def __repr__(self) -> str:
        return f"<Ticket {self.betreff!r} ({self.status.value})>"


class TicketNachricht(Base):
    """Eine einzelne Nachricht innerhalb eines Ticket-Verlaufs."""
    __tablename__ = "ticket_nachrichten"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    ticket_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    richtung: Mapped[NachrichtRichtung] = mapped_column(SAEnum(NachrichtRichtung), nullable=False)
    inhalt: Mapped[str] = mapped_column(Text, nullable=False)
    verfasst_von_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
    )
    # Für E-Mail-Threading (Etappe 2): Message-ID dieser Nachricht bzw. der
    # Message-ID, auf die sie antwortet. Ermöglicht, eingehende Antworten
    # dem richtigen Ticket zuzuordnen, statt nur nach Betreff zu raten.
    message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    erstellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="nachrichten")
    verfasst_von: Mapped[Optional["Benutzer"]] = relationship("Benutzer")

    def __repr__(self) -> str:
        return f"<TicketNachricht {self.richtung.value} @ {self.ticket_id}>"


# ---------------------------------------------------------------------------
# Einkaufswünsche (Vier-Augen-Prinzip für Vereinsausgaben)
# ---------------------------------------------------------------------------

class EinkaufswunschStatus(str, enum.Enum):
    OFFEN = "OFFEN"
    GENEHMIGT = "GENEHMIGT"
    ABGELEHNT = "ABGELEHNT"


class Einkaufswunsch(Base):
    """
    Ein Antrag auf eine Vereinsausgabe. Muss von zwei unterschiedlichen
    Vorstandsmitgliedern freigegeben werden, bevor eingekauft werden darf –
    der Antragsteller selbst zählt dabei nicht als Freigeber (Vier-Augen-Prinzip).
    """
    __tablename__ = "einkaufswuensche"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    titel: Mapped[str] = mapped_column(String(255), nullable=False)
    begruendung: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    geschaetzte_kosten_eur: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    status: Mapped[EinkaufswunschStatus] = mapped_column(
        SAEnum(EinkaufswunschStatus), default=EinkaufswunschStatus.OFFEN, nullable=False, index=True
    )

    # Antragsteller: entweder ein Systembenutzer (angefragt_von_id) ODER eine
    # externe Person ohne Login (anfragender_name/-email), z.B. wenn der
    # Vorstand stellvertretend für jemanden einen Antrag anlegt.
    angefragt_von_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
    )
    anfragender_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    anfragender_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    erstellt_von_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
    )

    # Deep-Link-Bestätigung durch den (externen) Antragsteller
    bestaetigungs_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    vom_anfragenden_bestaetigt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vom_anfragenden_bestaetigt_am: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    ablehnungsgrund: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    abgelehnt_von_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
    )
    abgelehnt_am: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    genehmigt_am: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    erstellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    aktualisiert_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    angefragt_von: Mapped[Optional["Benutzer"]] = relationship("Benutzer", foreign_keys=[angefragt_von_id])
    erstellt_von: Mapped[Optional["Benutzer"]] = relationship("Benutzer", foreign_keys=[erstellt_von_id])
    abgelehnt_von: Mapped[Optional["Benutzer"]] = relationship("Benutzer", foreign_keys=[abgelehnt_von_id])
    freigaben: Mapped[List["EinkaufswunschFreigabe"]] = relationship(
        "EinkaufswunschFreigabe", back_populates="einkaufswunsch", cascade="all, delete-orphan"
    )

    @property
    def anzeigename_anfragender(self) -> str:
        if self.angefragt_von:
            return self.angefragt_von.name
        return self.anfragender_name or self.anfragender_email or "Unbekannt"

    @property
    def anzahl_freigaben(self) -> int:
        return len(self.freigaben)

    def __repr__(self) -> str:
        return f"<Einkaufswunsch {self.titel!r} ({self.status.value})>"


class EinkaufswunschFreigabe(Base):
    """Eine einzelne Freigabe eines Vorstandsmitglieds für einen Einkaufswunsch."""
    __tablename__ = "einkaufswunsch_freigaben"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    einkaufswunsch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("einkaufswuensche.id", ondelete="CASCADE"), nullable=False, index=True
    )
    benutzer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="CASCADE"), nullable=False, index=True
    )
    freigegeben_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    einkaufswunsch: Mapped["Einkaufswunsch"] = relationship("Einkaufswunsch", back_populates="freigaben")
    benutzer: Mapped["Benutzer"] = relationship("Benutzer")

    __table_args__ = (
        UniqueConstraint("einkaufswunsch_id", "benutzer_id", name="uq_einkaufswunsch_freigabe"),
    )
