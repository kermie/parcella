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
from app.l10n import jinja_money, jinja_number, jinja_address, jinja_address_lines, jinja_currency_symbol

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["t"] = jinja_t
templates.env.globals["address"] = jinja_address
templates.env.globals["address_lines"] = jinja_address_lines
templates.env.globals["currency_symbol"] = jinja_currency_symbol
templates.env.filters["money"] = jinja_money
templates.env.filters["number"] = jinja_number
