# Modul: Ticketsystem

Support-Ticketsystem, angelehnt an [Freescout](https://github.com/freescout-help-desk/freescout).
Wird in drei Etappen gebaut – diese Seite beschreibt **Etappe 1**
(Datenmodell + manuelle Ticketverwaltung).

Modul-Flag: `tickets`

## Etappen-Überblick

1. **Datenmodell + manuelle Ticketverwaltung** (fertig) – Tickets, Verlauf,
   Zuweisung, Status, Mitglied-Abgleich. Noch kein automatischer E-Mail-Abruf.
2. **E-Mail-Integration** (geplant) – IMAP-Postfach-Konfiguration,
   Hintergrund-Polling (alle ~2 Min.), eingehende Mails werden zu
   Tickets/Nachrichten, ausgehende Antworten werden tatsächlich per SMTP
   versendet.
3. **Spam-Schnittstelle** (geplant) – die in Etappe 1 vorbereitete
   No-Op-Funktion (`app/spam_filter.py`) wird an einen echten Dienst
   angebunden.

## Datenmodell (Etappe 1)

```
tickets            – Ein Anliegen: Betreff, Status, Zuweisung, Absender,
                      optionale Mitglied-Zuordnung
ticket_nachrichten – Der Gesprächsverlauf eines Tickets (eingehend/
                      ausgehend/intern)
```

## Wichtige Entscheidungen

**Zugriffsrecht über bestehende `BenutzerRolle`**, nicht über ein neues,
unabhängiges Berechtigungssystem – der Verein plant, die Rollen später
ohnehin zu erweitern (z.B. um eine echte "erweiterter Vorstand"-Rolle).
Ein separates Ticket-Zugriffsrecht hätte diese Erweiterung nur verkompliziert.

**Status als explizite Zustandsmaschine**, nicht implizit aus der
Zuweisung abgeleitet:
```
NICHT_ZUGEWIESEN → ZUGEWIESEN → GESCHLOSSEN
                 ↘ ZURUECKGESTELLT (bis Datum) ↗
```
Wird ein Ticket zugewiesen, springt der Status automatisch auf
`ZUGEWIESEN`; wird die Zuweisung aufgehoben, zurück auf `NICHT_ZUGEWIESEN`.

**"Zurückgestellt bis" ist rein berechnet, kein Hintergrundjob.** Ein
Ticket mit Status `ZURUECKGESTELLT`, dessen Datum erreicht ist, wird nicht
automatisch in der Datenbank auf einen anderen Status umgeschaltet.
Stattdessen berechnet die Property `Ticket.ist_faellig` das bei jedem
Anzeigen live (`status == ZURUECKGESTELLT and zurueckgestellt_bis <= heute`).
Das spart einen Hintergrundjob nur für diesen Zweck – der einzige
tatsächlich nötige Hintergrundjob (E-Mail-Polling) kommt ohnehin in Etappe 2.

**Mitglied-Abgleich per E-Mail-Adresse ist bewusst vorsichtig.** Analog
zur Unfallversicherungs-Logik: Ist die Absender-Adresse **eindeutig** einem
Mitglied zuordenbar, geschieht das automatisch. Teilen sich mehrere
Mitglieder dieselbe Adresse (z.B. Ehepaare), trifft die Automatik **keine
Entscheidung** – die Oberfläche zeigt alle Kandidaten zur manuellen Auswahl
an (`finde_mitglieder_per_email()` in `app/ticket_utils.py`).

**Zuweisungs-Benachrichtigung nutzt die bestehende SMTP-Infrastruktur**,
nicht das erst in Etappe 2 kommende Ticket-Postfach. Die allgemeine
Vereins-SMTP-Konfiguration (siehe Wasser/Strom-übergreifende
`app/email_service.py`) reicht dafür bereits aus – ein weiterer Beleg
dafür, dass sich frühere Infrastruktur-Entscheidungen auszahlen.

**Spam-Felder existieren bereits in der Datenbank** (`spam_verdacht`,
`spam_score`), obwohl die eigentliche Prüfung (`app/spam_filter.py`)
noch ein reines No-Op ist. Damit entfällt in Etappe 3 eine weitere
Migration – nur die Prüffunktion selbst muss ausgetauscht werden.

**Änderungshistorie wiederverwendet.** Status- und Zuweisungswechsel
werden über den bereits bestehenden generischen `AenderungsTracker`
protokolliert (`entitaet_typ="Ticket"`) – keine eigene Historie-Tabelle
nötig.

## REST-API

Vollständige API von Anfang an (`/api/v1/tickets`), gemäß der Regel
"API-first ab sofort verbindlich" (siehe Architektur-Entscheidungen).
Tickets anlegen, Status/Zuweisung ändern, Nachrichten hinzufügen – alles
auch programmatisch möglich, z.B. für eine spätere Automatisierung des
E-Mail-Imports in Etappe 2 (die dann wahrscheinlich intern dieselben
Funktionen wie die API nutzt, statt eigene Logik zu duplizieren).
