# Improvement ideas

Backlog of ideas from a product review (October 2026), compared with
ConvergePoint, SharePoint + Power Automate, Collibra and Microsoft Purview.
Nothing here is committed: it is a list to sort, not a plan.

## Positioning that sets the priorities

Promulga is a **demonstration and training tool**: it shows, concretely, what
professional document-management and policy-management platforms do. It is not
a supported production platform. It is open source (Apache 2.0) so that its code
can be reused, adapted and extended, for instance into a solution for
organisations with a small budget.

So the priorities favour, in this order: what makes a demonstration or a
training session better, what makes the code easier to reuse, and features that
**show** a practice of professional platforms. Enterprise operations (user
provisioning, Kubernetes, SLAs) come last.

Already in 0.3 and therefore **not** listed: webhooks (Teams, Slack), deputies
during absences, version comparison, minor/major revisions, mandatory readers,
classification, read-only REST API, backup/restore commands, `/healthz`.

Done: licence changed from CC BY-NC 4.0 to Apache 2.0 (CC BY-NC is not an open
source licence and blocked reuse by companies and consultants).

## High priority: demonstration and training

### 1. Training scenarios
- **Guided scenarios per role** (owner, reviewer, approver, Document Manager,
  reader), each with a goal, steps and the expected result on screen.
- **Exercises with a correction**: "find the policy in force on a given date",
  "reject with a reason", "start a major revision", "follow up the missing
  acknowledgments".
- A **trainer guide**: session plan (half day, full day), slides outline, and
  what each screen teaches about the practice of professional platforms.
- A short note on each screen: "in professional platforms, this is called…",
  linking Promulga's vocabulary to ConvergePoint, PolicyTech, SharePoint.

### 2. Demo experience
- **Demo data in French, Dutch and English**, with realistic policies,
  standards and procedures of a data governance framework.
- A **"Reset the demo" button** (administrator only, demo mode only) that
  restores the demo data without deleting the Docker volume.
- A **demo mode**: banner "demonstration instance", the four example users
  selectable at sign-in, e-mails kept in the Outbox.
- **Video demos** recorded automatically from the scripts of the
  `promulga-demo` repository (Playwright, captions, optional voice-over).

### 3. Code that others can reuse
- An **architecture guide**: modules, data model, workflow state machine,
  where to extend (new document type, new stage timer, new export target).
- **CONTRIBUTING.md**: local setup without Docker, tests, coding conventions.
- Interface **translations** (texts are already marked for gettext): French and
  Dutch first.
- Keep the stack small and boring (FastAPI, SQLite/PostgreSQL, Jinja2) so that a
  small team can take it over.

## Medium priority: show what professional platforms do

### 4. Event bus (generalise the existing webhooks)
Also a teaching example of modern integration: one contract, no business logic
in the bus.
- Envelope in **CloudEvents** format, signed with HMAC, retries with backoff,
  each delivery recorded in the audit log.
- Event types: `document.created`, `stage.entered`, `task.assigned`,
  `task.due_soon`, `task.overdue`, `document.published`, `document.superseded`,
  `document.withdrawn`, `review.periodic_due`, `notification.requested`.
- **One-way only**: nothing external can change a document or its stage.
- Keep the current `text` field for Slack/Teams compatibility.
- Optional Docker Compose profile `integrations` (Node-RED, Apache 2.0, or
  Activepieces, MIT) and an `integrations/` folder with ready-to-import flows:
  Jira, Azure DevOps Boards, Microsoft Planner, Teams, GitHub Projects.
- The bus keeps the mapping document ↔ external ticket; Promulga knows nothing
  about it. Kanban fits the life cycle (one column per stage); Scrum does not.

### 5. Reader feedback loop
- "Give feedback" button on every portal document, attached to a **version**
  (and, if possible, a section or page).
- Categories: question, error, suggestion, out of date.
  Statuses: new → answered / kept for next revision / rejected (with reason).
- A backlog per document for the owner; kept items are imported as points to
  resolve when a revision starts; the change note lists the feedback addressed.
- Configurable threshold that alerts the owner or proposes an early review.
- Native page first; then an inbound `POST /api/feedback` fed by the bus
  (Microsoft Forms, Google Sheets…). Adding feedback never changes a document.
- Public portal: rate limiting, moderation. GDPR: optional e-mail, stated
  purpose, retention period.

### 6. Document governance practices
- **Fast track for minor revisions**: typo or link fixes approved by the owner
  alone, without the full 4/2/2/2-week cycle.
- **Hierarchy** policy → standard → procedure; when a parent changes, propose a
  review of its children (impact analysis).
- **Language variants** FR/NL/EN of the same version, approved together.
- **Regulatory coverage matrix**: link documents to requirements (GDPR article,
  ISO 27001 control, NIS2, AI Act) and show the gaps.
- **Exceptions / waivers** to a policy: justification, approval, expiry date.
- **"In force on date X"** view of the register, for audits.

### 7. Acknowledgment features of professional platforms
- **Comprehension quiz** in acknowledgment campaigns.
- **Targeting** readers by function, region or department, with a dashboard per
  manager (who is compliant, who is not).
- **Templates and pre-approved content** reusable across documents.

## Lower priority

### 8. Publication and notifications through the bus
- The GitHub export becomes one flow of the bus; the event carries a short-lived
  signed URL and the SHA-256 of the PDF (*claim check*), never the file itself.
  Confidentiality stays decided by Promulga. Targets: SharePoint, Azure Blob /
  S3, Confluence, Netlify, Collibra / Purview through the DCAT-AP link.
- E-mails stay in Promulga by default; the bus is an additional option per
  family (*Internal*, *Internal + bus*, *Bus only*); account security e-mails
  are always internal; optional delivery status for the audit log.

### 9. Data catalogue
Create or update a "Policy" asset in Collibra or Microsoft Purview for each
published document, through the bus. Promulga governs the document, the
catalogue operationalises the rule.

### 10. Enterprise operations (for whoever reuses the code)
- **SCIM** provisioning from Entra ID, portal access by role.
- Documented upgrade path, restore drill, monitoring of the outbox and webhooks.
- **Helm chart for Kubernetes**: app, Gotenberg and optional OnlyOffice, secrets
  as Kubernetes Secrets, data as a PersistentVolumeClaim, an Ingress instead of
  Caddy, probes on `/healthz`. Constraint: the in-process scheduler must run
  once, so the app stays at one replica (or the scheduler moves to a separate
  CronJob before scaling out).

## Suggested order
Training scenarios and demo data → reset button and demo mode → architecture
guide and translations → event bus → minor-revision fast track, hierarchy,
quiz → the rest on demand.
