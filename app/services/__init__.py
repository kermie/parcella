"""
Shared business-logic layer, called by both the Jinja2 HTML routers
(app/routers/<module>.py) and the JSON API routers
(app/routers/api_<module>.py) for the same module -- see ADR 0070.

Extends the pattern app/meter_utils.py already established (pure
functions imported independently by both metering routers) to also
cover persistence, audit logging, and notification side effects, not
just validation math.
"""
