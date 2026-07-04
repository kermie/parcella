"""
Hilfsfunktionen zur Protokollierung von Feldänderungen (Audit-Log).

Verwendung:
    tracker = AenderungsTracker(parzelle, "Parzelle")
    parzelle.flaeche_qm = neuer_wert
    await tracker.commit(db, benutzer_id)  # schreibt alle erkannten Änderungen
"""
from typing import Any, Optional

from app.models import Aenderungshistorie


def _zu_string(wert: Any) -> Optional[str]:
    """Wandelt einen beliebigen Feldwert in eine vergleichbare/speicherbare Zeichenkette um."""
    if wert is None:
        return None
    if hasattr(wert, "value"):  # Enum
        return str(wert.value)
    return str(wert)


class AenderungsTracker:
    """
    Erfasst den Zustand eines Objekts vor Änderungen und ermittelt
    beim Commit, welche Felder sich geändert haben.
    """

    def __init__(self, objekt, entitaet_typ: str, felder: list[str]):
        self.entitaet_id = objekt.id
        self.entitaet_typ = entitaet_typ
        self.felder = felder
        self.vorher = {feld: _zu_string(getattr(objekt, feld, None)) for feld in felder}
        self.objekt = objekt

    def ermittle_aenderungen(self) -> list[Aenderungshistorie]:
        eintraege = []
        for feld in self.felder:
            neuer_wert = _zu_string(getattr(self.objekt, feld, None))
            alter_wert = self.vorher.get(feld)
            if neuer_wert != alter_wert:
                eintraege.append(
                    Aenderungshistorie(
                        entitaet_typ=self.entitaet_typ,
                        entitaet_id=self.entitaet_id,
                        feldname=feld,
                        alter_wert=alter_wert,
                        neuer_wert=neuer_wert,
                    )
                )
        return eintraege

    async def commit(self, db, benutzer_id: Optional[str] = None):
        for eintrag in self.ermittle_aenderungen():
            eintrag.geaendert_von_id = benutzer_id
            db.add(eintrag)
