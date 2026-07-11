# Modul: Einkaufswünsche (Vier-Augen-Prinzip)

Kontrollmechanismus für Vereinsausgaben: Ein Antrag muss von zwei
unterschiedlichen Vorstandsmitgliedern freigegeben werden, bevor
eingekauft werden darf. Entstanden, weil zuvor Mitglieder frei einkauften
und im Nachhinein abrechneten – "komplett gegen alle Regeln".

Modul-Flag: `einkaufswuensche`

## Datenmodell

```
einkaufswuensche          – Der Antrag: Titel, Begründung, Link, Kosten, Status
einkaufswunsch_freigaben  – Einzelne Freigaben (wer, wann) – braucht 2 pro Antrag
```

## Wichtige Entscheidungen

**Wer darf was.** Jeder eingeloggte Benutzer kann einen Einkaufswunsch
anlegen – nur Freigeben/Ablehnen ist Vorstand/Admin vorbehalten
(`require_admin` aus `app/auth.py`, das trotz des Namens auch die Rolle
VORSTAND einschließt). Das spiegelt die reale Praxis: viele stellen
Anträge, aber eine klar abgegrenzte Gruppe entscheidet.

**Selbstfreigabe ausgeschlossen.** Weder der Antragsteller
(`angefragt_von_id`) noch die Person, die den Antrag ins System eingetragen
hat (`erstellt_von_id`, relevant bei stellvertretender Anlage), darf eine
der beiden nötigen Freigaben selbst geben. Ohne diese Sperre wäre das
Vier-Augen-Prinzip wirkungslos – wer den Antrag stellt, könnte sich sonst
selbst mitgenehmigen.

**Ablehnung braucht nur eine Person (Veto-Prinzip), Freigabe braucht
zwei.** Bewusst asymmetrisch: Die Freigabe von Geld soll ein Konsens von
zwei Personen sein (Schutz vor Fehlentscheidungen), aber jede einzelne
Person im Vorstand soll einen Antrag stoppen können, ohne dafür eine
zweite Person überzeugen zu müssen. Ein Veto ist eine Schutzmaßnahme, kein
Machtinstrument, das absichtlich erschwert werden sollte.

**Deep-Link-Bestätigung für Antragsteller ohne Login.** Wenn der Vorstand
einen Antrag stellvertretend für jemanden anlegt (z.B. weil die Person
keinen App-Zugang hat oder den Wunsch nur mündlich/telefonisch geäußert
hat), wird ein Bestätigungs-Token erzeugt (`itsdangerous`-Serializer,
gleiches Muster wie die Einladungs-Tokens in `app/auth.py`) und per E-Mail
verschickt. Der Link führt zu einer **öffentlichen** Seite (kein Login
nötig) auf der die Person die Angaben bestätigen kann. Diese Bestätigung
ist rein informativ für den Vorstand ("hat die Person das wirklich so
gemeint?") – sie ist keine Voraussetzung für die Freigabe durch den
Vorstand selbst, sondern zusätzliche Transparenz.

Hat der Antragsteller einen eigenen App-Zugang und legt den Wunsch selbst
an, entfällt der Bestätigungsschritt komplett – er hat die Angaben ja
bereits selbst im System eingegeben.

**Kostenfeld optional, aber vorgesehen.** `geschaetzte_kosten_eur` ist kein
Pflichtfeld (manche Anschaffungen haben noch keinen bekannten Preis), aber
naheliegend bei einem Ausgaben-Freigabeprozess – daher von Anfang an im
Datenmodell statt später nachzurüsten.

## REST-API

Vollständig von Anfang an (`/api/v1/einkaufswuensche`), analog zu den
anderen Modulen. Bemerkenswert: `freigeben` und `ablehnen` nutzen
`require_api_rolle(BenutzerRolle.ADMIN, BenutzerRolle.VORSTAND)` statt des
generischen `require_schreibzugriff` (der auch Kassierer einschließt) –
die Freigabeberechtigung ist hier bewusst enger gefasst als üblicher
Schreibzugriff.
