# Automatisierte Tests

## Philosophie

**Kein Anspruch auf 100% Testabdeckung.** Das wäre für ein Projekt dieser
Größe ein eigenes Fass ohne Boden. Stattdessen:

1. **Ein "Happy Path"-Test pro Modul** – die grundlegende Funktionalität
   funktioniert (anlegen, abrufen, verknüpfen).
2. **Gezielte Tests für die Stellen mit dem höchsten Regressionsrisiko** –
   also genau die Logik, die schon mal Kopfzerbrechen bereitet hat oder
   bei der ein stiller Fehler besonders schlimm wäre:
   - Vier-Augen-Prinzip bei Einkaufswünschen (Selbstfreigabe-Sperre,
     Doppel-Freigabe-Sperre, Veto-Ablehnung)
   - Zählerstand-Monotonie-Prüfung (darf nicht sinken)
   - Pflichtstunden-Gruppenbefreiung (`any()` statt `all()` bei Parzellen)
   - Versicherungskosten-Berechnung (Grund- + Zusatzbeträge)

## Warum echtes PostgreSQL, nicht SQLite

Mehrere Bugs in diesem Projekt traten **ausschließlich mit PostgreSQL**
auf (z.B. die Enum-Großschreibungs-Problematik, siehe
[Architektur-Entscheidungen](./architektur-entscheidungen.md)). Ein
Testlauf gegen SQLite hätte solche Bugs unsichtbar gemacht, statt sie zu
fangen – SQLite ist in vielen Dingen (Typsystem, Enum-Handling,
Constraint-Durchsetzung) großzügiger als PostgreSQL. Tests laufen daher
gegen eine echte, aber komplett wegwerfbare PostgreSQL-Instanz.

## Ausführen

```bash
./run_tests.sh
```

Das Skript kapselt den kompletten Ablauf: startet eine isolierte
Test-Datenbank (`tmpfs`, verschwindet beim Stoppen), installiert
Test-Abhängigkeiten, führt `pytest` aus, räumt danach auf – auch wenn
Tests fehlschlagen.

Die Test-Datenbank läuft nur mit `docker compose --profile test`, taucht
also bei normalem `docker compose up` nicht auf und stört den laufenden
Betrieb nie.

## Automatisch bei jedem Push

`.github/workflows/tests.yml` führt dieselbe Test-Suite bei jedem Push
und Pull Request auf GitHub aus (eigene, isolierte PostgreSQL-Instanz als
GitHub-Actions-Service, kein Docker-Compose nötig auf CI-Seite). Ein
fehlgeschlagener Test ist dort direkt im Pull Request sichtbar, bevor
etwas nach `main` gemerged wird.

## Wie die Testdatenbank funktioniert

`tests/conftest.py` setzt `DATABASE_URL` auf die Testdatenbank, **bevor**
irgendein Teil der App importiert wird. Das ist wichtig: Python cached
Modul-Importe, und `app/database.py` erstellt die Datenbankverbindung
beim ersten Import aus `settings.database_url` – würde die App vorher
schon einmal mit der Produktions-URL importiert, würden auch interne
Mechanismen (die Modul-Flags-Middleware, die Admin-Anlegen-Logik beim
Start) die falsche Datenbank verwenden. Weil wir die Umgebungsvariable
ganz am Anfang von `conftest.py` setzen, funktioniert das automatisch
richtig, ohne dass wir jede einzelne Stelle im Code eigens überschreiben
müssten.

Vor jedem einzelnen Test werden alle Tabellen geleert (nicht: verschachtelte
Transaktionen mit Rollback). Das ist bewusst die einfachere Lösung: die
Anwendung committet an vielen Stellen selbst mittendrin (z.B. nach jeder
Freigabe eines Einkaufswunsches) – das würde mit reinen
Test-Transaktionen, die am Ende zurückgerollt werden, zu Konflikten
führen. Tabellen leeren ist weniger elegant, aber robust und leicht
nachvollziehbar.

## Lehren aus dem ersten echten Testlauf

Zwei Probleme traten beim allerersten Ausführen auf – beide behoben,
aber dokumentiert, damit sie nicht wiederkehren:

**Test-E-Mail-Adressen dürfen keine reservierten Sonder-Domains
verwenden.** `admin@test.local` wurde von Pydantics `EmailStr`-Validierung
abgelehnt, weil `.local` auf der Liste reservierter Sonder-Namen
(`localhost`, `local`, `test`, `example`, `invalid`, `onion`) steht, die
die zugrunde liegende `email-validator`-Bibliothek als TLD ablehnt.
Test-Adressen verwenden seither `@example.com` – das ist eine ganz normale
`.com`-Domain (nur der zweite Teil heißt zufällig "example"), keine
Sonder-TLD, und Pydantic prüft standardmäßig ohnehin keine tatsächliche
Zustellbarkeit (kein DNS-Lookup).

**`pytest-asyncio` gibt jeder Testfunktion einen eigenen Event-Loop –
das kollidiert mit unserer Singleton-Datenbank-Engine.** Unsere
Datenbank-Engine (`app.database.engine`) ist ein Singleton, das beim
Modul-Import einmalig Verbindungen aufbaut. Läuft jeder Test in einem
eigenen Loop, versucht die Engine irgendwann, eine Verbindung aus einem
bereits beendeten Loop wiederzuverwenden – das äußert sich als
`RuntimeError: ... attached to a different loop`.

Zwei Lösungsversuche über einen eigenen `event_loop`-Fixture-Override
scheiterten an pytest-asyncio-Versionsproblemen: Version 0.24 ignoriert
einen solchen Override für Testfunktionen selbst (nur Fixtures
respektieren ihn); ein Downgrade auf 0.21.1 wiederum ist inkompatibel
mit `pytest` 8.x (`AttributeError: 'FixtureDef' object has no attribute
'unittest'`).

**Die robuste, versionsunabhängige Lösung:** Statt gegen
pytest-asyncios internes Loop-Verhalten anzukämpfen, wird der
Connection-Pool der Engine vor **jedem einzelnen Test** verworfen
(`await engine.dispose()`, als autouse-Fixture `_frische_verbindung`).
Danach entstehen neue Verbindungen automatisch im gerade aktiven Loop,
sobald sie das nächste Mal gebraucht werden – unabhängig davon, welche
pytest-asyncio-Version oder Loop-Scope-Konfiguration gerade gilt. Diese
Fixture muss vor der tabellenleerenden Fixture laufen; das wird über
eine explizite Fixture-Abhängigkeit erzwungen
(`_tabellen_leeren(_frische_verbindung)`), statt sich auf die
Definitionsreihenfolge zu verlassen.

**Lehre:** Bei hartnäckigen Event-Loop-Problemen mit async Datenbank-
Bibliotheken lohnt es sich oft mehr, die Ressource (hier: den
Connection-Pool) explizit zurückzusetzen, als das Verhalten des
Test-Frameworks exakt nachzuvollziehen – letzteres ändert sich
erfahrungsgemäß zwischen Versionen, ersteres nicht.

## SQLAlchemy Identity Map: veraltete Beziehungen trotz erneuter Abfrage

**`_zu_kosten_schema()` zeigte weiterhin 0 € Sachversicherungskosten**,
obwohl der erste `MissingGreenlet`-Fix bereits griff. Ursache: die
Beziehung `pv.sach_paket` wurde einmal geladen (mit Wert `None`), **bevor**
`sach_paket_id` überhaupt gesetzt wurde (nämlich beim Neuanlegen der Zeile
weiter oben in der Funktion). SQLAlchemys **Identity Map** sorgt dafür,
dass ein und dasselbe Python-Objekt innerhalb derselben Session für
denselben Primärschlüssel wiederverwendet wird – ein erneutes Abfragen
mit `selectinload(sach_paket)` überschreibt eine **bereits als geladen
markierte** Beziehung NICHT automatisch, selbst nach einem `commit()`,
solange `expire_on_commit=False` gesetzt ist (was wir bewusst so
konfiguriert haben, um andere Greenlet-Probleme zu vermeiden).

**Lösung:** `await db.refresh(pv, attribute_names=["sach_paket",
"zusatzpersonen"])` statt erneutem `select(...)` – `refresh()` erzwingt
gezielt das Neuladen genau der angegebenen Beziehungen aus der
Datenbank, unabhängig vom bisherigen (u.U. veralteten) Ladezustand des
Objekts.

**Einordnung:** Das ist eine dritte, eigenständige Variante der
"frisch angelegtes Objekt + fehlendes Beziehungs-Reload"-Problemfamilie,
die uns in diesem Projekt schon mehrfach begegnet ist (Ticketsystem,
Versicherungs-Anlegen) – diesmal nicht als komplett fehlendes Reload,
sondern als ein Reload, das durch die Identity Map wirkungslos blieb.
Bei jedem "Objekt X, dann Feld Y setzen, dann Beziehung Z lesen"-Muster
lohnt sich die Frage: wurde Z schon VOR dem Setzen von Y geladen?

## Weitere Testläufe: ein ernster Geschäftslogik-Bug gefunden

**Die "any() statt all()"-Regel war an zwei von drei Stellen falsch
herum implementiert.** Die Web-Auswertungsseite in `pflichtstunden.py`
hatte die Regel korrekt umgesetzt (`any(p["befreit"] for p in
paechter_details)`), aber sowohl der **CSV-Export** (ebenfalls in
`pflichtstunden.py`) als auch die **REST-API** (`api_pflichtstunden.py`)
implementierten sie beim Nachbau versehentlich als `all()`: eine Variable
namens `alle_befreit` wurde mit `True` initialisiert und bei jedem
nicht-befreiten Pächter auf `False` gesetzt – das ist "ALLE müssen
befreit sein", nicht "MINDESTENS EINER". Der irreführende Variablenname
war vermutlich die Ursache: er wurde offenbar kopiert, ohne die
tatsächliche `any()`-Logik mitzukopieren.

**Das ist kein kosmetischer Bug.** Für Parzellen mit einem befreiten und
einem nicht-befreiten Pächter hätte der CSV-Export (der für die
tatsächliche Abrechnung genutzt wird!) einen Schuldbetrag ausgewiesen,
wo eigentlich keiner fällig ist. Genau diese Art Fehler automatisiert zu
finden, bevor sie in einer echten Abrechnung auftaucht, ist der Grund,
warum wir überhaupt angefangen haben, Tests zu schreiben.

**Behoben an beiden Stellen**, und die durchweg korrekte Variable in der
Web-Auswertungsseite von `alle_befreit` zu `ist_befreit` umbenannt – der
alte Name lud förmlich dazu ein, beim nächsten Kopieren wieder falsch
verstanden zu werden.

**Zusätzlich:** Ein Pydantic-Validierungsfehler in
`api_versicherungen.py`s `_zu_kosten_schema()` – `model_validate(pv)`
wurde direkt auf das Zielschema mit den *berechneten* Kostenfeldern
aufgerufen, die aber keine echten ORM-Spalten sind. Pydantic verlangte
diese Felder deshalb schon beim Validieren, nicht erst beim
nachträglichen Setzen. Behoben, indem zuerst das Basis-Schema (nur echte
Spalten) validiert und die berechneten Felder erst beim Konstruieren des
vollständigen Zielschemas ergänzt werden.

## Erste echte Testläufe: zwei App-Bugs gefunden (nicht nur Test-Infrastruktur)

Nachdem die Event-Loop-Infrastruktur stand, deckten die ersten
tatsächlich laufenden Tests zwei echte, bis dahin unbemerkte Probleme auf:

**`PflichtstundenModus` ist kleingeschrieben, alle anderen Enums
großgeschrieben.** Dieser Enum stammt aus Phase 1 (Migration
`0002_pflichtstunden`), bevor die "Enum-Werte immer großschreiben"-
Konvention eingeführt wurde (siehe Architektur-Entscheidungen). Er wurde
bei der Umstellung nicht nachgezogen. Das ist kein Bug im Sinne von
"funktioniert falsch" – der Code ist intern konsistent (Modell und
Migration verwenden beide `pro_pachtvertrag`) – aber eine **Falle für
alle, die zukünftig Enum-Werte verwenden und großgeschrieben erwarten**,
weil es der einzige Ausreißer im gesamten Projekt ist. Bewusst NICHT
nachträglich vereinheitlicht (das wäre eine weitere Migration nur für
Konsistenz, ohne funktionalen Nutzen) – stattdessen hier dokumentiert,
damit niemand erneut darüber stolpert. Die Tests wurden entsprechend
angepasst (`modus="pro_pachtvertrag"`, klein).

**`MissingGreenlet` auch im Versicherungsmodul, nicht nur im
Ticketsystem.** `api_versicherungen.py`s `versicherung_setzen()` legte
bei Bedarf eine neue `ParzelleVersicherung`-Zeile an, griff aber direkt
danach auf `pv.zusatzpersonen` zu, ohne die Zeile mit `selectinload` neu
zu laden – exakt das gleiche Muster, das wir beim Ticketsystem schon
einmal gefunden und behoben hatten (siehe Architektur-Entscheidungen).
Es war schlicht nicht konsequent auf alle Stellen im Code angewendet
worden, an denen "neu anlegen, dann sofort Beziehung lesen" passiert.
Behoben nach demselben Muster: nach dem Anlegen explizit neu laden.

**Lehre:** Automatisierte Tests fangen genau diese Art von "wir wissen es
eigentlich schon, haben es aber nicht überall angewendet"-Fehlern – das
ist der eigentliche Wert der Testsuite, nicht nur das Verhindern neuer
Bugs, sondern das Aufspüren bereits vorhandener, unbemerkter.

## Bekannte Grenzen (bewusst nicht automatisiert getestet)

- **IMAP-Abruf und SMTP-Versand** (`app/ticket_mailer.py`,
  `app/email_service.py`): erfordern einen echten Mailserver. Diese Pfade
  werden weiterhin manuell getestet (siehe die frühere Diagnose-Sitzung
  mit dem direkten `imaplib`-Testskript). Ein Mocking dieser externen
  Systeme wäre möglich, wurde aber als nicht lohnenswert für den
  aktuellen Projektumfang eingeschätzt – die Fehleranfälligkeit liegt
  eher an echten Netzwerk-/Konfigurationsproblemen als an der eigenen
  Logik, die bereits getestet ist (Threading-Zuordnung, Ticket-Erzeugung).
- **Externe Spam-Prüf-API** (`app/spam_filter.py`, `_externe_pruefung()`):
  gleicher Grund – nur relevant, wenn ein Verein tatsächlich einen
  externen Dienst konfiguriert, was aktuell niemand tut.
- **E-Mail-Versand allgemein** (Einladungen, Zuweisungsbenachrichtigungen):
  `sende_email()` schlägt in der Testumgebung mangels SMTP-Konfiguration
  einfach fehl (gibt `False` zurück) – das ist beabsichtigtes, bereits
  vom Code abgefangenes Verhalten, kein Testfehler.

## Neues Modul? Neue Tests nicht vergessen

Beim Bau eines neuen Moduls (siehe auch die Checkliste in
[docs/README.md](./README.md)) gehört ab sofort auch eine
`tests/test_<modul>.py`-Datei mit mindestens einem Happy-Path-Test dazu –
genau wie Doku und API-Endpunkte inzwischen selbstverständlich sind.
