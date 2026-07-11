# Dokumentation

Diese Dokumentation begleitet die Entwicklung der Gartenverein-Verwaltung
und wird fortlaufend mit jedem neuen Feature ergänzt – nicht nachträglich
am Ende, sondern direkt während der Entstehung, solange die Entscheidungen
und Beweggründe noch frisch sind.

## Module

- [Mitglieder & Parzellen](./module-mitglieder-parzellen.md) – Kernmodul, immer aktiv
- [Pflichtstunden](./module-pflichtstunden.md) – Arbeitseinsätze, Patenschaften, Vereinsrollen
- [Zählerwesen (Wasser & Strom)](./module-zaehlerwesen.md) – gemeinsame Codebasis für beide Medien
- [Versicherungen](./module-versicherungen.md) – Sach- und Unfallversicherung pro Parzelle
- [Ticketsystem](./module-tickets.md) – Support-Tickets, alle 3 Etappen fertig
- [Einkaufswünsche](./module-einkaufswuensche.md) – Vier-Augen-Prinzip für Vereinsausgaben

## Querschnittsthemen

- [Architektur-Entscheidungen](./architektur-entscheidungen.md) – warum manche Dinge so gebaut sind, wie sie sind
- [Betrieb](./betrieb.md) – Docker, Migrationen, SMTP-Einrichtung, Fehlerbehebung

## Für neue Module

Beim Bau eines neuen Moduls hat sich folgendes Muster bewährt (siehe
Architektur-Entscheidungen für Details):

1. Modelle in `app/models.py`, Enum-Werte **immer großschreiben** (siehe
   Lehre aus mehreren Bugs dazu)
2. Migration in `migrations/versions/`, Revisionsname unter 32 Zeichen
3. Router mit `dependencies=[Depends(require_modul("<name>"))]`
4. Eintrag in `app/module_flags.py` (`MODULE_DEFAULTS`)
5. Eintrag in `app/routers/admin.py` (`MODULE_FELDER`) für die Ein-/Ausschalt-Oberfläche
6. Navigationsblock in `app/templates/base.html` als aufklappbare `nav-group`
7. Eine neue Seite hier in `docs/` schreiben, solange die Entscheidungen noch frisch sind
