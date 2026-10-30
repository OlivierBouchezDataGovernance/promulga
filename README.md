<img src="docs/promulga-logo.svg" alt="Promulga" height="56">

# Promulga

**Self-hosted life cycle of governance documents** – policies, standards,
procedures, guidelines – from the first draft to the official, versioned PDF,
with a public portal to find them.

**A demonstration and training tool.** Promulga shows, concretely and in an
hour on a laptop, what professional document-management and policy-management
platforms do: deadlines and reminders, review and approval, official stamped
PDFs, read and acknowledge, audit trail. Use it to train Data Owners and
governance teams, to demonstrate a process, or to pilot a policy framework
before choosing a product. It is open source (Apache 2.0) so that its code can
be reused, adapted and extended, for instance into a solution for organisations
with a small budget. It is not a supported production platform.

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
| Sign-in | optional (viewer role) | passkey, e-mail + password, or single sign-on (OpenID Connect: Entra ID, Google, Keycloak...) |

### What it does

- **Fixed workflow, configurable timing.** Draft → Review → Revision →
  Approval → Published. Durations per stage (default 4/2/2/2 weeks),
  "due soon" and "overdue" reminders, repeat frequency, daily summary for the
  Document Managers – all set in *Settings*.
- **Word stays Word.** The owner checks out the working file, edits it in
  Word (or LibreOffice), and checks it in again. Every check-in is kept as a
  revision.
- **Review in the application.** Reviewers comment on the document page
  (and may attach their annotated copy); the owner decides on every remark.
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

New in 0.4:

- **Document hierarchy.** Every document hangs under a parent: Charter >
  Policy > Standard, Guideline, Procedure (Control under a Standard, Work
  instruction under a Procedure). The Charter is the top. Shown on the document
  page, on the document control page of the Word file, in a tree of the whole
  register and on the portal.
- **Clear rules per stage.** A Draft is seen by its owner only. During the
  Review reviewers comment, nobody edits, and the Word file is replaced by a
  PDF *review copy*. In Revision the owner decides on every remark (accepted, or
  rejected with a justification) before submitting; the others may only reply;
  comments written in Word or in the online editor are imported as remarks. In
  Approval the content is frozen: approvers approve a PDF built from the
  submitted revision, and that revision is published. No second review round.
- **Annexes without approval.** French and Dutch translations attached to a
  published English version (marked "the English version prevails", outdated
  when a new version is published), and external references (legal texts,
  regulations, norms) stored once and linked to the documents that rely on them.

New in 0.3:

- **Classification** of every document: External publication, Commercial
  publication, Internal publication, Limited distribution, Confidential. It is
  printed in a coloured band on the cover page and decides who reads the
  document: limited and confidential documents are shown only to their
  participants after sign-in, and only external and commercial publications
  can be exported to a public web site.
- **Cover page with your logo and copyright line**, keywords and a link to the
  official version; variables also in page headers and footers; `{{HISTORY}}`
  and `{{APPROVALS}}` tables anywhere in the document.
- **A template per document type** (Policy, Standard, Procedure...), and
  **mandatory readers** chosen per document.

New in 0.2:

- **One shared version, online.** Optional OnlyOffice editor (the closest to
  Word): reviewers comment and suggest changes in the same file.
- **Metadata inside the Word file.** Automatic cover page with version
  history, variables `{{TITLE}}`, `{{VERSION}}`... refreshed at every step,
  file names built from tags (`{CODE:domain}-{TYPE_CODE}-{VERSION}-...`),
  global unique document number `{ID}`.
- **Governance.** Mandatory change note, version comparison, segregation of
  duties warning, periodic review with reminders, deputies during absences,
  read & acknowledge with tracking, indicators (on-time rate, cycle time...).
- **Publication to your web site.** Static package of the documents tagged
  `public` (index, PDFs, DCAT-AP / JSON / CSV catalogues, checksums), pushed to
  GitHub in one commit; optional electronic seal (PAdES) of the PDFs.
- **Integrations.** Read-only REST API with tokens, webhooks (Teams, Slack).
- **Passkeys.** Sign in with Windows Hello, Touch ID, Android or a security
  key: no password, nothing secret stored on the server, phishing-resistant.
- **Safer operations.** Automatic database migrations, `promulga backup`,
  secrets as files, first-run password link, security checks page.

### Run it

```bash
./deploy/setup.sh
```

```bash
docker compose up -d --build
```

Add `--profile editor` to the second command for the online editor.

- [docs/INSTALL.md](docs/INSTALL.md) – installation on any Linux server, with a step-by-step guide for a **Google Cloud** VM.
- [docs/INSTALL-WSL.md](docs/INSTALL-WSL.md) – a local test on a Windows laptop with Ubuntu WSL.
- [docs/QUICKSTART.md](docs/QUICKSTART.md) – first steps after installation, and a 15-minute demo scenario.
- [docs/WORD-TEMPLATES.md](docs/WORD-TEMPLATES.md) – cover page, Word variables, file names.
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) – online editor, GitHub export, API, webhooks, electronic seal.
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
Caddy for automatic HTTPS, Alembic migrations, lxml for Word files, pyHanko for
PAdES signatures, OnlyOffice Docs (optional) for online editing.

### Status, licence and support

© 2026 Olivier Bouchez. Licensed under the [Apache License 2.0](LICENSE)
(see also [NOTICE](NOTICE)): you may use, modify and redistribute it,
including in commercial or internal projects, provided you keep the licence
and copyright notices and state your changes.

Promulga is a **demonstration and training tool**, offered **as is**, without
warranty and without a service level: whoever installs it runs and secures
their own server, and support is best effort only (support@promulga.com).
Training and demonstration sessions are available on request from Olivier
Bouchez Data Consulting. Website: https://promulga.com.

Known limits of version 0.2: the interface is in English (texts are marked
for translation), a single application process, the cover page and variables
need .docx files, the online editor is limited to 20 simultaneous editors
(OnlyOffice Community).

### Repository layout

| Path | Content |
|---|---|
| `promulga/` | the application (workflow, portal, workspace, templates, styles) |
| `tests/` | automated tests (`pytest`) |
| `deploy/Caddyfile`, `Dockerfile`, `docker-compose.yml`, `.env.example` | deployment |
| `docs/` | installation and quick start guides |
| `google-apps-script/` | the earlier zero-budget prototype on Google Sheets / Docs / Apps Script |
