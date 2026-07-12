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

from app.models import ParcelStatus, BenutzerRolle


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
# Telefon / E-Mail (Unterobjekte von Member)
# ---------------------------------------------------------------------------

class PhoneBase(BaseModel):
    number: str = Field(..., max_length=50)
    label: Optional[str] = Field(None, max_length=50)
    is_primary: bool = False


class PhoneCreate(PhoneBase):
    pass


class PhoneOut(PhoneBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class EmailAddressBase(BaseModel):
    address: EmailStr
    label: Optional[str] = Field(None, max_length=50)
    is_primary: bool = False


class EmailAddressCreate(EmailAddressBase):
    pass


class EmailAddressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    address: str
    label: Optional[str] = None
    is_primary: bool


# ---------------------------------------------------------------------------
# Member
# ---------------------------------------------------------------------------

class MemberBase(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    date_of_birth: Optional[date] = None
    street: Optional[str] = Field(None, max_length=255)
    postal_code: Optional[str] = Field(None, max_length=10)
    city: Optional[str] = Field(None, max_length=100)
    iban: Optional[str] = Field(None, max_length=34)
    member_since: Optional[date] = None
    member_until: Optional[date] = None
    email_notifications: bool = True
    notes: Optional[str] = None


class MemberCreate(MemberBase):
    pass


class MemberUpdate(BaseModel):
    """Alle Felder optional – für PATCH-artige Teilupdates via PUT."""
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    date_of_birth: Optional[date] = None
    street: Optional[str] = Field(None, max_length=255)
    postal_code: Optional[str] = Field(None, max_length=10)
    city: Optional[str] = Field(None, max_length=100)
    iban: Optional[str] = Field(None, max_length=34)
    member_since: Optional[date] = None
    member_until: Optional[date] = None
    email_notifications: Optional[bool] = None
    notes: Optional[str] = None


class MemberAssignmentBrief(BaseModel):
    """Kompakte Parcel-Info innerhalb einer Member-Antwort."""
    model_config = ConfigDict(from_attributes=True)
    parcel_id: str
    plot_number: str
    is_primary_tenant: bool


class MemberOut(MemberBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    updated_at: datetime
    is_active: bool
    phone_numbers: List[PhoneOut] = []
    email_addresses: List[EmailAddressOut] = []


class MemberDetailOut(MemberOut):
    """Erweiterte Ansicht inkl. zugeordneter Parzellen, für GET /members/{id}."""
    parcels: List[MemberAssignmentBrief] = []


# ---------------------------------------------------------------------------
# Parcel
# ---------------------------------------------------------------------------

class ParcelBase(BaseModel):
    plot_number: str = Field(..., max_length=20)
    area_sqm: Optional[Decimal] = None
    notes: Optional[str] = None


class ParcelCreate(ParcelBase):
    pass


class ParcelUpdate(BaseModel):
    plot_number: Optional[str] = Field(None, max_length=20)
    area_sqm: Optional[Decimal] = None
    status: Optional[ParcelStatus] = None
    termination_note: Optional[str] = None
    notes: Optional[str] = None


class ParcelAssignmentBrief(BaseModel):
    """Kompakte Member-Info innerhalb einer Parcel-Antwort."""
    model_config = ConfigDict(from_attributes=True)
    member_id: str
    name: str
    is_primary_tenant: bool


class ParcelOut(ParcelBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: ParcelStatus
    termination_note: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ParcelDetailOut(ParcelOut):
    members: List[ParcelAssignmentBrief] = []


# ---------------------------------------------------------------------------
# Member-Parcel-Zuordnung
# ---------------------------------------------------------------------------

class AssignmentCreate(BaseModel):
    member_id: str
    parcel_id: str
    is_primary_tenant: bool = True
    assigned_from: Optional[date] = None
    assigned_until: Optional[date] = None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    member_id: str
    parcel_id: str
    is_primary_tenant: bool
    assigned_from: Optional[date] = None
    assigned_until: Optional[date] = None


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
# Work Hours (Pflichtstunden)
# ---------------------------------------------------------------------------

class WorkHoursConfigurationBase(BaseModel):
    year: int
    hours_required: Decimal
    rate_per_hour_eur: Decimal
    mode: str = Field("PER_PARCEL", description="PER_PARCEL oder PER_MEMBER")
    note: Optional[str] = None


class WorkHoursConfigurationCreate(WorkHoursConfigurationBase):
    pass


class WorkHoursConfigurationOut(WorkHoursConfigurationBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class ClubRoleBase(BaseModel):
    name: str
    description: Optional[str] = None
    hours_exempt: bool = False
    exemption_reason: Optional[str] = None


class ClubRoleCreate(ClubRoleBase):
    pass


class ClubRoleOut(ClubRoleBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class MemberClubRoleCreate(BaseModel):
    member_id: str
    club_role_id: str
    year: int
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    note: Optional[str] = None


class MemberClubRoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    member_id: str
    club_role_id: str
    year: int
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    note: Optional[str] = None


class WorkSessionBase(BaseModel):
    title: str
    description: Optional[str] = None
    type: str = Field("STANDARD", description="STANDARD oder SPECIAL")
    date: date
    time_from: Optional[str] = None
    time_until: Optional[str] = None
    max_participants: Optional[int] = None
    hours_per_participant: Optional[Decimal] = None


class WorkSessionCreate(WorkSessionBase):
    pass


class WorkSessionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    date: Optional[date] = None
    time_from: Optional[str] = None
    time_until: Optional[str] = None
    max_participants: Optional[int] = None
    hours_per_participant: Optional[Decimal] = None


class WorkSessionOut(WorkSessionBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class SessionParticipationCreate(BaseModel):
    member_id: str
    status: str = Field("ATTENDED", description="REGISTERED, ATTENDED oder NO_SHOW")
    hours_completed: Optional[Decimal] = None
    note: Optional[str] = None


class SessionParticipationUpdate(BaseModel):
    status: Optional[str] = None
    hours_completed: Optional[Decimal] = None
    note: Optional[str] = None


class SessionParticipationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    session_id: str
    member_id: str
    status: str
    hours_completed: Optional[Decimal] = None
    note: Optional[str] = None


class SponsorshipBase(BaseModel):
    area: str
    description: Optional[str] = None
    credited_hours: Decimal
    valid_from: date
    valid_until: Optional[date] = None


class SponsorshipCreate(SponsorshipBase):
    member_id: Optional[str] = None


class SponsorshipUpdate(BaseModel):
    member_id: Optional[str] = None
    area: Optional[str] = None
    description: Optional[str] = None
    credited_hours: Optional[Decimal] = None
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None


class SponsorshipOut(SponsorshipBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    member_id: Optional[str] = None


class EvaluationRowOut(BaseModel):
    """Eine Zeile der Work-Hours-Jahresauswertung."""
    label: str
    hours_required: Decimal
    hours_completed: Decimal
    hours_open: Decimal
    amount_due_eur: Decimal
    exempt: bool
    fulfilled: bool


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


# ---------------------------------------------------------------------------
# Ticketsystem
# ---------------------------------------------------------------------------

class TicketNachrichtCreate(BaseModel):
    richtung: str = Field("AUSGEHEND", description="EINGEHEND, AUSGEHEND oder INTERN")
    inhalt: str


class TicketNachrichtOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    ticket_id: str
    richtung: str
    inhalt: str
    verfasst_von_id: Optional[str] = None
    erstellt_am: datetime


class TicketCreate(BaseModel):
    betreff: str
    absender_email: EmailStr
    absender_name: Optional[str] = None
    nachricht: str = Field(..., description="Erste Nachricht des Tickets (wird als EINGEHEND gespeichert)")


class TicketStatusUpdate(BaseModel):
    status: str = Field(..., description="NICHT_ZUGEWIESEN, ZUGEWIESEN, ZURUECKGESTELLT oder GESCHLOSSEN")
    zurueckgestellt_bis: Optional[date] = None


class TicketZuweisungUpdate(BaseModel):
    benutzer_id: Optional[str] = Field(None, description="Leer/None = Zuweisung aufheben")


class TicketMemberUpdate(BaseModel):
    mitglied_id: Optional[str] = None


class TicketSpamUpdate(BaseModel):
    spam_verdacht: bool = Field(..., description="false zum Aufheben eines Spam-Verdachts (falsch-positiv)")


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    betreff: str
    status: str
    zugewiesen_an_id: Optional[str] = None
    zurueckgestellt_bis: Optional[date] = None
    mitglied_id: Optional[str] = None
    absender_email: str
    absender_name: Optional[str] = None
    spam_verdacht: bool
    spam_score: Optional[Decimal] = None
    spam_begruendung: Optional[str] = None
    erstellt_am: datetime
    aktualisiert_am: datetime
    geschlossen_am: Optional[datetime] = None


class TicketDetailOut(TicketOut):
    nachrichten: List[TicketNachrichtOut] = []


# ---------------------------------------------------------------------------
# Einkaufswünsche
# ---------------------------------------------------------------------------

class EinkaufswunschCreate(BaseModel):
    titel: str
    begruendung: str
    link: Optional[str] = None
    geschaetzte_kosten_eur: Optional[Decimal] = None
    anfragender_name: Optional[str] = Field(None, description="Nur wenn für eine externe Person angelegt")
    anfragender_email: Optional[EmailStr] = Field(None, description="Nur wenn für eine externe Person angelegt")


class EinkaufswunschAblehnenRequest(BaseModel):
    ablehnungsgrund: str


class EinkaufswunschFreigabeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    benutzer_id: str
    freigegeben_am: datetime


class EinkaufswunschOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    titel: str
    begruendung: str
    link: Optional[str] = None
    geschaetzte_kosten_eur: Optional[Decimal] = None
    status: str
    angefragt_von_id: Optional[str] = None
    anfragender_name: Optional[str] = None
    anfragender_email: Optional[str] = None
    erstellt_von_id: Optional[str] = None
    vom_anfragenden_bestaetigt: bool
    ablehnungsgrund: Optional[str] = None
    abgelehnt_von_id: Optional[str] = None
    abgelehnt_am: Optional[datetime] = None
    genehmigt_am: Optional[datetime] = None
    erstellt_am: datetime


class EinkaufswunschDetailOut(EinkaufswunschOut):
    freigaben: List[EinkaufswunschFreigabeOut] = []
