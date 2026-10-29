# Quick start

You installed Promulga ([INSTALL.md](INSTALL.md)) and signed in as administrator.
These steps take about 15 minutes.

## 1. Make it yours – *Settings > Appearance*

- **Site name** and **tagline**.
- **Colours**: primary (headings, banner), primary dark (navigation and
  footer), accent (buttons, links), success (approve / published). The text
  on buttons switches automatically between white and black to stay readable.
  Stage colours (Draft, Review...) are fixed, see [BRAND.md](BRAND.md).
- **Logo** (PNG or SVG, shown in the navigation bar) and an optional
  **banner image** behind the portal's home banner.
- **Banner HTML** – the text of the portal home page, for example:

  ```html
  <div class="eyebrow">Governance</div>
  <h1>Our policies and standards</h1>
  <p>The official, approved version of every governance document.</p>
  ```

- **Footer HTML** – links, legal notice, contact. Scripts and event
  attributes are removed automatically.

## 2. Set the timing – *Settings > Deadlines and alerts*

- Stage durations (default Draft 28, Review 14, Revision 14, Approval 14 days).
- "Due soon" reminder N days before the deadline, overdue reminder repeated
  every N days, hour of the reminders, daily summary for managers.
- Document types, their reference prefix and icon (`Policy=POL=shield-check` → `POL-0001`).
- Optionally a Word **template** that becomes the first file of new documents.

## 3. Decide who reads the portal – *Settings > Public portal*

Public (default) or sign-in required. Intranet-only access is a server
setting (`PORTAL_ALLOWED_IPS`, see INSTALL.md).

## 4. Build the navigation – *Tags*

Create one or more taxonomies – for example **Domain**, **Regulation**,
**Audience** – and their tags. Tags can be nested ("Data quality" under "Data
governance"): the portal shows a document under its tag and under every parent.

Give the tags of the "Domain" taxonomy a **code** (e.g. `DG` for Data
governance): it can be used in file names. Create a tag **Public** (e.g. in a
"Visibility" taxonomy): documents carrying it go to the public export.

## 5. Invite people – *Users*

| Role | Can |
|---|---|
| viewer | read the portal when it requires sign-in |
| contributor | be owner, reviewer or approver |
| manager | Document Manager: create documents, change participants, close a stuck review, start new revisions, withdraw documents, see everything |
| admin | manager + users, settings, outbox |

You can also skip this step: people named as owner, reviewer or approver of a
document are invited automatically (as contributors).

Without SMTP, invitations are not sent: open *Outbox*, expand the e-mail and
copy its link.

## 6. Create the first document – *+ New document*

Title, type, summary (shown on the portal), **one owner**, reviewers
(optional), **at least one approver**, tags, and optionally the first file.

## 7. Follow a document through its life

| Who | Where | What |
|---|---|---|
| Owner | *My tasks* > document | **Check out** (downloads the file and locks it), edit in Word, **Check in**; then **Submit for review** |
| Reviewers | *My tasks* > document | download the latest file, add **comments** (optionally attach an annotated copy), click **Review done** |
| Owner | document page | reply to and **resolve** comments, check in the corrected file, **Submit for approval** |
| Approvers | document page | **Approve**, or **Reject** with a comment (back to Revision; all approvals of the round are cancelled) |
| – | automatic | when the last approver approves: PDF conversion, stamp *REF – title – version – date* on every page, publication on the portal, e-mail to all participants |
| Manager | document page | later: **Start new minor revision** (1.0 → 1.1) or **major** (→ 2.0); the published version stays in force meanwhile |

Every step is in the document's **History** and in the global **Audit log**
(CSV export). The **Register** lists all documents with their stage, deadline
and who the workflow is waiting for (CSV export).

---

## 8. Governance features

- **Change note**: a new revision asks what it will change; it appears in the
  version history of the cover page and of the portal.
- **Compare versions**: on the document page, *Compare versions* shows the
  paragraphs added and removed between two revisions.
- **Periodic review**: every published document gets a next review date
  (default 12 months, per document in *Edit*); the owner is warned 30 days
  before, managers see it in the daily summary and in *Indicators*.
- **Read & acknowledge**: tick *Readers must acknowledge* on a document; at
  publication every account is asked to confirm on the portal; follow the rate
  and remind the missing people from the document page (CSV export).
- **Deputies**: in *My account*, set an absence and a deputy; the deputy
  receives the requests and can review or approve on your behalf (audit log).
- **Segregation of duties**: a warning if the owner is also approver or reviewer.
- **Indicators**: on-time rate and average duration per stage, cycle time,
  overdue stages, reviews due, acknowledgment rates.
- **Export**, **API**, **Security**: see [INTEGRATIONS.md](INTEGRATIONS.md) and *Admin > Security*.

## 15-minute demo scenario

1. On the server: `docker compose exec app promulga demo-data`.
2. Show the **portal** (`/`): taxonomy cards, browse *Domain > Data
   governance*, search "access", open a document and its stamped PDF.
3. Sign in as administrator, show **My tasks**, the **Register** and a
   document's **History**.
4. Play the roles: in *Outbox*, open the invitation of
   `reviewer@example.org`, copy the link into a **private window**, choose a
   password. As the reviewer, open *Records Retention Policy*, add a comment,
   click **Review done**.
5. Same for `owner@example.org` (another private window): resolve the
   comment, check in a file, submit for approval.
6. Same for `approver@example.org`: approve → the document appears on the portal
   with version 1.0.
7. Show **Settings**: change the accent colour and the banner text, reload the portal.
8. Show the reminders: *Settings > Send today's reminders now*, then *Outbox*.

Reset the demo by deleting the volume (**this deletes all data**):
`docker compose down -v` then `docker compose up -d`.
