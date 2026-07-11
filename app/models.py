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
    AKTIV = "AKTIV"
    GEKUENDIGT = "GEKUENDIGT"
    GELOESCHT = "GELOESCHT"


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
    zaehlpunkte: Mapped[List["Zaehlpunkt"]] = relationship(
        "Zaehlpunkt", back_populates="parzelle"
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
    STANDARD = "STANDARD"      # Geplanter Termin, Anmeldung möglich
    BESONDERS = "BESONDERS"    # Spontan/ungeplant (Gartenbank streichen etc.)


class TeilnahmeStatus(str, enum.Enum):
    ANGEMELDET = "ANGEMELDET"           # Hat sich angemeldet
    ERSCHIENEN = "ERSCHIENEN"           # War da, Stunden werden angerechnet
    NICHT_ERSCHIENEN = "NICHT_ERSCHIENEN"  # Angemeldet aber nicht erschienen


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


# ---------------------------------------------------------------------------
# Änderungshistorie (generisches Audit-Log für Feldänderungen)
# ---------------------------------------------------------------------------

class Aenderungshistorie(Base):
    """
    Generisches Audit-Log: protokolliert Feldänderungen an beliebigen
    Entitäten (z.B. Parzelle.flaeche_qm). Ermöglicht Nachvollziehbarkeit
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
# (Hauptzähler, Parzelle, Vereinsanschluss). Die Verbrauchslogik ist für
# beide Medien identisch – nur Einheit, Anzeige-Rundung und Icon
# unterscheiden sich (siehe app/routers/zaehlerwesen.py).
# ---------------------------------------------------------------------------

class ZaehlerMedium(str, enum.Enum):
    WASSER = "WASSER"
    STROM = "STROM"


class ZaehlpunktTyp(str, enum.Enum):
    HAUPTZAEHLER = "HAUPTZAEHLER"  # Übergabepunkt vom öffentlichen Versorger
    PARZELLE = "PARZELLE"          # Anschluss an einer Parzelle
    VEREIN = "VEREIN"              # Vereinseigene Anschlussstelle (Vereinsheim, Waschplatz etc.)


class Zaehlpunkt(Base):
    """
    Ein Zählpunkt für ein Medium (Wasser oder Strom). Entweder an eine
    Parzelle gekoppelt, eine vereinseigene Anschlussstelle, oder der
    Hauptzähler der Gesamtversorgung vom öffentlichen Versorger.

    Eine Parzelle kann sowohl einen Wasser- als auch einen Strom-Zaehlpunkt
    haben (zwei Zeilen, unterschieden über "medium").
    """
    __tablename__ = "zaehlpunkte"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    medium: Mapped[ZaehlerMedium] = mapped_column(SAEnum(ZaehlerMedium), nullable=False)
    typ: Mapped[ZaehlpunktTyp] = mapped_column(SAEnum(ZaehlpunktTyp), nullable=False)

    parzelle_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("parzellen.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Für HAUPTZAEHLER/VEREIN-Zaehlpunkte (keine Parzelle): freier Name.
    bezeichnung: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    notizen: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    parzelle: Mapped[Optional["Parzelle"]] = relationship("Parzelle", back_populates="zaehlpunkte")
    zaehler: Mapped[List["Zaehler"]] = relationship(
        "Zaehler", back_populates="zaehlpunkt", cascade="all, delete-orphan"
    )

    @property
    def anzeigename(self) -> str:
        if self.parzelle:
            return f"Parzelle {self.parzelle.gartennummer}"
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
# Versicherungsmodul: Sach- und Unfallversicherung pro Parzelle
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
    Versicherungsstatus einer Parzelle für ein bestimmtes Jahr:
    Sachversicherung (optional, mit gewähltem Paket) und Unfallversicherung
    (optional, Grundbetrag deckt den Haushalt des Hauptpächters ab).
    """
    __tablename__ = "parzelle_versicherung"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parzelle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parzellen.id", ondelete="CASCADE"), nullable=False, index=True
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

    parzelle: Mapped["Parzelle"] = relationship("Parzelle")
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
    Ein Mitglied, das zusätzlich zum Haushalt des Hauptpächters gegen
    Aufpreis in die Unfallversicherung der Parzelle aufgenommen wurde
    (z.B. ein Mitpächter, der nicht am selben Wohnort lebt).
    """
    __tablename__ = "unfallversicherung_zusatzpersonen"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    parzelle_versicherung_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("parzelle_versicherung.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mitglied_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    parzelle_versicherung: Mapped["ParzelleVersicherung"] = relationship(
        "ParzelleVersicherung", back_populates="zusatzpersonen"
    )
    mitglied: Mapped["Mitglied"] = relationship("Mitglied")

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
        String(36), ForeignKey("mitglieder.id", ondelete="SET NULL"), nullable=True, index=True
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
    mitglied: Mapped[Optional["Mitglied"]] = relationship("Mitglied")
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
