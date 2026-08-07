# TREASURER role's privilege retired: Groups are the only way to grant more than baseline now

**Context:** amends [ADR 0041](./0041-groups-supersede-roles-for-new-user-access.md).
That decision made Groups an *additive* alternative to
ADMIN/BOARD roles, explicitly keeping `UserRole.TREASURER`/`READONLY`
as an inert baseline everywhere -- except one place it never got
updated: `app/api_auth.py`'s `require_write_access = require_api_role(ADMIN, BOARD, TREASURER)`
still gave **any** TREASURER-role account unconditional API write
access to every module that dependency gates, regardless of Group
configuration. This was the one remaining place role alone (not Group
membership) granted meaningful privilege beyond the baseline.

kermie: "the treasurer role can be completely ditched as it is not
necessary any longer as we have groups where we can define fine
grained access rules for each and every newly invited person... the
treasurer is always member of the board and shall get all access but
administration" -- i.e. a treasurer's real-world access ("full access,
no admin panel") is already exactly what a `grants_full_access` Group
provides (or BOARD role itself); there's no distinct "treasurer"
permission shape that isn't already expressible as a Group. Confirmed
this explicitly amends ADR 0041 rather than sitting alongside it.

## Decision

`require_write_access` drops `TREASURER` from its allow-list:

```python
require_write_access = require_api_role(UserRole.ADMIN, UserRole.BOARD)
```

TREASURER now behaves identically to READONLY everywhere in the app --
baseline access only, widened purely by Group membership -- which is
what ADR 0041 already intended for it and what the HTML side
(`app/permissions.py`, which never special-cased TREASURER to begin
with) already did.

**The `UserRole.TREASURER` enum value itself is NOT removed.** Postgres
has no `ALTER TYPE ... DROP VALUE` (see this project's own sharp-edge
notes on enum handling) -- dropping it from the Python `enum.Enum`
without a full enum-type-rebuild migration would break deserializing
any existing row still holding that value. Since the value is now
functionally inert (identical to READONLY), there's no behavioral
reason to attempt that rebuild. `TREASURER` simply stops being
privileged anywhere, the same status READONLY already has. Consistent
with ADR 0041's own precedent (`Invitation.role` keeping the column
but always writing `READONLY` as an inert default rather than touching
the DB enum).

No admin UI change needed: per ADR 0041, role hasn't been an assignable
dropdown since that change shipped -- `app/routers/admin.py`'s only
role-related admin action is stripping a legacy ADMIN/BOARD role back
down, never assigning TREASURER. TREASURER can now only exist on
accounts created before ADR 0041; there is currently no such account in
this installation's database (checked directly: `SELECT DISTINCT role
FROM users` returns only `READONLY`).

## Behavior change on upgrade

Flagging explicitly, same as ADR 0070's tickets change: any existing
installation with a TREASURER-role account that relied on blanket API
write access (e.g. an automation authenticating as that account) loses
it on upgrade -- that account needs a Group grant (e.g. membership in a
`grants_full_access` group) to keep writing via the API. HTML access is
unaffected either way, since TREASURER never had special HTML privilege
to begin with.

## Not done here

Rolling `require_api_permission` (ADR 0070's Group-based API check) out
to the other API routers that still use `require_write_access`/
`require_api_role` is unrelated, larger, tracked follow-up work (ADR
0070's checklist) -- this change only removes TREASURER's role-based
bypass of that system, it doesn't yet make Group-scoped access (e.g. "a
water-module-only Group") actually work uniformly across every API
router. That's still pending, module by module.
