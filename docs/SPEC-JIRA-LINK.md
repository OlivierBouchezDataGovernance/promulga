# Specification: optional native Jira link (PROM-49, LAB-74)

Status: **draft for validation by Olivier**. Nothing is built. Written 2026-11-03.

## 1. Goal

Teams that already plan their work in Jira should see the life of their governance documents there, without
copying it by hand: one issue per document, the documents being written or revised in the current sprint, and the
remarks of readers kept as a backlog for the next version. Promulga stays the reference: it decides the stages,
the approvals and the publication; Jira only shows them and collects work items.

Out of scope: approving or publishing from Jira, editing documents in Jira, replacing the Promulga e-mails for
account security.

## 2. Principles

1. **Optional and off by default.** Enabled per instance in *Settings > Integrations* with a Jira site, a project
   and an API token in `secrets/jira_token`. Without it Promulga works as today.
2. **Promulga is the master of the workflow.** A transition in Jira never moves a document. The only data that
   flows back from Jira are readers' remarks (section 6) and, optionally, who is assigned to a task.
3. **One Jira project per Promulga instance** (e.g. `PROM-DOCS`), Scrum or Kanban.
4. **Idempotent and retried**, like the webhooks: every call goes through an outbox (table `jira_outbox`), retried
   by the background job, written in the audit log. A Jira outage never blocks a workflow action.
5. **Confidentiality is decided by Promulga.** Audit documents never reach Jira; limited and confidential documents
   are created with a security level (or only their reference, without title or summary: setting).

## 3. Mapping

| Promulga | Jira | When |
|---|---|---|
| Domain of the document (`Document.domain`), else its parent Policy | **Epic** "Domain: Data quality" | first document of the domain |
| Document (`DG-POL-0008`) | **Story** (or Task) "DG-POL-0008 Governance Document Policy", in the epic | document created |
| Stage (Draft, Review, Revision, Approval, Published, Retired) | Status of the story (one column per stage on the board) | each stage change |
| Reviewer / approver who must act | **Sub-task** "Review v1.1 - Raf Reviewer", assignee = that person if their e-mail matches a Jira account | stage Review / Approval opens; closed when they act |
| Stage due date | Due date of the story and of its sub-tasks | each stage |
| Version in preparation, change note | Fields *Fix version* (`DG-POL-0008 v1.1`) and description | new revision |
| Published version | Comment with the link to the PDF and its SHA-256; story Done | publication |
| Periodic review due | Story reopened (status "Review due") with the due date | `review_reminder_days` before the date |
| Remarks of readers | **Issues of type "Remark"** linked to the story (section 6) | from the portal |

Custom field `Promulga reference` (text) on every issue, so that the link survives a renamed title; Promulga keeps
`jira_key` on the document (new column) and on each remark.

## 4. Sprint: documents being edited

- In a Scrum project, every document in Draft, Review, Revision or Approval is placed in the **active sprint** when
  it enters Draft (new document or new revision), unless the Document Manager unticks it.
- At the end of a sprint, unfinished documents move to the next sprint (Jira does it when the sprint is closed).
- Estimate (story points): by document type (setting, e.g. Policy 5, Standard 3, Procedure 2, Knowledge 1).
- Kanban project: no sprint; the board columns are the stages. The analysis of 2026 (docs/IDEAS.md, section 4)
  found that Kanban fits the life cycle better; Scrum is kept for teams that plan by sprint.

## 5. Notifications

Setting per family of e-mails: *Promulga* (default), *Promulga and Jira*, *Jira only*. With *Jira only*, the
assignment of a sub-task is the notification (Jira e-mails, Slack/Teams apps). Invitations, password resets and
acknowledgment campaigns always stay in Promulga.

## 6. Backlog of readers' remarks

- On the portal, a signed-in reader presses **Give feedback** on a published version: category (question, error,
  suggestion, out of date), text, optionally a section or page.
- Promulga stores the remark (new table `feedback`, attached to the version) and creates a Jira issue of type
  *Remark* linked to the story of the document, label `promulga-feedback`, priority from the category.
- The owner triages in Jira or in Promulga: *kept for the next version*, *answered*, *rejected* (with a reason).
  The status comes back to Promulga (Jira webhook or polling every 15 minutes).
- When a new revision starts, the kept remarks are listed on the document page as points to address; the change note
  proposes "Addresses: PROM-123, PROM-130". When the version is published, those issues are closed with the version.
- GDPR: the reader's name is visible to the owner only; retention follows the document; anonymous feedback only if
  the portal is public and the setting allows it (rate limited).

## 7. Settings and security

| Setting | Where |
|---|---|
| `JIRA_URL`, `JIRA_PROJECT`, `JIRA_USER` (e-mail of a service account) | `.env` |
| API token | `secrets/jira_token` (Docker secret, never in the database nor in logs) |
| Issue types, epic per domain or per Policy, sprint on/off, story points by type, e-mail families, confidential mode | *Settings > Integrations* |

The service account needs: browse, create and edit issues, manage sprints (Scrum), add comments. Jira webhooks back to
Promulga (remarks only) are authenticated with a shared secret in `secrets/jira_webhook_secret`.

## 8. Build or bus?

Two ways to build it:

| | Native in Promulga (this spec) | Through the event bus (IDEAS.md section 4: CloudEvents + Node-RED / Activepieces flow) |
|---|---|---|
| Set-up for a team | one form in Settings | deploy and maintain a flow |
| Other trackers (Azure DevOps, Planner, GitHub Projects) | one adapter each in Promulga | one flow each, Promulga unchanged |
| Remarks coming back | native, with the portal button | needs an inbound API (`POST /api/feedback`) |
| Code in Promulga | ~800 lines + tests, Jira API to follow | event envelope only |

Proposal: build the **reader feedback** (section 6) natively in Promulga first, since it is useful without Jira; then
the **outbound** part (sections 3 to 5) as a native adapter behind the same outbox as the webhooks, so that it can later
be moved behind the bus without changing its behaviour.

## 9. Phasing and estimate

| Phase | Content | Estimate |
|---|---|---|
| 1 | Feedback button on the portal, `feedback` table, owner's backlog on the document page, import as points to address | M (3) |
| 2 | Jira outbox, story per document, statuses, due dates, published comment, epic per domain | L (5) |
| 3 | Sub-tasks per reviewer/approver, sprint placement, story points | M (3) |
| 4 | Remarks to Jira and back (webhook), notification families | L (5) |

## 10. Questions for Olivier

1. One Jira project per Promulga instance, or reuse the programme project (LAB / PROM)?
2. Epic per **domain** (new attribute, LAB-145) or per **Policy** (the parent in the hierarchy)?
3. Scrum with sprints, or Kanban with one column per stage (recommended)?
4. Limited and confidential documents in Jira: with a security level, reference only, or not at all?
5. Should phase 1 (reader feedback without Jira) become its own ticket first?
