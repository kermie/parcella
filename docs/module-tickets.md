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
3. **Spam-Schnittstelle** (fertig) – eingebaute Heuristiken plus optionale
   externe API, konfigurierbar unter `/admin/einstellungen`.

## Etappe 3: Spam-Filter

**Zwei kombinierte Ebenen.** Eingebaute Heuristiken laufen sofort, ohne
externen Dienst: Absender-Domain-Sperrliste, Schlüsselwort-Sperrliste,
Anzahl Links im Text. Eine optionale externe API (`app/spam_filter.py`,
`_externe_pruefung()`) wird nur genutzt, wenn eine URL konfiguriert ist –
sie muss lediglich `{"spam_score": 0.0-1.0}` als JSON zurückgeben, damit
beliebige Dienste (Akismet, ein selbst gehosteter Filter, ein kleiner
Adapter vor einem bezahlten Dienst) angebunden werden können, ohne
Aufrufer-Code anzufassen. Der finale Score ist das Maximum aus Heuristik-
und externem Score; schlägt der externe Aufruf fehl, wird stillschweigend
auf die Heuristiken zurückgefallen – ein Ausfall des externen Diensts darf
niemals die Ticketerstellung blockieren.

**Transparenz statt stillem Aussortieren.** Als Spam markierte Tickets
werden nicht gelöscht, sondern nur aus dem Standard-Filter "Aktiv"
ausgeblendet. Ein eigener Filter-Tab "Verdächtig" (mit Zähler-Badge) zeigt
sie weiterhin an, inklusive Score und einer nachvollziehbaren Begründung
(`spam_reasoning`, z.B. "Schlüsselwörter gefunden: casino, gewinn").
Jedes Ticket lässt sich mit einem Klick als "kein Spam" (falsch-positiv)
freigeben – wichtig, weil Heuristiken nie perfekt sind und ein Verein
niemals ein echtes Anliegen versehentlich für immer verlieren soll.

**Spam-Prüfung nur bei neuen Tickets, nicht bei Antworten.** Antwortet
jemand auf ein bereits bestehendes (per Threading zugeordnetes) Ticket,
läuft keine erneute Spam-Prüfung – spart unnötige (ggf. kostenpflichtige)
externe Aufrufe und ist inhaltlich korrekt: eine bereits als legitim
erkannte Konversation muss nicht bei jeder Antwort neu bewertet werden.

**Neue Abhängigkeit:** `httpx` (für die optionale externe API) wurde zu
`requirements.txt` hinzugefügt – erfordert `docker compose build`, nicht
nur `restart`.

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
`process_incoming_mails()` auf. Kein Celery, kein Redis, kein
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

**Erstsynchronisierung überspringt bestehende E-Mails.** Ein Postfach, das
schon lange in Betrieb ist, kann tausende E-Mails enthalten. Der allererste
Abruf würde ohne Sonderbehandlung versuchen, **alle** davon einzeln per
`UID FETCH` abzurufen – das führte in der Praxis zu einem `socket error:
EOF` (Verbindungsabbruch durch den Mailserver bei zu vielen Befehlen in
einer Sitzung). Die Lösung: Ist `ticket_imap_letzte_uid` noch nicht
gesetzt, wird beim ersten Abruf **nur die aktuell höchste UID ermittelt**
(eine einzelne `SEARCH`, kein `FETCH`) und als Startpunkt gespeichert –
ohne eine einzige Mail zu verarbeiten. Ab dem nächsten Zyklus werden dann
ausschließlich neu eingehende E-Mails verarbeitet. Das entspricht auch dem
erwarteten Verhalten: niemand möchte, dass das jahrelange Mail-Archiv
plötzlich komplett als Tickets im System auftaucht.

**Lehre:** Bei jeder Art von "seit dem letzten Mal"-Verarbeitung (IMAP,
aber auch denkbar für andere Polling-Szenarien) den Sonderfall "es gab
noch nie ein 'letztes Mal'" explizit behandeln, statt ihn implizit als
"alles seit Anfang der Zeit" zu interpretieren.

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
`message_id`-Werte vorheriger `TicketMessage`-Einträge abgeglichen.
Schlägt das fehl (z.B. weil der Kunde eine neue Mail statt zu antworten
schreibt, aber mit gleichem Betreff), wird ersatzweise nach Absender-
Adresse + bereinigtem Betreff (ohne "Re:"/"AW:"-Präfix) in offenen
Tickets gesucht. Ohne Treffer entsteht ein neues Ticket.

**Geschlossene Tickets öffnen sich bei neuer Antwort automatisch
wieder** – der Status springt zurück auf `ASSIGNED` (falls weiterhin
zugewiesen) oder `UNASSIGNED`.

**Spam-Prüfung wird bereits aufgerufen, obwohl sie noch ein No-Op ist.**
`pruefe_auf_spam()` aus Etappe 1 wird bei jeder eingehenden Mail bereits
aufgerufen und das Ergebnis in `spam_suspected`/`spam_score` gespeichert –
nur die eigentliche Prüflogik ist noch leer. In Etappe 3 muss daher nur
noch diese eine Funktion ausgetauscht werden, keine Aufrufer.

## Datenmodell (Etappe 1)

```
tickets            – Ein Anliegen: Betreff, Status, Zuweisung, Absender,
                      optionale Mitglied-Zuordnung
ticket_messages     – Der Gesprächsverlauf eines Tickets (eingehend/
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
UNASSIGNED → ASSIGNED → CLOSED
     ↘ DEFERRED (bis Datum) ↗
```
Wird ein Ticket zugewiesen, springt der Status automatisch auf
`ASSIGNED`; wird die Zuweisung aufgehoben, zurück auf `UNASSIGNED`.

**"Zurückgestellt bis" ist rein berechnet, kein Hintergrundjob.** Ein
Ticket mit Status `DEFERRED`, dessen Datum erreicht ist, wird nicht
automatisch in der Datenbank auf einen anderen Status umgeschaltet.
Stattdessen berechnet die Property `Ticket.is_due` das bei jedem
Anzeigen live (`status == DEFERRED and deferred_until <= heute`).
Das spart einen Hintergrundjob nur für diesen Zweck – der einzige
tatsächlich nötige Hintergrundjob (E-Mail-Polling) kommt ohnehin in Etappe 2.

**Mitglied-Abgleich per E-Mail-Adresse ist bewusst vorsichtig.** Analog
zur Unfallversicherungs-Logik: Ist die Absender-Adresse **eindeutig** einem
Mitglied zuordenbar, geschieht das automatisch. Teilen sich mehrere
Mitglieder dieselbe Adresse (z.B. Ehepaare), trifft die Automatik **keine
Entscheidung** – die Oberfläche zeigt alle Kandidaten zur manuellen Auswahl
an (`find_members_by_email()` in `app/ticket_utils.py`).

**Zuweisungs-Benachrichtigung nutzt die bestehende SMTP-Infrastruktur**,
nicht das erst in Etappe 2 kommende Ticket-Postfach. Die allgemeine
Vereins-SMTP-Konfiguration (siehe Wasser/Strom-übergreifende
`app/email_service.py`) reicht dafür bereits aus – ein weiterer Beleg
dafür, dass sich frühere Infrastruktur-Entscheidungen auszahlen.

**Spam-Felder existieren bereits in der Datenbank** (`spam_suspected`,
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
