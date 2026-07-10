"""
Spam-Filter-Schnittstelle für das Ticketsystem.

Etappe 1/2: No-Op – jede Nachricht gilt als unbedenklich (spam_verdacht=False,
spam_score=None). Etappe 3: hier wird die Anbindung an einen externen Dienst
(z.B. Spamhaus-Abfrage, Akismet, ein selbst gehosteter Bayes-Filter o.ä.)
eingebaut, ohne dass Aufrufer (Ticket-Erstellung) sich ändern müssen.
"""
from typing import Optional
from dataclasses import dataclass


@dataclass
class SpamPruefungsErgebnis:
    ist_spam_verdacht: bool
    score: Optional[float] = None
    begruendung: Optional[str] = None


async def pruefe_auf_spam(absender_email: str, betreff: str, inhalt: str) -> SpamPruefungsErgebnis:
    """
    Prüft eine eingehende Nachricht auf Spam-Verdacht.

    Aktuell ein No-Op (immer "kein Verdacht") – die Funktionssignatur ist
    bewusst schon so gestaltet, dass eine echte Prüfung (externer Dienst,
    Heuristiken) später eingesetzt werden kann, ohne Aufrufer anzupassen.
    """
    return SpamPruefungsErgebnis(ist_spam_verdacht=False, score=None, begruendung=None)
