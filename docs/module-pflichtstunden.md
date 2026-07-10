# Modul: Pflichtstunden

Verwaltet die jährliche Arbeitsstunden-Pflicht: Standard- und
Sondereinsätze, Patenschaften als Alternative, Vereinsrollen mit
automatischer Befreiung.

Modul-Flag: `pflichtstunden` (siehe `app/module_flags.py`)

## Datenmodell

```
pflichtstunden_konfiguration – Jahresbasierte Stunden/Satz-Konfiguration
vereinsrollen                – Vereinsämter (Vorstand, erweiterter Vorstand etc.)
mitglied_vereinsrolle        – Zuordnung Mitglied → Vereinsrolle (jahresbasiert)
arbeitseinsaetze             – Standard- und Besondere Einsätze
einsatz_teilnahmen           – Wer war bei welchem Einsatz, mit Stunden
patenschaften                – Bereichsverantwortlichkeiten (pauschale Anrechnung)
```

## Wichtige Entscheidungen

**Konfigurierbarer Abrechnungsmodus.** Manche Vereine rechnen Pflichtstunden
pro Mitglied ab, andere pro Pachtvertrag (Parzelle). Beides ist über
`PflichtstundenModus` (`PRO_MITGLIED` / `PRO_PACHTVERTRAG`) einstellbar,
statt fest einprogrammiert zu sein – ein Beispiel für "was gehört
generisch ins Produkt, nicht nur in unseren Verein".

**Pächter-Gruppen bei PRO_PACHTVERTRAG.** Wenn mehrere Personen dieselbe
Parzelle pachten, zählen ihre Stunden zusammen gegen die eine Pflicht der
Parzelle (nicht jeder einzeln). Ein Pächter kann z.B. 2 Stunden leisten,
der andere 3 – zusammen sind die 5 Stunden erfüllt.

**Vereinsrollen-Befreiung gilt für die ganze Parzelle.** Wenn ein Mitglied
im (erweiterten) Vorstand ist und dadurch von Pflichtstunden befreit ist,
gilt diese Befreiung für die **gesamte Parzelle**, nicht nur für die
befreite Person – die Überlegung: der Vorstandsposten "hält dem Rest der
Familie/Mitpächter den Rücken frei". Umgesetzt als `any()` (mindestens ein
Pächter befreit → ganze Parzelle befreit), nicht `all()`.

**Befreiung gilt pro Kalenderjahr, nicht tagesgenau.** Legt ein Vorstand
sein Amt im Oktober nieder, gilt die Befreiung trotzdem für das ganze
Jahr. Erst das Folgejahr unterliegt wieder der normalen Pflicht. Die
Tabelle `mitglied_vereinsrolle` hat ein `jahr`-Feld statt reiner
Datumsspannen, genau aus diesem Grund.

**Patenschaften sind Projekte, keine Zuordnungen.** Ursprünglich zwang die
Oberfläche dazu, beim Anlegen einer Patenschaft sofort ein Mitglied
zuzuordnen. Das wurde geändert: Patenschaften ("Bereiche") können ohne
Mitglied angelegt werden ("noch nicht vergeben") – der reale Workflow ist,
dass der Verein zuerst Patenschaften ausschreibt und dann Bewerber
zuordnet. Mehrere Mitglieder können sich einen Bereich teilen, indem
mehrere Patenschaft-Zeilen mit demselben Bereichsnamen (Autovervollständigung
via `<datalist>`) angelegt werden – jedes bekommt die volle Stundenanrechnung.

**Anrechenbare Stunden werden aus der aktuellen Konfiguration vorbefüllt**,
bleiben aber frei editierbar (z.B. falls eine Patenschaft mehr Aufwand
macht als die Standard-Pflicht).

## Bekannte Fallstricke

- `EinsatzTyp` und `TeilnahmeStatus` mussten (wie mehrere andere Enums)
  nachträglich auf Großschreibung korrigiert werden – siehe
  [Architektur-Entscheidungen](./architektur-entscheidungen.md) für die
  ausführliche Erklärung dieses wiederkehrenden Bugs.

## REST-API

Dieses Modul verfügt (nachträglich ergänzt) über es für dieses Modul vollständige
REST-API-Endpunkte (JWT-authentifiziert, siehe `/api/docs`). Siehe README
für die Endpunkt-Übersicht. Hintergrund: anfangs wurden neue Module nur
als Web-Oberfläche gebaut, die API wurde nachträglich nachgezogen – seither
gilt die Regel, dass jedes neue Modul **von Anfang an** sowohl Web-UI als
auch API-Endpunkte bekommt (siehe Architektur-Entscheidungen).
