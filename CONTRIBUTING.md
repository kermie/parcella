# Contributing to Gartenmanager

Thanks for your interest in this project! It's intended as open-source
software for allotment garden associations -- generic enough to be useful
beyond the association it originated from.

## License and what that means for you

This project is licensed under the **GNU Affero General Public License
v3.0 (AGPL-3.0)**. In short:

- You may freely use, modify, and redistribute the code.
- If you run a modified version **as a network service** (e.g. SaaS for
  other associations), you must make the source code of your version
  publicly available -- even without classic redistribution.
- Derivative works must likewise be licensed under AGPL-3.0 (copyleft).

By contributing (pull request), you agree that your code is licensed
under the same terms (AGPL-3.0).

## How you can contribute

1. **Issues**: found a bug or have an idea? Open an issue before starting
   larger changes -- this avoids duplicate work.
2. **Fork & branch**: create a fork, work in a feature branch
   (`git checkout -b feature/my-change`).
3. **Pull request**: briefly describe what changes and why.

## Development environment

```bash
git clone <your-fork-url>
cd gartenverein
cp .env.example .env
docker compose build web
docker compose run --rm --entrypoint alembic web upgrade head
docker compose up -d
```

The app then runs at http://localhost:8000, API docs at
http://localhost:8000/api/docs.

## Code conventions

- **Language**: technical identifiers (class/table/column names,
  function names, URLs, API endpoints) are in **English**. User-facing UI
  text (labels, error messages, email content) stays in **German**, since
  the software's audience is German-speaking club members -- see
  [Architecture Decisions](./docs/architecture-decisions.md) for the
  reasoning and history of this split. Code comments and docstrings are
  also generally German, matching the maintainers' primary language.
- **Genericity**: new fields/functions should, where sensible, not only
  fit the originating association but allotment garden associations in
  general (e.g. configurable area types instead of hard-coded A/B/C
  logic, in case other associations need different categories).
- **Migrations**: every model change in `app/models.py` needs an
  accompanying Alembic migration:
  ```bash
  docker compose run --rm web alembic revision --autogenerate -m "Short description"
  ```
  Always review a migration manually before committing it --
  autogenerate occasionally misses things (e.g. renames are detected as
  drop+create).
- **API schemas**: new/changed models should get matching Pydantic
  schemas in `app/schemas.py`, so they're available via the REST API.
- **Tests**: new modules should come with a `tests/test_<module>.py` file
  with at least one happy-path test (see [docs/testing.md](./docs/testing.md)
  for the testing philosophy and how to run the suite).

## What helps us most

- Translating module UI text into English (the i18n foundation exists --
  one language per installation, switchable in admin settings -- but only
  the Tickets module's UI text is fully translated so far; every other
  module still shows German text even when English is selected; see
  `app/i18n.py` and `app/translations/`)
- Documentation for additional deployment scenarios
- Accessibility (a11y) of the templates
- Additional language translations (adding a new `app/translations/<code>.json`)

If you have questions: open an issue, we'll take a look.
