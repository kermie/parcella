"""
Hilfsfunktionen für das Zählerwesen (Wasser + Strom): Verbrauchsberechnung
und Plausibilitätsprüfung. Medium-agnostisch – funktioniert identisch für
Wasseruhren wie für Stromzähler.
"""
from datetime import date
from typing import Optional, List
from decimal import Decimal

from app.models import Zaehler, Zaehlerstand


def sortierte_staende(zaehler: Zaehler) -> List[Zaehlerstand]:
    """Zählerstände eines Zählers, chronologisch nach Jahr sortiert."""
    return sorted(zaehler.zaehlerstaende, key=lambda z: z.jahr)


def stand_vor_jahr(zaehler: Zaehler, jahr: int, exclude_id: Optional[str] = None) -> Decimal:
    """
    Ermittelt den relevanten Vorwert für die Verbrauchsberechnung eines
    bestimmten Jahres: die letzte Ablesung VOR diesem Jahr, oder falls
    keine existiert, den Anfangsstand des Zählers.
    """
    fruehere = [
        z for z in sortierte_staende(zaehler)
        if z.jahr < jahr and z.id != exclude_id
    ]
    if fruehere:
        return Decimal(str(fruehere[-1].stand))
    return Decimal(str(zaehler.anfangsstand))


def stand_nach_jahr(zaehler: Zaehler, jahr: int, exclude_id: Optional[str] = None) -> Optional[Decimal]:
    """Die nächste vorhandene Ablesung NACH einem Jahr, falls vorhanden (für Editier-Plausibilität)."""
    spaetere = [
        z for z in sortierte_staende(zaehler)
        if z.jahr > jahr and z.id != exclude_id
    ]
    if spaetere:
        return Decimal(str(spaetere[0].stand))
    return None


def berechne_verbrauch(zaehler: Zaehler, jahr: int) -> Optional[Decimal]:
    """
    Verbrauch eines Zählers in einem bestimmten Jahr = Ablesung dieses
    Jahres minus letzte Ablesung davor (oder Anfangsstand). Gibt None
    zurück, wenn für dieses Jahr keine Ablesung vorliegt.
    """
    aktuelle = next((z for z in zaehler.zaehlerstaende if z.jahr == jahr), None)
    if not aktuelle:
        return None
    vorwert = stand_vor_jahr(zaehler, jahr, exclude_id=aktuelle.id)
    return Decimal(str(aktuelle.stand)) - vorwert


def pruefe_monotonie(
    zaehler: Zaehler, jahr: int, neuer_stand: Decimal, exclude_id: Optional[str] = None
) -> Optional[str]:
    """
    Plausibilitätsprüfung: der Zählerstand eines Zählers darf über die
    Zeit nicht sinken. Gibt eine Fehlermeldung zurück, falls die Prüfung
    fehlschlägt, sonst None.
    """
    vorwert = stand_vor_jahr(zaehler, jahr, exclude_id=exclude_id)
    if neuer_stand < vorwert:
        return (
            f"Der Zählerstand ({neuer_stand}) darf nicht kleiner sein als der "
            f"vorherige Stand ({vorwert}) desselben Zählers."
        )

    nachwert = stand_nach_jahr(zaehler, jahr, exclude_id=exclude_id)
    if nachwert is not None and neuer_stand > nachwert:
        return (
            f"Der Zählerstand ({neuer_stand}) darf nicht größer sein als der "
            f"bereits erfasste spätere Stand ({nachwert}) desselben Zählers."
        )

    return None


def gesamtverbrauch_fuer_typ(zaehlpunkte: List, jahr: int) -> Decimal:
    """
    Summiert den Verbrauch aller aktiven Zähler einer Liste von
    Zaehlpunkten für ein bestimmtes Jahr. Zaehlpunkte/Zähler ohne
    Ablesung für dieses Jahr tragen 0 bei (statt die Summe zu verfälschen
    oder einen Fehler zu werfen) – die Auswertungsseite weist Lücken
    separat aus.
    """
    gesamt = Decimal("0")
    for zaehlpunkt in zaehlpunkte:
        for zaehler in zaehlpunkt.zaehler:
            verbrauch = berechne_verbrauch(zaehler, jahr)
            if verbrauch is not None:
                gesamt += verbrauch
    return gesamt


# Rundung: wie viele Nachkommastellen werden pro Medium angezeigt/erfasst?
# Wasser wird mit einer Nachkommastelle abgelesen (m³), Strom als Ganzzahl (kWh).
DEZIMALSTELLEN_PRO_MEDIUM = {
    "WASSER": 1,
    "STROM": 0,
}


def runde_fuer_medium(wert: Decimal, medium: str) -> Decimal:
    """Rundet einen Wert auf die für das Medium übliche Nachkommastellen-Anzahl."""
    stellen = DEZIMALSTELLEN_PRO_MEDIUM.get(medium, 1)
    quant = Decimal("1") if stellen == 0 else Decimal("1." + "0" * stellen)
    return wert.quantize(quant)
