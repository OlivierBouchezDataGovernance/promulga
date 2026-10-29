<img src="docs/promulga-logo.svg" alt="Promulga" height="56">

# Promulga

**Self-hosted life cycle of governance documents** – policies, standards,
procedures, guidelines – from the first draft to the official, versioned PDF,
with a public portal to find them.

```
 Draft ──submit──▶ Review ──all reviewers done──▶ Revision ──submit──▶ Approval ──all approve──▶ Published
 (owner edits)     (reviewers comment)            (owner corrects)     (approvers decide)        (stamped PDF, vX.Y)
   ▲                                                   ▲──────────── one reject ◀──────┘               │
   └──────────────────────── new minor / major revision (Document Manager) ◀────────────────────────────┘
```

Two web sites in one small application:

| | **Portal** (`/`) | **Workspace** (`/admin`) |
|---|---|---|
| For | everyone (public by default), or signed-in readers, or the intranet only | owners, reviewers, approvers, Document Managers, administrators |
| What | browse the published documents through one or more tag hierarchies, full-text search, read/download the official PDF and its previous versions | tasks, register, check-out / check-in of the Word file, comments, review done, approve / reject, publication, reminders, audit log, users, tags, look and feel |
| Sign-in | optional (viewer role) | e-mail + password, or single sign-on (OpenID Connect: Entra ID, Google, Keycloak...) |

### What it does

- **Fixed workflow, configurable timing.** Draft → Review → Revision →
  Approval → Published. Durations per stage (default 4/2/2/2 weeks),
  "due soon" and "overdue" reminders, repeat frequency, daily summary for the
  Document Managers – all set in *Settings*.
- **Word stays Word.** The owner checks out the working file, edits it in
  Word (or LibreOffice), and checks it in again. Every check-in is kept as a
  revision.
- **Review in the application.** Reviewers comment on the document page
  (and may attach their annotated copy); the owner replies and resolves.
- **Approval by everyone.** All approvers must approve; one rejection (with a
  mandatory comment) sends the document back to the owner.
- **Official publication.** The approved file is converted to PDF, stamped
  on every page with reference, version, date and page number, fingerprinted
  (SHA-256) and frozen. Versions are `major.minor`; earlier versions remain
  available.
- **Audit trail.** Every action, by whom, when, with which comment; exportable
  as CSV. The register is exportable too.
- **Your look.** Colours, logo, banner image, banner and footer HTML are
  edited by the administrator (HTML is sanitised: no scripts). Accessible by
  default (WCAG AA contrasts, fixed stage colours, icons always labelled),
  self-hosted Inter font and Lucide icons: no third-party request.
- **E-mails** to the person who must act, with links; without an SMTP server
  everything is readable in the *Outbox* (handy for demos).

### Run it

```bash
cp .env.example .env
```

```bash
docker compose up -d --build
```

- [docs/INSTALL.md](docs/INSTALL.md) – installation on any Linux server, with a step-by-step guide for a **Google Cloud** VM.
- [docs/INSTALL-WSL.md](docs/INSTALL-WSL.md) – a local test on a Windows laptop with Ubuntu WSL.
- [docs/QUICKSTART.md](docs/QUICKSTART.md) – first steps after installation, and a 15-minute demo scenario.
- [docs/BRAND.md](docs/BRAND.md) – visual identity: logo, colours, stage colours, typography, icons.

For development without Docker:

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

```bash
promulga serve
```

### Stack

Python 3.12, FastAPI, SQLAlchemy (SQLite by default, PostgreSQL optional),
Jinja2 templates (no JavaScript framework), APScheduler for reminders,
Gotenberg (LibreOffice) for PDF conversion, pypdf/ReportLab for stamping,
Caddy for automatic HTTPS. One `docker compose up`, three containers.

### Status, licence and support

Promulga is an **open-source project offered as is**, under the
[Apache License 2.0](LICENSE). Whoever installs it runs and secures their own
server. There is **no support, no warranty and no service level** – only
best-effort answers to issues when time allows. Review it, test it, and get a
security review before trusting it with sensitive documents.

Known limits of version 0.1: no in-browser editing (check-out / check-in
only), no database migrations yet (back up before upgrading), passkeys not yet
supported (use SSO for strong authentication), single application process.

### Repository layout

| Path | Content |
|---|---|
| `promulga/` | the application (workflow, portal, workspace, templates, styles) |
| `tests/` | automated tests (`pytest`) |
| `deploy/Caddyfile`, `Dockerfile`, `docker-compose.yml`, `.env.example` | deployment |
| `docs/` | installation and quick start guides |
| `google-apps-script/` | the earlier zero-budget prototype on Google Sheets / Docs / Apps Script |
