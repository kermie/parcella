# Modul: Ticketsystem

Support-Ticketsystem, angelehnt an [Freescout](https://github.com/freescout-help-desk/freescout).
Wird in drei Etappen gebaut – diese Seite beschreibt **Etappe 1**
(Datenmodell + manuelle Ticketverwaltung).

Modul-Flag: `tickets`

## Etappen-Überblick

1. **Datenmodell + manuelle Ticketverwaltung** (fertig) – Tickets, Verlauf,
   Zuweisung, Status, Mitglied-Abgleich. Noch kein automatischer E-Mail-Abruf.
2. **E-Mail-Integration** (fertig) – IMAP-Postfach-Konfiguration,
   Hintergrund-Polling (alle 2 Min.), eingehende Mails werden zu
   Tickets/Nachrichten, ausgehende Antworten werden per SMTP versendet.
3. **Spam-Schnittstelle** (geplant) – die in Etappe 1 vorbereitete
   No-Op-Funktion (`app/spam_filter.py`) wird an einen echten Dienst
   angebunden.

## Etappe 2: E-Mail-Integration

**Ein Postfach für IMAP-Abruf und SMTP-Versand**, getrennt von der
allgemeinen Vereins-SMTP-Konfiguration (die nur für Einladungs-E-Mails
dient). Konfiguration unter `/admin/einstellungen`, Karte
"Ticket-Postfach (IMAP/SMTP)". Beide Passwörter werden verschlüsselt
gespeichert (siehe `app/crypto_utils.py`) – die Passwort-Sonderbehandlung
beim Speichern (leer = unverändert lassen) wurde dafür generalisiert
(`if schluessel.endswith("_password")` statt einem hartkodierten Einzelfall).

**Hintergrund-Polling ohne neuen Dienst.** Ein `asyncio`-Task, gestartet
in der `lifespan`-Funktion von `main.py`, ruft alle 2 Minuten
`verarbeite_eingehende_mails()` auf. Kein Celery, kein Redis, kein
separater Container – passt zum "klein und robust"-Anspruch des Projekts.
Zusätzlich gibt es einen manuellen "Postfach jetzt abrufen"-Button für
sofortiges Testen, ohne auf den nächsten Zyklus zu warten.

**Ein einziges Postfach für alles – auf Wunsch des Vereins.** Ursprünglich
war die Ticket-Postfach-Konfiguration komplett getrennt von der
allgemeinen SMTP-Konfiguration (eigene Felder für Host/Port/Benutzer/
Passwort). Der Verein hat das bewusst vereinfacht: "Wenn ich ein
Ticketsystem sinnvoll nutzen will, braucht es nur eine einzige
E-Mail-Adresse, ein einziges Postfach. Alles andere würde ich auf dem
Server nutzen und ggf. Weiterleitungen einrichten." Die SMTP-Zugangsdaten
(Host, Port, Benutzer, Passwort) werden daher jetzt **einmal** gepflegt
(`app/email_service.py`, `lade_smtp_konfiguration()`) und für Einladungen
**und** Ticket-Antworten wiederverwendet (`app/ticket_mailer.py` importiert
diese Funktion direkt, statt eigene SMTP-Felder zu duplizieren). Nur für
den IMAP-Abruf (Empfang) gibt es zusätzliche Felder (`imap_host`,
`imap_port`, `imap_ssl`) – IMAP-Benutzer/-Passwort sind identisch mit den
SMTP-Zugangsdaten, da es sich um dasselbe Postfach handelt.

**IMAP läuft synchron in einem Thread, nicht async.** Es gibt keine
ausgereifte async-IMAP-Bibliothek in den Standard-Abhängigkeiten. Statt
eine neue hinzuzufügen, nutzt `app/ticket_mailer.py` Python-Bordmittel
(`imaplib`, `email`) synchron, ausgeführt über `asyncio.to_thread(...)`,
damit der Event-Loop währenddessen nicht blockiert.

**Betriebsdaten in der bestehenden Vereinseinstellungen-Tabelle.** Die
zuletzt verarbeitete IMAP-UID und der letzte Fehler landen als
`ticket_imap_letzte_uid` / `ticket_imap_letzter_fehler` in derselben
Key-Value-Tabelle, die auch Modul-Flags und SMTP-Einstellungen speichert –
keine zusätzliche Tabelle/Migration nur für diesen Zweck.

**Threading über Message-ID, mit Fallback.** Eingehende Antworten werden
zuerst über `In-Reply-To`/`References`-Header gegen gespeicherte
`message_id`-Werte vorheriger `TicketNachricht`-Einträge abgeglichen.
Schlägt das fehl (z.B. weil der Kunde eine neue Mail statt zu antworten
schreibt, aber mit gleichem Betreff), wird ersatzweise nach Absender-
Adresse + bereinigtem Betreff (ohne "Re:"/"AW:"-Präfix) in offenen
Tickets gesucht. Ohne Treffer entsteht ein neues Ticket.

**Geschlossene Tickets öffnen sich bei neuer Antwort automatisch
wieder** – der Status springt zurück auf `ZUGEWIESEN` (falls weiterhin
zugewiesen) oder `NICHT_ZUGEWIESEN`.

**Spam-Prüfung wird bereits aufgerufen, obwohl sie noch ein No-Op ist.**
`pruefe_auf_spam()` aus Etappe 1 wird bei jeder eingehenden Mail bereits
aufgerufen und das Ergebnis in `spam_verdacht`/`spam_score` gespeichert –
nur die eigentliche Prüflogik ist noch leer. In Etappe 3 muss daher nur
noch diese eine Funktion ausgetauscht werden, keine Aufrufer.

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
