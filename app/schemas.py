"""
Pydantic-Schemas für die REST-API.

Trennung von DB-Modellen (app/models.py) und API-Schemas ist bewusst:
so können wir API-Verträge stabil halten, auch wenn sich interne
Modelle ändern, und unterschiedliche Felder für Erstellung/Antwort haben.
"""
from datetime import date, datetime
from typing import Optional, List
from decimal import Decimal

from pydantic import BaseModel, EmailStr, ConfigDict, Field

from app.models import ParzelleStatus, BenutzerRolle


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minuten: int


class LoginRequest(BaseModel):
    email: EmailStr
    passwort: str


class BenutzerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    name: str
    rolle: BenutzerRolle
    ist_aktiv: bool


# ---------------------------------------------------------------------------
# Telefon / E-Mail (Unterobjekte von Mitglied)
# ---------------------------------------------------------------------------

class TelefonBase(BaseModel):
    nummer: str = Field(..., max_length=50)
    bezeichnung: Optional[str] = Field(None, max_length=50)
    ist_primaer: bool = False


class TelefonCreate(TelefonBase):
    pass


class TelefonOut(TelefonBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class EmailAdresseBase(BaseModel):
    adresse: EmailStr
    bezeichnung: Optional[str] = Field(None, max_length=50)
    ist_primaer: bool = False


class EmailAdresseCreate(EmailAdresseBase):
    pass


class EmailAdresseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    adresse: str
    bezeichnung: Optional[str] = None
    ist_primaer: bool


# ---------------------------------------------------------------------------
# Mitglied
# ---------------------------------------------------------------------------

class MitgliedBase(BaseModel):
    vorname: str = Field(..., max_length=100)
    nachname: str = Field(..., max_length=100)
    geburtsdatum: Optional[date] = None
    strasse: Optional[str] = Field(None, max_length=255)
    plz: Optional[str] = Field(None, max_length=10)
    ort: Optional[str] = Field(None, max_length=100)
    iban: Optional[str] = Field(None, max_length=34)
    mitglied_seit: Optional[date] = None
    mitglied_bis: Optional[date] = None
    email_benachrichtigungen: bool = True
    notizen: Optional[str] = None


class MitgliedCreate(MitgliedBase):
    pass


class MitgliedUpdate(BaseModel):
    """Alle Felder optional – für PATCH-artige Teilupdates via PUT."""
    vorname: Optional[str] = Field(None, max_length=100)
    nachname: Optional[str] = Field(None, max_length=100)
    geburtsdatum: Optional[date] = None
    strasse: Optional[str] = Field(None, max_length=255)
    plz: Optional[str] = Field(None, max_length=10)
    ort: Optional[str] = Field(None, max_length=100)
    iban: Optional[str] = Field(None, max_length=34)
    mitglied_seit: Optional[date] = None
    mitglied_bis: Optional[date] = None
    email_benachrichtigungen: Optional[bool] = None
    notizen: Optional[str] = None


class ParzelleZuordnungKurz(BaseModel):
    """Kompakte Parzelleninfo innerhalb einer Mitglied-Antwort."""
    model_config = ConfigDict(from_attributes=True)
    parzelle_id: str
    gartennummer: str
    ist_hauptpaechter: bool


class MitgliedOut(MitgliedBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    updated_at: datetime
    ist_aktiv: bool
    telefonnummern: List[TelefonOut] = []
    email_adressen: List[EmailAdresseOut] = []


class MitgliedDetailOut(MitgliedOut):
    """Erweiterte Ansicht inkl. zugeordneter Parzellen, für GET /mitglieder/{id}."""
    parzellen: List[ParzelleZuordnungKurz] = []


# ---------------------------------------------------------------------------
# Parzelle
# ---------------------------------------------------------------------------

class ParzelleBase(BaseModel):
    gartennummer: str = Field(..., max_length=20)
    flaeche_qm: Optional[Decimal] = None
    notizen: Optional[str] = None


class ParzelleCreate(ParzelleBase):
    pass


class ParzelleUpdate(BaseModel):
    gartennummer: Optional[str] = Field(None, max_length=20)
    flaeche_qm: Optional[Decimal] = None
    status: Optional[ParzelleStatus] = None
    kuendigung_datum: Optional[date] = None
    kuendigung_notiz: Optional[str] = None
    notizen: Optional[str] = None


class MitgliedZuordnungKurz(BaseModel):
    """Kompakte Mitgliedinfo innerhalb einer Parzelle-Antwort."""
    model_config = ConfigDict(from_attributes=True)
    mitglied_id: str
    name: str
    ist_hauptpaechter: bool


class ParzelleOut(ParzelleBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: ParzelleStatus
    kuendigung_datum: Optional[date] = None
    kuendigung_notiz: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ParzelleDetailOut(ParzelleOut):
    mitglieder: List[MitgliedZuordnungKurz] = []


# ---------------------------------------------------------------------------
# Mitglied-Parzelle-Zuordnung
# ---------------------------------------------------------------------------

class ZuordnungCreate(BaseModel):
    mitglied_id: str
    parzelle_id: str
    ist_hauptpaechter: bool = True
    zuordnung_von: Optional[date] = None
    zuordnung_bis: Optional[date] = None


class ZuordnungOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    mitglied_id: str
    parzelle_id: str
    ist_hauptpaechter: bool
    zuordnung_von: Optional[date] = None
    zuordnung_bis: Optional[date] = None


# ---------------------------------------------------------------------------
# Vereinseinstellung
# ---------------------------------------------------------------------------

class VereinseinstellungOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    schluessel: str
    wert: Optional[str] = None
    beschreibung: Optional[str] = None


class VereinseinstellungUpdate(BaseModel):
    wert: Optional[str] = None


# ---------------------------------------------------------------------------
# Generische Listenantwort (Pagination-ready)
# ---------------------------------------------------------------------------

class PaginierteAntwort(BaseModel):
    gesamt: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Pflichtstunden
# ---------------------------------------------------------------------------

class PflichtstundenKonfigurationBase(BaseModel):
    jahr: int
    stunden_gesamt: Decimal
    stundensatz_eur: Decimal
    modus: str = Field("PRO_PACHTVERTRAG", description="PRO_PACHTVERTRAG oder PRO_MITGLIED")
    notiz: Optional[str] = None


class PflichtstundenKonfigurationCreate(PflichtstundenKonfigurationBase):
    pass


class PflichtstundenKonfigurationOut(PflichtstundenKonfigurationBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class VereinsrolleBase(BaseModel):
    name: str
    beschreibung: Optional[str] = None
    pflichtstunden_befreit: bool = False
    befreiungsgrund: Optional[str] = None


class VereinsrolleCreate(VereinsrolleBase):
    pass


class VereinsrolleOut(VereinsrolleBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class MitgliedVereinsrolleCreate(BaseModel):
    mitglied_id: str
    vereinsrolle_id: str
    jahr: int
    von: Optional[date] = None
    bis: Optional[date] = None
    notiz: Optional[str] = None


class MitgliedVereinsrolleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    mitglied_id: str
    vereinsrolle_id: str
    jahr: int
    von: Optional[date] = None
    bis: Optional[date] = None
    notiz: Optional[str] = None


class ArbeitseinsatzBase(BaseModel):
    titel: str
    beschreibung: Optional[str] = None
    typ: str = Field("STANDARD", description="STANDARD oder BESONDERS")
    datum: date
    uhrzeit_von: Optional[str] = None
    uhrzeit_bis: Optional[str] = None
    max_teilnehmer: Optional[int] = None
    stunden_pro_teilnehmer: Optional[Decimal] = None


class ArbeitseinsatzCreate(ArbeitseinsatzBase):
    pass


class ArbeitseinsatzUpdate(BaseModel):
    titel: Optional[str] = None
    beschreibung: Optional[str] = None
    typ: Optional[str] = None
    datum: Optional[date] = None
    uhrzeit_von: Optional[str] = None
    uhrzeit_bis: Optional[str] = None
    max_teilnehmer: Optional[int] = None
    stunden_pro_teilnehmer: Optional[Decimal] = None


class ArbeitseinsatzOut(ArbeitseinsatzBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class EinsatzTeilnahmeCreate(BaseModel):
    mitglied_id: str
    status: str = Field("ERSCHIENEN", description="ANGEMELDET, ERSCHIENEN oder NICHT_ERSCHIENEN")
    stunden_geleistet: Optional[Decimal] = None
    notiz: Optional[str] = None


class EinsatzTeilnahmeUpdate(BaseModel):
    status: Optional[str] = None
    stunden_geleistet: Optional[Decimal] = None
    notiz: Optional[str] = None


class EinsatzTeilnahmeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    einsatz_id: str
    mitglied_id: str
    status: str
    stunden_geleistet: Optional[Decimal] = None
    notiz: Optional[str] = None


class PatenschaftBase(BaseModel):
    bereich: str
    beschreibung: Optional[str] = None
    stunden_anrechenbar: Decimal
    von: date
    bis: Optional[date] = None


class PatenschaftCreate(PatenschaftBase):
    mitglied_id: Optional[str] = None


class PatenschaftUpdate(BaseModel):
    mitglied_id: Optional[str] = None
    bereich: Optional[str] = None
    beschreibung: Optional[str] = None
    stunden_anrechenbar: Optional[Decimal] = None
    von: Optional[date] = None
    bis: Optional[date] = None


class PatenschaftOut(PatenschaftBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    mitglied_id: Optional[str] = None


class AuswertungZeileOut(BaseModel):
    """Eine Zeile der Pflichtstunden-Jahresauswertung."""
    bezeichnung: str
    pflicht_stunden: Decimal
    geleistete_stunden: Decimal
    offen_stunden: Decimal
    schuldbetrag_eur: Decimal
    befreit: bool
    erfuellt: bool


# ---------------------------------------------------------------------------
# Zählerwesen (Wasser & Strom) – medium-agnostische Schemas
# ---------------------------------------------------------------------------

class ZaehlpunktBase(BaseModel):
    typ: str = Field(..., description="HAUPTZAEHLER, PARZELLE oder VEREIN")
    parzelle_id: Optional[str] = None
    bezeichnung: Optional[str] = None
    notizen: Optional[str] = None


class ZaehlpunktCreate(ZaehlpunktBase):
    # Erstes Zähler wird direkt mit angelegt
    nummer: str
    geeicht_bis: Optional[int] = None
    eingebaut_am: Optional[date] = None
    anfangsstand: Decimal = Decimal("0")


class ZaehlpunktUpdate(BaseModel):
    bezeichnung: Optional[str] = None
    notizen: Optional[str] = None


class ZaehlerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    nummer: str
    ist_aktiv: bool
    geeicht_bis: Optional[int] = None
    eingebaut_am: Optional[date] = None
    ausgebaut_am: Optional[date] = None
    anfangsstand: Decimal


class ZaehlpunktOut(ZaehlpunktBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    medium: str


class ZaehlpunktDetailOut(ZaehlpunktOut):
    aktueller_zaehler: Optional[ZaehlerOut] = None
    fruehere_zaehler: List[ZaehlerOut] = []


class ZaehlerTauschRequest(BaseModel):
    neue_nummer: str
    ausgebaut_am: date
    eingebaut_am: date
    geeicht_bis: Optional[int] = None
    anfangsstand: Decimal = Decimal("0")


class ZaehlerstandCreate(BaseModel):
    jahr: int
    datum: date
    stand: Decimal
    notiz: Optional[str] = None


class ZaehlerstandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    zaehler_id: str
    jahr: int
    datum: date
    stand: Decimal
    notiz: Optional[str] = None


class VerbrauchZeileOut(BaseModel):
    """Eine Zeile der Verbrauchsauswertung (Zaehlpunkt + berechneter Verbrauch)."""
    zaehlpunkt_id: str
    bezeichnung: str
    zaehler_nummer: Optional[str] = None
    verbrauch: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Versicherungen
# ---------------------------------------------------------------------------

class SachversicherungPaketBase(BaseModel):
    jahr: int
    bezeichnung: str
    betrag_eur: Decimal
    reihenfolge: int = 0


class SachversicherungPaketCreate(SachversicherungPaketBase):
    pass


class SachversicherungPaketOut(SachversicherungPaketBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class VersicherungsKonfigurationBase(BaseModel):
    jahr: int
    unfall_grundbetrag_eur: Decimal
    unfall_zusatzbetrag_eur: Decimal


class VersicherungsKonfigurationCreate(VersicherungsKonfigurationBase):
    pass


class VersicherungsKonfigurationOut(VersicherungsKonfigurationBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class ParzelleVersicherungUpdate(BaseModel):
    hat_sachversicherung: bool = False
    sach_paket_id: Optional[str] = None
    hat_unfallversicherung: bool = False
    zusatzpersonen_mitglied_ids: List[str] = []


class ParzelleVersicherungOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    parzelle_id: str
    jahr: int
    hat_sachversicherung: bool
    sach_paket_id: Optional[str] = None
    hat_unfallversicherung: bool


class ParzelleVersicherungKostenOut(ParzelleVersicherungOut):
    zusatzpersonen_mitglied_ids: List[str] = []
    sach_kosten_eur: Decimal
    unfall_kosten_eur: Decimal
    gesamt_kosten_eur: Decimal
