# Quick start

You installed Promulga ([INSTALL.md](INSTALL.md)) and signed in as administrator.
These steps take about 15 minutes.

## 1. Make it yours – *Settings > Appearance*

- **Site name** and **tagline**.
- **Colours**: primary (headings, banner), primary dark (navigation and
  footer), accent (buttons), success (approve / published).
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
- Document types and their reference prefix (`Policy=POL` → `POL-0001`).
- Optionally a Word **template** that becomes the first file of new documents.

## 3. Decide who reads the portal – *Settings > Public portal*

Public (default) or sign-in required. Intranet-only access is a server
setting (`PORTAL_ALLOWED_IPS`, see INSTALL.md).

## 4. Build the navigation – *Tags*

Create one or more taxonomies – for example **Domain**, **Regulation**,
**Audience** – and their tags. Tags can be nested ("Data quality" under "Data
governance"): the portal shows a document under its tag and under every parent.

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
