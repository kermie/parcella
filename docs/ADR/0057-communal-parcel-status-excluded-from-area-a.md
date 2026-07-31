# Communal parcel status: excluded from Area A, Area B stays a manual figure

**Context:** Some parcels are club-managed common areas -- paths, a
playground, a shared shed lot -- not leasable land. Before issue #168,
there was no way to record such a plot as a real `Parcel` (with its own
`area_sqm`, matching how every other physical piece of the grounds is
tracked) without it being wrongly counted as Area A (leased parcels,
`app/area_utils.py`'s `compute_area_a_sqm`), which sums every
non-deleted parcel's area regardless of what it's used for.

**Decision: add `ParcelStatus.COMMUNAL`, exclude it from Area A only.**

- New enum value `COMMUNAL` (migration `0067_communal_parcel_status`,
  `ALTER TYPE parcelstatus ADD VALUE` in an `autocommit_block()`, same
  pattern as 0052/0053/0066).
- `compute_area_a_sqm()` now excludes both `DELETED` (not a real parcel)
  and `COMMUNAL` (real, but not leasable land) -- previously only
  `DELETED` was excluded.
- Freely switchable back to `ACTIVE` via the same status dropdown
  already used for `ACTIVE`/`TERMINATED`/`DELETED` -- confirmed with
  the reporter this needs to stay a simple, reversible toggle (a club
  deciding to lease out a former common-area plot after all), not a
  one-way flag or a separate table.
- No validation added to block assigning a tenant to a `COMMUNAL`
  parcel. Nothing currently forces a tenant assignment onto any parcel,
  so in practice a communal parcel simply never gets one -- confirmed
  acceptable with the reporter rather than adding an extra invariant to
  maintain.

**Decision: Area B stays the existing manual "Total - Area A - Area C"
figure -- it is *not* recomputed as the sum of `COMMUNAL` parcels'
`area_sqm`.** This was the actual fork in the design (see the issue
discussion): summing real communal-parcel records directly would have
been a genuine move away from the hardcoded `flaeche_gesamt_qm`/
`flaeche_c_qm` `ClubSetting`s CLAUDE.md already flags as a named
exception, but the reporter explicitly chose to keep the manual figure
and only fix the Area A leak -- lower risk, and the manual total/C
settings aren't going away as part of this issue. A future issue could
revisit switching Area B to a live sum if the manual settings ever
become a real pain point, but that's explicitly out of scope here.

**Consequence:** `app/main.py`'s dashboard "total area" stat, which
duplicated Area A's query inline instead of calling
`compute_area_a_sqm()`, was refactored to call it directly -- otherwise
the dashboard figure and the settings page's Area A would have silently
diverged the moment a `COMMUNAL` parcel existed, exactly the kind of
two-places-to-keep-in-sync bug this issue was already fixing one
instance of.
