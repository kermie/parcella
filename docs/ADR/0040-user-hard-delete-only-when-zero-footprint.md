# Hard-deleting a user: only when they have zero footprint anywhere

**Context:** Only `is_active` deactivation existed for a `User`
(app/routers/admin.py), following this project's general
historization-over-deletion convention (ADR 0005). The council wanted
a real delete too -- e.g. for an account created by mistake, or a
duplicate invite -- not just "hidden but still listed as inactive
forever."

A full audit of `app/models.py` found 21 foreign keys across 19 models
pointing at `users.id`. 17 are nullable `SET NULL` columns that read as
audit trail if blanked (`MeterReading.recorded_by_id`,
`ChangeHistory.changed_by_id`, `PurchaseRequest.rejected_by_id`,
`TicketMessage.authored_by_id`, etc.) -- deleting the user would
silently erase "who did this" on records that still exist. 4 are
`CASCADE` + `NOT NULL` (`GroupMembership.user_id`,
`PurchaseRequestApproval.user_id`, `CouncilPresence.user_id`,
`CouncilAbsence.user_id`) -- deleting the user would cascade-delete
those rows outright, including real approval and attendance history.

**Decision:** A hard delete (`POST /admin/users/{id}/delete`,
`require_system_admin`) is only offered when `_user_has_history`
(`app/routers/admin.py`) finds zero rows anywhere across all 21 FK
columns. If the user has any footprint at all -- even one old ticket
assignment or a single meeting attendance record -- delete is refused
and the edit page explains why, pointing at deactivate instead. This
keeps hard-delete strictly for "this account never actually did
anything" (a fresh invite accepted then immediately reconsidered, a
typo'd duplicate account, etc.), and preserves ADR 0005's spirit
(never silently lose queryable history) for every account that has a
real footprint.

Same last-ADMIN lockout guard as deactivate/edit-role (`_is_last_admin`,
ADR 0039) applies to delete too -- deleting the last active ADMIN is
blocked the same way deactivating or demoting them is.

**Not attempted:** any kind of "reassign this history to another user
first, then delete" flow. That's real added complexity (which of the
21 columns get reassigned to whom, and is that even meaningful for
things like `PurchaseRequestApproval` where the identity of the
approver *is* the record) for a use case ("I want this specific
person's name gone from the system's history") this project isn't
trying to solve -- deactivation already removes their access, which is
the actual operational need in that case.
