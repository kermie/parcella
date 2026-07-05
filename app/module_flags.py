"""
Modul-Flags: Ein-/Ausblenden optionaler Funktionsbereiche.

Konzept:
- Jedes optionale Modul hat einen Schlüssel "modul_<name>" in der
  Vereinseinstellungen-Tabelle (z.B. "modul_pflichtstunden").
- Fehlt der Schlüssel (z.B. bei bestehenden Installationen ohne
  explizite Einstellung), gilt der Default in MODULE_DEFAULTS
  (bewusst True, damit bestehende Nutzer nichts verlieren).
- Die Flags werden einmal pro Request in einer Middleware geladen
  und unter request.state.module_flags abgelegt – Templates und
  Router-Dependencies lesen von dort, ohne erneut die DB zu fragen.

Neues Modul hinzufügen:
1. Eintrag in MODULE_DEFAULTS mit sprechendem Namen und Default-Wert.
2. Eintrag in MODULE_FELDER (admin.py) für die Einstellungsseite.
3. Router mit `dependencies=[Depends(require_modul("<name>"))]` schützen.
4. Navigation in base.html mit `{% if request.state.module_flags.<name> %}` umschließen.
"""
from typing import Dict

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Vereinseinstellung

# Default-Zustand pro Modul, falls kein expliziter Wert in der DB steht.
# Bewusst True für bestehende Module, damit ein Update nichts "kaputt macht".
MODULE_DEFAULTS: Dict[str, bool] = {
    "pflichtstunden": True,
}


def _wert_zu_bool(wert: str) -> bool:
    return wert.strip().lower() in ("true", "1", "ja", "an")


async def lade_modul_flags(db: AsyncSession) -> Dict[str, bool]:
    """Lädt alle Modul-Flags aus der Datenbank, ergänzt um Defaults."""
    result = await db.execute(
        select(Vereinseinstellung).where(Vereinseinstellung.schluessel.like("modul_%"))
    )
    gespeichert = {e.schluessel: e.wert for e in result.scalars().all()}

    flags = dict(MODULE_DEFAULTS)
    for name in MODULE_DEFAULTS:
        wert = gespeichert.get(f"modul_{name}")
        if wert is not None:
            flags[name] = _wert_zu_bool(wert)
    return flags


def require_modul(modul_name: str):
    """
    Dependency-Factory für Router: sperrt alle Endpunkte eines Routers,
    falls das Modul deaktiviert ist. Liest aus request.state.module_flags
    (von der Middleware gesetzt), fragt NICHT erneut die Datenbank.
    """

    async def checker(request: Request):
        flags = getattr(request.state, "module_flags", {})
        if not flags.get(modul_name, MODULE_DEFAULTS.get(modul_name, True)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dieser Funktionsbereich ist in diesem Verein deaktiviert.",
            )

    return checker
