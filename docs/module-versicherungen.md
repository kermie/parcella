# Modul: Versicherungen (Insurance)

> **Hinweis zur Umbenennung:** Der Code (Modelle, Tabellen, URLs,
> API-Endpunkte) wurde vollständig auf Englisch umgestellt:
> `SachversicherungPaket` → `PropertyInsurancePackage`,
> `VersicherungsKonfiguration` → `InsuranceConfiguration`,
> `ParzelleVersicherung` → `ParcelInsurance`,
> `UnfallversicherungZusatzperson` → `AccidentInsuranceAdditionalPerson`,
> `/versicherungen/` → `/insurance/`. Details und Lehren dazu in
> [Architektur-Entscheidungen](./architektur-entscheidungen.md).
> Diese Seite beschreibt weiterhin die fachliche Logik, die sich dabei
> nicht geändert hat.

Verwaltet zwei optionale, pro Parzelle abzuschließende Versicherungen:
Sachversicherung/property insurance (wählbares Paket) und
Unfallversicherung/accident insurance (mit automatischer
Haushalts-Erkennung).

Modul-Flag: `insurance`

## Datenmodell

```
property_insurance_packages         – Konfigurierbare Pakete pro Jahr (z.B. 40/60/80/100 €)
insurance_configuration              – Jahresbasis: Unfall-Grund- und Zusatzbetrag
parcel_insurance                     – Versicherungsstatus einer Parzelle für ein Jahr
accident_insurance_additional_persons – Wer zusätzlich zum Haushalt mitversichert ist
```

## Wichtige Entscheidung: Haushalts-Erkennung per Adressvergleich

Die Unfallversicherung deckt automatisch alle Pächter einer Parzelle ab,
die **dieselbe Adresse** wie der Hauptpächter haben (Straße, PLZ, Ort im
Mitgliederdatensatz) – ohne Aufpreis, weil sie im selben Haushalt leben.

Pächter mit **abweichender Adresse** werden als Kandidaten angezeigt, aber
**nicht automatisch** hinzugefügt – der Verein entscheidet bewusst pro
Person (Checkbox), ob sie gegen den Zusatzbetrag mitversichert werden
sollen. Das war eine explizite Anforderung: "können mitversichert werden"
bedeutet Opt-in, kein Automatismus.

Die Erkennung passiert in `household_grouping()`
(`app/insurance_utils.py`) und ist bewusst **nur eine Anzeige-Hilfe**,
keine harte Regel in der Datenbank – die tatsächliche Abrechnung basiert
auf der expliziten Auswahl in `accident_insurance_additional_persons`,
nicht auf einer Live-Berechnung der Adressen. Das bedeutet: ändert sich
später die Adresse eines Mitglieds, ändert sich nicht rückwirkend die
Abrechnung vergangener Jahre.

## Konfigurierbare Pakete statt fester Werte

Die Sachversicherungs-Pakete (aktuell 40/60/80/100 €) sind eine
eigenständige Tabelle (`property_insurance_packages`), jahresbasiert, mit
frei editierbarer Anzahl und Beträgen – kein hartkodiertes Vier-Pakete-Modell.
Das folgt demselben Prinzip wie die Pflichtstunden-Konfiguration: Werte,
die sich jährlich ändern können, gehören in eine Tabelle, nicht in Code.

## Bekannte Fallstricke

- Gleicher `MissingGreenlet`-Fallstrick wie im Zählerwesen-Modul: beim
  erstmaligen Anlegen einer `ParcelInsurance` (wenn eine Parzelle zum
  ersten Mal für ein Jahr geöffnet wird) müssen die Beziehungen nach dem
  Commit explizit neu geladen werden, bevor auf `property_package` oder
  `additional_persons` zugegriffen wird. Siehe `_get_or_create_pi()`.

## REST-API

Dieses Modul verfügt (nachträglich ergänzt) über es für dieses Modul vollständige
REST-API-Endpunkte (JWT-authentifiziert, siehe `/api/docs`). Siehe README
für die Endpunkt-Übersicht. Hintergrund: anfangs wurden neue Module nur
als Web-Oberfläche gebaut, die API wurde nachträglich nachgezogen – seither
gilt die Regel, dass jedes neue Modul **von Anfang an** sowohl Web-UI als
auch API-Endpunkte bekommt (siehe Architektur-Entscheidungen).
