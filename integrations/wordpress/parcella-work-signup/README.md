# Parcella Work Session Signup (WordPress connector)

A thin, reference WordPress plugin that connects a public work-session
signup form to a Parcella installation's public signup API. It contains
no business logic of its own -- capacity checks, parcel validation, and
signup storage all happen in Parcella. This plugin only:

1. Fetches the current upcoming sessions and parcel list from Parcella
   (server-side, unauthenticated -- these are public read endpoints).
2. Renders a form via the `[parcella_work_signup]` shortcode.
3. Submits signups back to Parcella's public API, server-side, using an
   API token that never reaches the visitor's browser.

This is meant as both a usable plugin and a reference for writing an
equivalent connector for another CMS (TYPO3, Contao, ...): the contract
is the three HTTP endpoints, not this PHP code.

## Installation

1. Copy the `parcella-work-signup` folder into your WordPress
   installation's `wp-content/plugins/` directory.
2. Activate "Parcella Work Session Signup" under Plugins.
3. Go to Settings -> Parcella Connector and fill in:
   - **Parcella base URL** -- e.g. `https://parcella.your-club.org`
   - **API token** -- from Parcella's admin area under
     Administration -> Integrations
4. In Parcella, make sure the "Public signup API" module is enabled
   (Administration -> Settings -> optional modules) -- it is off by
   default.
5. Place `[parcella_work_signup]` on any page or post.

## Notes

- The session list is cached for 60 seconds and the parcel list for an
  hour (WordPress transients), so a busy page doesn't hit Parcella on
  every single page view.
- A hidden honeypot field is included and forwarded to Parcella as-is;
  Parcella decides what to do with it.
- Styling is deliberately minimal (a few inline rules for the honeypot
  and feedback messages) so it inherits your theme's form styling.
  Override `.parcella-work-signup` in your theme's CSS as needed.
- The form only collects a parcel number, an optional name, and
  optional remarks -- no phone or email field. Once a signup matches
  (or falls back to registering) real Parcella members, their contact
  details already live on the Member record; collecting them again on
  the public form would just be redundant data with nowhere useful to
  go. If your fork of this plugin needs them for some other reason, the
  underlying Parcella API still accepts optional `phone`/`email` fields
  in the signup payload -- just add the inputs back and include them in
  the array built in `parcella_signup_render_shortcode()`.
- The name field's HTML `name` attribute is `parcella_signup_name`, not
  `name` -- WordPress reserves `name` as a core query variable (used to
  look up a page/post by slug). A form field literally called `name`
  gets picked up by `WP::parse_request()` and causes a 404 the moment
  it's non-empty, since WordPress tries to find a page with that slug
  instead of rendering the current page. Worth remembering if you're
  customizing this plugin: WordPress also reserves `page`, `paged`,
  `author`, `cat`, `tag`, `feed`, `search`, `attachment`, and several
  others as query variables -- avoid using any of them as a form field
  name on the front end.

## Translations

The form text is translated to German out of the box
(`languages/parcella-work-signup-de_DE.mo`) and follows the WordPress
site's configured language automatically -- no settings needed. For any
other language, copy `languages/parcella-work-signup.pot` to
`parcella-work-signup-{locale}.po` (e.g. `parcella-work-signup-fr_FR.po`
for French), translate the strings, and compile it:

    msgfmt -o parcella-work-signup-{locale}.mo parcella-work-signup-{locale}.po

Drop both files into the `languages/` folder and WordPress picks them
up automatically based on the site's language setting.
