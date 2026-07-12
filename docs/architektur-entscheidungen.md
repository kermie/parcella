# Architektur-Entscheidungen

Warum manche Dinge so gebaut sind, wie sie sind – gesammelt, damit
Entscheidungen nicht verloren gehen und nicht versehentlich rückgängig
gemacht werden.

## Modul-System: Ein-/Ausschaltbare Funktionsbereiche

**Kontext:** Nicht jeder Kleingärtnerverein braucht Pflichtstunden,
Wasserverwaltung oder Stromverwaltung. Ein Verein ohne eigene Wasserleitung
soll damit nicht in der Oberfläche behelligt werden.

**Entscheidung:** Statt eines vollwertigen Plugin-Systems (externe Pakete,
Hooks, Sandboxing) – was für ein einzelnes Team ohne Security-Review-Prozess
ein unnötiges Risiko wäre – gibt es ein leichtgewichtiges Feature-Flag-System:

- Jedes Modul hat einen Schlüssel `modul_<name>` in der
  `vereinseinstellungen`-Tabelle (Boolean als String `"true"`/`"false"`)
- `app/module_flags.py` lädt diese Flags **einmal pro Request** über eine
  Middleware und legt sie unter `request.state.module_flags` ab
- Router-Dependencies (`require_modul("<name>")`) sperren ganze Router,
  falls deaktiviert (404 statt der Seite)
- Templates blenden Navigationsblöcke bedingt aus
- **Keine Migration nötig für neue Module** – die Flags leben in der
  bestehenden Key-Value-Tabelle

Die REST-API ist die eigentliche Erweiterbarkeitsfläche für externe
Integrationen (unabhängig programmierbar, jede Sprache) – kein
In-Process-Plugin-System nötig.

## Enum-Werte: Immer Großschreibung

**Der Bug, der mehrfach auftrat:** Mehrere Enums (`ParzelleStatus`,
`EinsatzTyp`, `TeilnahmeStatus`, `BefreiungsGrund`) hatten in Python
kleingeschriebene Werte (`"aktiv"`), aber die allererste Migration
(`0001_initial`) hatte die PostgreSQL-Enum-Typen mit **Großschreibung**
angelegt (`AKTIV`). SQLAlchemy sendet bei einem `str`-Enum den *Wert* (nicht
den Namen) als Parameter – Kleinschreibung in Python + Großschreibung in
der DB führte zu `invalid input value for enum`.

**Konvention seither:** Alle Enum-Werte werden großgeschrieben definiert,
identisch zum Enum-Namen:

```python
class ParzelleStatus(str, enum.Enum):
    AKTIV = "AKTIV"
    GEKUENDIGT = "GEKUENDIGT"
    GELOESCHT = "GELOESCHT"
```

Beim Anlegen einer neuen Migration mit `sa.Enum(...)` immer explizit die
gewünschten (großgeschriebenen) String-Werte angeben, **nicht** verlassen
auf automatisch generierte Werte von `alembic revision --autogenerate` –
die übernehmen manchmal die Python-Enum-*Namen*, was zufällig passt, aber
nicht garantiert ist.

**Anzeige bleibt trotzdem klein:** Damit die Oberfläche nicht
`AKTIV`/`GEKUENDIGT` anzeigt, wird in Templates der Jinja-Filter `|lower`
verwendet: `{{ p.status.value|lower }}`.

## Router-Factory für strukturell identische Module

Siehe [Zählerwesen-Dokumentation](./module-zaehlerwesen.md) für das
Router-Factory-Muster. Kurzfassung: wenn zwei Module (Wasser/Strom)
strukturell identisch sind und sich nur in Konfigurationswerten
unterscheiden (Einheit, Icon, Dezimalstellen), lohnt sich eine
Fabrikfunktion statt Code-Duplikation.

## Datenbankverbindungen: pool_recycle

**Der Bug:** Nach längerer Inaktivität (z.B. über Nacht) schlugen
Datenbankzugriffe gelegentlich mit `MissingGreenlet` beim
Connection-Pool-Ping fehl. Ursache: `pool_pre_ping=True` allein reicht
nicht – ohne `pool_recycle` bleiben Verbindungen im Pool potenziell zu
lange offen, werden vom Netzwerk/Postgres irgendwann stillschweigend
beendet, und der Ping-Mechanismus kollidiert dann mit dem asynchronen
Treiber.

**Fix:** `pool_recycle=1800` (30 Minuten) in `app/database.py` – erneuert
Verbindungen proaktiv, bevor sie stale werden können.

## Historisierung: Beenden statt Löschen

Wiederkehrendes Muster im ganzen Projekt: Pächter-Zuordnungen, Wasseruhren,
Vereinsrollen-Mitgliedschaften werden bei "Ende" nicht gelöscht, sondern
über ein `bis`-Datum (oder `ist_aktiv`-Flag + Ausbaudatum) beendet. Die
Historie bleibt dadurch durchsuchbar ("Wer war 2019 Pächter von G042?"),
ohne eine separate Archiv-Tabelle zu brauchen.

## Passwörter: Hashing vs. Verschlüsselung

Zwei unterschiedliche Bedürfnisse, zwei unterschiedliche Werkzeuge:

- **Login-Passwörter** (Benutzer der App): **bcrypt** (Hash, Einbahnstraße).
  Die App muss das Original nie wiederherstellen, nur vergleichen.
- **SMTP-Passwort** (für den Mailversand): **Fernet/AES** (Verschlüsselung,
  umkehrbar) in `app/crypto_utils.py`. Die App muss sich damit tatsächlich
  beim Mailserver anmelden, ein Hash wäre hier nutzlos.

Der Verschlüsselungsschlüssel wird per SHA-256 aus `SECRET_KEY` abgeleitet
(nicht zum Hashen eines Passworts, sondern um einen 32-Byte-Schlüssel in
der von Fernet geforderten Form zu erzeugen). Ändert sich `SECRET_KEY`,
werden bereits verschlüsselte Werte unlesbar – `SECRET_KEY` sollte daher
stabil und geheim bleiben.

## SQLAlchemy Async: Lazy-Loading-Fallstrick

Frisch angelegte Objekte (`db.add()` + `commit()`, nicht per Query mit
`selectinload` geladen) haben ihre `relationship`-Felder nicht eager
geladen. Ein späterer synchroner Zugriff darauf (`objekt.beziehung`) löst
einen Lazy-Load aus, der mit dem asynchronen Datenbanktreiber zu
`MissingGreenlet` führt. Betroffen waren `ParzelleVersicherung.zusatzpersonen`
und `Zaehlpunkt`-Beziehungen.

**Regel:** Nach dem Neuanlegen einer Zeile mit Beziehungen, die später
gebraucht werden, die Zeile explizit mit `selectinload(...)` neu laden,
statt das ursprüngliche (frisch erzeugte) Objekt weiterzuverwenden.

## Kernmodul auf Englisch umgestellt (Mitglieder/Parzellen → Members/Parcels)

**Warum jetzt, nicht später:** Solange nur ein Verein die Software
produktiv nutzt, ist jeder Zeitpunkt günstiger als der nächste. Sobald
externe Vereine oder Contributors dazukommen, wird jede Umbenennung von
Tabellen, URLs und API-Endpunkten zum Breaking Change. Der Verein hat
sich daher bewusst für eine rigorose, vollständige Umstellung
entschieden – kein halbes Ergebnis, auch wenn es kurzfristig mehr
Aufwand bedeutet.

**Modulweise vorgegangen, Kernmodul zuerst.** Mitglieder/Parzellen sind
in praktisch jedem anderen Modul über Fremdschlüssel verankert
(Pflichtstunden, Zählerwesen, Versicherungen, Tickets, Einkaufswünsche) –
daher mussten sie zuerst umgestellt werden, als Vorlage für alle
folgenden Module. Andere Module behalten bewusst vorerst ihre deutschen
Bezeichner (Tabellen, eigene Spalten, URLs) – nur ihre Fremdschlüssel-
Verweise auf die neuen `members`/`parcels`-Tabellen und die
`Member`/`Parcel`-Klassennamen wurden zwingend mitgezogen, sonst wäre die
Anwendung nach diesem Schritt nicht mehr lauffähig gewesen.

**CamelCase-Wortgrenzen als Stolperstein bei automatisierten Umbenennungen.**
Ein Skript mit `\bMitglied\b`/`\bParzelle\b`-Wortgrenzen-Regex trifft
zusammengesetzte Klassennamen wie `MitgliedVereinsrolle` oder
`ParzelleVersicherung` NICHT (kein Regex-Wortgrenzen-Übergang zwischen
Kleinbuchstabe und Großbuchstabe in camelCase) – das war hier
gewünscht (diese Klassen gehören anderen Modulen, eigene Runde später),
hätte bei "MitgliedParzelle" (die eigentliche Kernklasse) aber ebenso
zugeschlagen, wäre sie nicht vorher explizit als zusammengesetzter
String behandelt worden. Lehre: bei automatisierten Umbenennungen im
Code IMMER zuerst prüfen, welche zusammengesetzten Bezeichner von einer
Wortgrenzen-Regex tatsächlich (nicht) erfasst werden, bevor man sich auf
das Ergebnis verlässt.

**SQLAlchemy Identity Map + Beziehungs-Attributnamen sind eine
Ketten-Falle.** Beim Umbenennen von `relationship()`-Attributen (z.B.
`MitgliedParzelle.mitglied` → `MemberParcel.member`) genügt es nicht,
nur die Definition zu ändern – JEDER Aufrufer, der `.mitglied` auf einem
`MemberParcel`-Objekt liest, bricht mit `AttributeError`. Diese Zugriffe
sind über viele Module verteilt (Pflichtstunden-Auswertung,
Versicherungs-Haushalts-Erkennung, Dashboard-Statistiken), da
`Parcel.member_assignments`/`Member.parcel_assignments` fast überall
durchlaufen werden. Eine reine `\bmitglied\b`-Wortgrenzen-Regex hätte
das nicht sauber von unrelated lokalen Variablen gleichen Namens in
ANDEREN, noch nicht umgestellten Modulen unterscheiden können – hier war
gezieltes, dateiweises Prüfen nötig statt eines blinden globalen Ersatzes.

**Verwaiste Schema-Felder beim Aufräumen entdeckt.** Sowohl
`ParzelleUpdate.kuendigung_datum` (Pydantic) als auch die entsprechende
Spalte hätten längst entfernt sein sollen (Migration 0006 hatte die
DB-Spalte bereits gelöscht) – nur das API-Schema hinkte hinterher. Beim
gründlichen Durchgehen für die Umbenennung fiel das auf und wurde
gleich mitbereinigt.

## API-first ab sofort verbindlich

**Der Lückenfund:** Nach dem Bau von Pflichtstunden, Zählerwesen und
Versicherungen stellte sich heraus, dass nur die ursprünglichen
Phase-1-Module (Mitglieder, Parzellen) REST-API-Endpunkte hatten – die
drei neueren Module existierten nur als Web-Oberfläche. Das widersprach
der eigentlichen Idee, dass die REST-API die zentrale Erweiterbarkeitsfläche
für externe Integrationen sein soll (siehe Diskussion zum Plugin-System).

**Regel ab sofort:** Jedes neue Modul bekommt von Anfang an sowohl eine
Web-Oberfläche (Jinja2-Router) als auch REST-API-Endpunkte
(`app/routers/api_<modul>.py`), nicht nacheinander. Bestehende Lücken
(Pflichtstunden, Zählerwesen, Versicherungen) wurden nachgezogen.
