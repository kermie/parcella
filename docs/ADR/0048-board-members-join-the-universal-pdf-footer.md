# Board members join the universal PDF footer

**Context:** [ADR 0047](./0047-club-settings-board-members.md) added a
board-members list to admin -> settings -> club settings. GitHub issue
#121 asked to print it on every generated PDF: "Place: second column,
right under registration court value in the following format: Board
members: board member 1, board member 2 etc."

**Decision:**

1. **One more field on `OrgFooterContext`, not a new footer mechanism.**
   [ADR 0045](./0045-universal-pdf-footer-and-flyer-chrome.md) already
   made the org/register/bank footer universal across every
   PDF-producing generator via a single `OrgFooterContext`/
   `org_footer_html()` in `app/pdf_chrome.py`. Board members are just
   another field on that same context (`board_members: List[str]`),
   populated by `load_org_footer_context()` alongside the existing
   `ClubSetting` reads -- every PDF automatically gets the line with no
   per-generator changes needed, the same reasoning ADR 0045 already
   established for bank details.

2. **Rendered as an appended line in the register-info column
   (column 2), not a fourth column.** Per the issue's explicit
   placement request. When there's no register-court value at all, the
   board-members line simply becomes that column's only line (same
   "blank field just omits its line" convention every other footer
   field already follows) rather than being suppressed.

3. **Scoped to active members, re-resolved on every PDF render, not a
   snapshot.** `load_org_footer_context()` joins `ClubBoardMember` to
   `Member` through the same `active_member_filter()` used by the admin
   settings picker (ADR 0047), so a member whose membership has since
   lapsed (or who was soft-deleted) stops appearing on freshly
   generated documents automatically -- no cleanup job needed, and
   nothing invalidates historical/already-generated PDFs (those already
   have the names baked into rendered content, same as every other
   footer field).
