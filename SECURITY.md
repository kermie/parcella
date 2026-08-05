# Security Policy

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a suspected security
vulnerability.

Instead, use GitHub's private vulnerability reporting for this repository:

1. Go to the [Security tab](https://github.com/kermie/parcella/security).
2. Click **"Report a vulnerability"**.
3. Describe the issue, including steps to reproduce and the affected
   version/commit if known.

This opens a private advisory visible only to the maintainer and you,
without needing a mailing address or public disclosure up front.

If private reporting is ever unavailable, open a minimal public issue
that asks for a private channel rather than describing the
vulnerability itself, and the maintainer will follow up.

## What to expect

This is a community-maintained open-source project, not a company with
an SLA. As a rough guide:

- Acknowledgement: within a few days.
- A fix or mitigation plan: depends on severity and complexity: no fixed
  deadline is promised, but reports are not left silent.
- Credit: reporters are credited in the eventual advisory/release notes
  unless they ask to stay anonymous.

## Supported versions

Only the latest tagged release and `main` are supported with security
fixes. There is no long-term-support branch at this stage of the
project.

## Scope

In scope: the application code in this repository (`app/`, templates,
migrations, the WordPress connector under `integrations/wordpress/`).

Out of scope: vulnerabilities in third-party dependencies themselves
(report those upstream), and social-engineering or physical-access
attacks against a specific deployment.

## Background

See [docs/security.md](./docs/security.md) for what protects this app,
what a full-codebase review already fixed, and what limitations are
knowingly still open.
