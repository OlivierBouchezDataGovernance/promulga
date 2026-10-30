# Improvement ideas

Backlog of ideas from a product review (October 2026), compared with
ConvergePoint, SharePoint + Power Automate, Collibra and Microsoft Purview.
Nothing here is committed: it is a list to sort, not a plan.

Already in 0.3 and therefore **not** listed: webhooks (Teams, Slack), deputies
during absences, version comparison, minor/major revisions, mandatory readers,
classification, read-only REST API, backup/restore commands, `/healthz`.

## High priority

### 1. Licence
CC BY-NC 4.0 is not designed for software, and "non-commercial" is ambiguous:
a company using Promulga internally to manage its own policies may be judged
"commercial" by its legal team, which blocks the very users Promulga targets.
The "free up to 20 editors" rule is not expressed in the licence itself.
Options: dual licence AGPL + commercial, or a source-available licence such as
the Functional Source License (FSL).

### 2. Event bus (generalise the existing webhooks)
Promulga emits one well-defined contract; all knowledge of external APIs lives
in a third-party bus, never in Promulga.
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
  n8n only as an option the customer installs (Sustainable Use License).
- The bus keeps the mapping document ↔ external ticket; Promulga knows nothing
  about it. Kanban fits the life cycle (one column per stage); Scrum does not.

### 3. Reader feedback loop
- "Give feedback" button on every portal document, attached to a **version**
  (and, if possible, a section or page).
- Categories: question, error, suggestion, out of date.
  Statuses: new → answered / kept for next revision / rejected (with reason).
- A backlog per document for the owner; kept items are imported as points to
  resolve when a revision starts; the change note lists the feedback addressed
  and notifies its authors.
- Configurable threshold (e.g. 5 open items or 1 reported error) that alerts
  the owner or proposes an early review.
- Native page first; then an inbound `POST /api/feedback` fed by the bus
  (Microsoft Forms → Power Automate, Google Sheets…). Adding feedback never
  changes a document, so the one-way rule still holds.
- Public portal: rate limiting, light captcha, moderation. GDPR: optional
  e-mail, stated purpose, retention period. Private to the owner by default,
  optional public FAQ under the document.

## Medium priority

### 4. Publication through the bus
- The GitHub export becomes one flow of the bus instead of core code.
- *Claim check* pattern: the event carries a short-lived signed URL and the
  SHA-256 of the PDF, never the file itself; each target verifies the hash.
- Confidentiality stays in Promulga: no download URL is ever emitted towards
  a public target for a document that is not an external/commercial publication.
- New targets: SharePoint, Azure Blob / S3, Confluence, Netlify, and
  Collibra / Purview through the DCAT-AP link.

### 5. Notifications
- E-mails stay in Promulga by default; the bus is an **additional** option.
- One setting per family: *Internal* (default), *Internal + bus*, *Bus only*.
  Families: actions expected, reminders, manager digest, publication and
  acknowledgment campaigns, account security.
- Account security e-mails (invitation, password reset) are always internal.
- One notification dispatcher with two channels (SMTP, event); optional
  delivery status callback (`delivered`, `bounced`, `failed`) for the audit log.

### 6. Document governance
- **Fast track for minor revisions**: typo or link fixes approved by the owner
  alone, without the full 4/2/2/2-week cycle.
- **Hierarchy** policy → standard → procedure; when a parent changes, propose a
  review of its children (impact analysis).
- **Language variants** FR/NL/EN of the same version, approved together.
- **Regulatory coverage matrix**: link documents to requirements (GDPR article,
  ISO 27001 control, NIS2, AI Act) and show the gaps.
- **Exceptions / waivers** to a policy: justification, approval, expiry date.
- **"In force on date X"** view of the register, for audits.

### 7. Acknowledgment and access (gaps with ConvergePoint)
- **Comprehension quiz** in acknowledgment campaigns.
- **Targeting** readers by function, region or department, with a dashboard per
  manager (who is compliant, who is not).
- **SCIM** provisioning from Entra ID (joiners and leavers without manual work).
- Portal access by role, finer than public / signed-in / intranet.
- **Templates and pre-approved content** reusable across documents.

## Lower priority

### 8. Data catalogue
Create or update a "Policy" asset in Collibra or Microsoft Purview for each
published document, with owner, version, review date and a link to the official
PDF, through the bus. Message: Promulga governs the document, the catalogue
operationalises the rule.

### 9. Operations
Documented upgrade path between versions, restore drill, monitoring of the
outbox and of webhook deliveries.

### 10. Compare page
Highlight the open event contract (versus ConvergePoint's Zapier and SharePoint
events), the verifiable integrity of published PDFs and the reader feedback loop.

## Suggested order
Licence → event bus → fast track for minor revisions, hierarchy, quiz →
reader feedback → the rest on demand.
