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

class ParzelleStatus(str, enum.Enum):
    AKTIV = "aktiv"
    GEKUENDIGT = "gekuendigt"
    GELOESCHT = "geloescht"


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
# Vereinsmitglieder
# ---------------------------------------------------------------------------

class Mitglied(Base):
    __tablename__ = "mitglieder"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Persönliche Daten
    vorname: Mapped[str] = mapped_column(String(100), nullable=False)
    nachname: Mapped[str] = mapped_column(String(100), nullable=False)
    geburtsdatum: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Adresse
    strasse: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    plz: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    ort: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Bankdaten
    iban: Mapped[Optional[str]] = mapped_column(String(34), nullable=True)

    # Mitgliedschaft
    mitglied_seit: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    mitglied_bis: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Kommunikation
    email_benachrichtigungen: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Notizen (intern)
    notizen: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Beziehungen
    telefonnummern: Mapped[List["MitgliedTelefon"]] = relationship(
        "MitgliedTelefon", back_populates="mitglied", cascade="all, delete-orphan"
    )
    email_adressen: Mapped[List["MitgliedEmail"]] = relationship(
        "MitgliedEmail", back_populates="mitglied", cascade="all, delete-orphan"
    )
    parzellen_zuordnungen: Mapped[List["MitgliedParzelle"]] = relationship(
        "MitgliedParzelle", back_populates="mitglied"
    )

    @property
    def vollname(self) -> str:
        return f"{self.vorname} {self.nachname}"

    @property
    def ist_aktiv(self) -> bool:
        return self.deleted_at is None and (
            self.mitglied_bis is None or self.mitglied_bis >= date.today()
        )

    def __repr__(self) -> str:
        return f"<Mitglied {self.vollname}>"


class MitgliedTelefon(Base):
    """Mehrere Telefonnummern pro Mitglied."""
    __tablename__ = "mitglied_telefon"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    mitglied_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nummer: Mapped[str] = mapped_column(String(50), nullable=False)
    bezeichnung: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # z.B. "Mobil", "Festnetz"
    ist_primaer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    mitglied: Mapped["Mitglied"] = relationship("Mitglied", back_populates="telefonnummern")


class MitgliedEmail(Base):
    """Mehrere E-Mail-Adressen pro Mitglied."""
    __tablename__ = "mitglied_email"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    mitglied_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False, index=True
    )
    adresse: Mapped[str] = mapped_column(String(255), nullable=False)
    bezeichnung: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # z.B. "Privat", "Arbeit"
    ist_primaer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    mitglied: Mapped["Mitglied"] = relationship("Mitglied", back_populates="email_adressen")


# ---------------------------------------------------------------------------
# Parzellen
# ---------------------------------------------------------------------------

class Parzelle(Base):
    __tablename__ = "parzellen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Gartennummer (z.B. "G093", "G26/27")
    gartennummer: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)

    # Fläche
    flaeche_qm: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)

    # Status
    status: Mapped[ParzelleStatus] = mapped_column(
        SAEnum(ParzelleStatus), default=ParzelleStatus.AKTIV, nullable=False
    )

    # Kündigung (wer hat wann gekündigt)
    kuendigung_datum: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    kuendigung_notiz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Notizen
    notizen: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Beziehungen
    mitglieder_zuordnungen: Mapped[List["MitgliedParzelle"]] = relationship(
        "MitgliedParzelle", back_populates="parzelle"
    )

    def __repr__(self) -> str:
        return f"<Parzelle {self.gartennummer}>"


# ---------------------------------------------------------------------------
# Zuordnungstabelle Mitglied <-> Parzelle (m:n mit Metadaten)
# ---------------------------------------------------------------------------

class MitgliedParzelle(Base):
    """
    Verbindet Mitglieder mit Parzellen.
    Ermöglicht Doppelgärten (ein Mitglied, mehrere Parzellen)
    sowie Gemeinschaftsgärten (mehrere Mitglieder, eine Parzelle).
    """
    __tablename__ = "mitglied_parzelle"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    mitglied_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parzelle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parzellen.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Ist dieses Mitglied der Hauptpächter?
    ist_hauptpaechter: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Zeitraum der Zuordnung
    zuordnung_von: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    zuordnung_bis: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    mitglied: Mapped["Mitglied"] = relationship("Mitglied", back_populates="parzellen_zuordnungen")
    parzelle: Mapped["Parzelle"] = relationship("Parzelle", back_populates="mitglieder_zuordnungen")

    __table_args__ = (
        UniqueConstraint("mitglied_id", "parzelle_id", name="uq_mitglied_parzelle"),
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

class PflichtstundenModus(str, enum.Enum):
    PRO_PACHTVERTRAG = "pro_pachtvertrag"  # Stunden gelten pro Parzelle (Standard)
    PRO_MITGLIED = "pro_mitglied"          # Stunden gelten pro Mitglied


class PflichtstundenKonfiguration(Base):
    """
    Jährliche Konfiguration der Pflichtstunden.
    Historisiert – alte Werte bleiben erhalten für Auswertungen vergangener Jahre.
    """
    __tablename__ = "pflichtstunden_konfiguration"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    jahr: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    stunden_gesamt: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    stundensatz_eur: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    modus: Mapped[PflichtstundenModus] = mapped_column(
        SAEnum(PflichtstundenModus), default=PflichtstundenModus.PRO_PACHTVERTRAG, nullable=False
    )
    notiz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<PflichtstundenKonfiguration {self.jahr}: {self.stunden_gesamt}h à {self.stundensatz_eur}€>"


# ---------------------------------------------------------------------------
# Vereinsrollen (erweiterter Vorstand etc.)
# ---------------------------------------------------------------------------

class BefreiungsGrund(str, enum.Enum):
    VORSTAND = "VORSTAND"
    ERWEITERTER_VORSTAND = "ERWEITERTER_VORSTAND"
    KRANKHEIT = "KRANKHEIT"
    ALTER = "ALTER"
    SONSTIG = "SONSTIG"


class Vereinsrolle(Base):
    """
    Rollen im Verein (Vorstand, erweiterter Vorstand, Beisitzer etc.).
    Getrennt vom App-Benutzersystem (BenutzerRolle) – hier geht es um
    Vereinsämter, nicht um Zugriffsrechte.
    """
    __tablename__ = "vereinsrollen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    beschreibung: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pflichtstunden_befreit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    befreiungsgrund: Mapped[Optional[BefreiungsGrund]] = mapped_column(
        SAEnum(BefreiungsGrund), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    zuordnungen: Mapped[List["MitgliedVereinsrolle"]] = relationship(
        "MitgliedVereinsrolle", back_populates="vereinsrolle"
    )

    def __repr__(self) -> str:
        return f"<Vereinsrolle {self.name}>"


class MitgliedVereinsrolle(Base):
    """
    Zuordnung Mitglied → Vereinsrolle für ein bestimmtes Jahr.
    Die Befreiung gilt immer für das gesamte Kalenderjahr (auch wenn die
    Rolle unterjährig niedergelegt wird).
    """
    __tablename__ = "mitglied_vereinsrolle"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    mitglied_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vereinsrolle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("vereinsrollen.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jahr: Mapped[int] = mapped_column(Integer, nullable=False)
    von: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    bis: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notiz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    mitglied: Mapped["Mitglied"] = relationship("Mitglied")
    vereinsrolle: Mapped["Vereinsrolle"] = relationship("Vereinsrolle", back_populates="zuordnungen")

    __table_args__ = (
        UniqueConstraint("mitglied_id", "vereinsrolle_id", "jahr", name="uq_mitglied_vereinsrolle_jahr"),
    )


# ---------------------------------------------------------------------------
# Patenschaften
# ---------------------------------------------------------------------------

class Patenschaft(Base):
    """
    Ein Mitglied übernimmt die Patenschaft für einen Bereich (z.B. Hecke,
    Spielplatz). Die Patenschaft gilt pauschal als Pflichtstunden-Erfüllung.
    """
    __tablename__ = "patenschaften"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    mitglied_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("mitglieder.id", ondelete="SET NULL"), nullable=True, index=True
    )
    bereich: Mapped[str] = mapped_column(String(255), nullable=False)
    beschreibung: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stunden_anrechenbar: Mapped[float] = mapped_column(
        Numeric(5, 1), nullable=False,
        comment="Pauschale Stunden die pro Jahr angerechnet werden"
    )
    von: Mapped[date] = mapped_column(Date, nullable=False)
    bis: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="NULL = läuft noch")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    mitglied: Mapped[Optional["Mitglied"]] = relationship("Mitglied")

    def __repr__(self) -> str:
        return f"<Patenschaft {self.bereich} → {self.mitglied_id}>"


# ---------------------------------------------------------------------------
# Arbeitseinsätze
# ---------------------------------------------------------------------------

class EinsatzTyp(str, enum.Enum):
    STANDARD = "standard"      # Geplanter Termin, Anmeldung möglich
    BESONDERS = "besonders"    # Spontan/ungeplant (Gartenbank streichen etc.)


class TeilnahmeStatus(str, enum.Enum):
    ANGEMELDET = "angemeldet"           # Hat sich angemeldet
    ERSCHIENEN = "erschienen"           # War da, Stunden werden angerechnet
    NICHT_ERSCHIENEN = "nicht_erschienen"  # Angemeldet aber nicht erschienen


class Arbeitseinsatz(Base):
    """
    Geplanter oder spontaner Arbeitseinsatz im Verein.
    """
    __tablename__ = "arbeitseinsaetze"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    titel: Mapped[str] = mapped_column(String(255), nullable=False)
    beschreibung: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    typ: Mapped[EinsatzTyp] = mapped_column(
        SAEnum(EinsatzTyp), default=EinsatzTyp.STANDARD, nullable=False
    )
    datum: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    uhrzeit_von: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)   # "08:00"
    uhrzeit_bis: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)   # "12:00"
    max_teilnehmer: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stunden_pro_teilnehmer: Mapped[Optional[float]] = mapped_column(
        Numeric(4, 1), nullable=True,
        comment="Standardwert; kann pro Teilnahme überschrieben werden"
    )
    erstellt_von_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    teilnahmen: Mapped[List["EinsatzTeilnahme"]] = relationship(
        "EinsatzTeilnahme", back_populates="einsatz", cascade="all, delete-orphan"
    )
    erstellt_von: Mapped[Optional["Benutzer"]] = relationship("Benutzer")

    @property
    def freie_plaetze(self) -> Optional[int]:
        if self.max_teilnehmer is None:
            return None
        angemeldet = sum(1 for t in self.teilnahmen if t.status != TeilnahmeStatus.NICHT_ERSCHIENEN)
        return max(0, self.max_teilnehmer - angemeldet)

    def __repr__(self) -> str:
        return f"<Arbeitseinsatz {self.datum} {self.titel}>"


class EinsatzTeilnahme(Base):
    """
    Teilnahme eines Mitglieds an einem Arbeitseinsatz.
    """
    __tablename__ = "einsatz_teilnahmen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    einsatz_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("arbeitseinsaetze.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mitglied_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[TeilnahmeStatus] = mapped_column(
        SAEnum(TeilnahmeStatus), default=TeilnahmeStatus.ANGEMELDET, nullable=False
    )
    stunden_geleistet: Mapped[Optional[float]] = mapped_column(
        Numeric(4, 1), nullable=True,
        comment="Überschreibt stunden_pro_teilnehmer des Einsatzes wenn gesetzt"
    )
    notiz: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    einsatz: Mapped["Arbeitseinsatz"] = relationship("Arbeitseinsatz", back_populates="teilnahmen")
    mitglied: Mapped["Mitglied"] = relationship("Mitglied")

    __table_args__ = (
        UniqueConstraint("einsatz_id", "mitglied_id", name="uq_einsatz_mitglied"),
    )
