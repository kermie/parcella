"""
Gemeinsam genutzte Jinja2Templates-Instanz.

Vorher hatte jeder Router seine eigene Jinja2Templates(...)-Instanz mit
jeweils eigenem Environment – das hätte bedeutet, die `t`-Übersetzungsfunktion
(siehe app/i18n.py) in jedem einzelnen Router separat registrieren zu
müssen. Stattdessen: EIN Environment, hier zentral konfiguriert, von allen
Routern importiert.
"""
from fastapi.templating import Jinja2Templates

from app.i18n import jinja_t

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["t"] = jinja_t
