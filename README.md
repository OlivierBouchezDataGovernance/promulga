# Governance document workflow (Google Apps Script prototype)

A zero-budget workflow for the life cycle of governance documents (policies,
standards, procedures, guidelines), built on free Google tools:

- **Google Docs** to write, comment and read the documents;
- **a Google Sheet** as the register and audit log;
- **e-mails with a personal link** to tell each person what to do and by when;
- **a small web page** (Apps Script web app) with the action buttons.

Nobody needs to install anything or understand Git. Participants only need a
(free) Google account.

> Status: prototype. It is meant to be deployed and tested by the owner of
> the Google account before any real use. See [Known limits](#known-limits).

---

## Contents

1. [How the workflow works](#how-the-workflow-works)
2. [Setup, step by step](#setup-step-by-step)
3. [Daily use](#daily-use)
4. [Testing with 2–3 Google accounts](#testing-with-23-google-accounts)
5. [Known limits](#known-limits)
6. [Updating the code](#updating-the-code)
7. [Files](#files)
8. [Possible next steps](#possible-next-steps)

---

## How the workflow works

```
 Draft ──submit──▶ Review ──all reviewers done──▶ Revision ──submit──▶ Approval ──all approve──▶ Published
 4 weeks           2 weeks   (or Manager closes)   2 weeks      ▲        2 weeks                      │
   ▲                                                            └──── one approver rejects ─────────┤
   └───────────────────────── Document Manager starts a new minor/major revision ◀────────────────┘
```

| Stage | Who acts | What they do | Deadline |
|---|---|---|---|
| **Draft** | Owner | Writes the text in the Google Doc, clicks **Submit for review** | +28 days |
| **Review** | Each reviewer | Adds comments in the Google Doc, clicks **Review done** | +14 days |
| **Revision** | Owner | Processes the comments, clicks **Submit for approval** | +14 days |
| **Approval** | Each approver | Clicks **Approve**, or **Reject** with a comment (→ back to Revision; all approvals of the round are cancelled) | +14 days |
| **Published** | – | Version number and date are fixed, a PDF goes to the *Published* folder, the Doc is locked | – |

- A document without reviewers goes from Draft directly to Approval.
- Deadlines are fixed: when a stage is overdue, the people who still have to
  act get a reminder every morning, and the Document Managers get a summary.
  A Document Manager can close a review that is stuck.
- **Versions** are `major.minor`. The first publication is `1.0`. To change a
  published document, a Document Manager clicks *Start new minor revision*
  (→ `1.1`) or *Start new major revision* (→ `2.0`); the same Google Doc is
  unlocked and goes back to Draft. Every published version keeps its own PDF.

### Who can see and do what in Google Drive

The script changes the sharing of the Google Doc at each stage. Google does
not send its own "shared with you" e-mails: the workflow sends one clear e-mail
instead.

| Stage | Owner | Reviewers | Approvers | Document Managers |
|---|---|---|---|---|
| Draft | editor | – | – | viewer |
| Review | commenter (text frozen, can reply to comments) | commenter | – | viewer |
| Revision | editor | viewer | – | viewer |
| Approval | viewer | viewer | viewer | viewer |
| Published | viewer (Doc locked) | viewer | viewer | viewer |

Published PDFs are shared (read-only) with all participants, the Document
Managers and the optional `PUBLISHED_READERS`, or with anyone who has the link
if `PUBLISH_LINK_SHARING` is `yes`.

All files (register, Docs, PDFs) belong to the administrator's Google account.

### The register

The Google Sheet has three tabs:

- **Documents** – one row per document: ID, title, type, Doc URL, owner,
  reviewers, approvers, status, stage due date, version, publication date,
  plus technical columns the workflow uses (Drive file ID, who already
  reviewed/approved in the current round, next version, last PDF, last update).
- **History** – the audit log: one line for every creation, submission,
  review, approval, rejection, publication and participant change, with the
  date, the person and the comment.
- **Settings** – the few things the administrator can configure (see below).

> Do not edit the *Documents* or *History* tabs by hand, except for testing:
> changes made directly in the sheet bypass the audit log.

---

## Setup, step by step

You need about 20 minutes. Use the Google account that will **own** all the
documents (the "administrator" account). It can be a free Gmail account.

### 1. Create the register sheet

1. Go to <https://sheets.google.com> and create a **blank spreadsheet**.
2. Name it, for example, *Governance document register*.

### 2. Paste the script

1. In the sheet, click **Extensions > Apps Script**. A new tab opens with the
   script editor and a file called `Code.gs`.
2. Click the project name at the top (*Untitled project*) and rename it, e.g.
   *Governance document workflow*.
3. Click the **gear icon (Project Settings)** on the left and tick
   **"Show 'appsscript.json' manifest file in editor"**.
4. Go back to the **Editor** (the `< >` icon). Open `appsscript.json`, delete
   its content, and paste the content of [`src/appsscript.json`](src/appsscript.json).
   (This also switches on the *Drive API* service the script needs and sets the
   time zone to Europe/Brussels – change `"timeZone"` if needed.)
5. Delete `Code.gs` (three dots next to the name > **Delete**).
6. For each `.gs` file in the [`src`](src) folder, click **+ > Script**, type the
   name **without** `.gs` (e.g. `Config`), and paste the file content:
   `Config`, `Register`, `Setup`, `Auth`, `Sharing`, `Workflow`, `Mail`,
   `Reminders`, `WebApp`.
7. For each `.html` file, click **+ > HTML**, type the name **without** `.html`
   and paste the content: `Index`, `Styles`, `Script`.
8. Click the **Save** icon (or Ctrl+S).

### 3. Run the setup

1. Go back to the sheet tab and **reload the page**. After a few seconds a
   menu **Policy workflow** appears next to *Help*.
2. Click **Policy workflow > Set up (run once)**.
3. Google asks for authorisation:
   - click **Continue**, choose the administrator account;
   - you will see *"Google hasn't verified this app"*. This is normal for a
     script you wrote yourself: click **Advanced > Go to … (unsafe)**;
   - review the permissions (Sheets, Docs, Drive, send e-mail as you,
     triggers) and click **Allow**.
4. Run **Policy workflow > Set up (run once)** again if the first click only
   triggered the authorisation. When it finishes you see *Setup complete*.

Setup has created:

- the tabs *Documents*, *History* and *Settings*;
- two folders in your Drive: *Policy workflow - Working documents* and
  *Policy workflow - Published*;
- a template document *Policy workflow - TEMPLATE* (in the working folder).

### 4. Fill in the Settings tab

| Key | What to put |
|---|---|
| `ORGANISATION_NAME` | Your organisation's name (shown in e-mails). |
| `DOCUMENT_MANAGERS` | E-mails of the Document Managers, comma-separated. Your own address was put there. |
| `TEMPLATE_DOC_ID` | Leave as is, or the ID of your own template (the long code in its URL between `/d/` and `/edit`). The template may use `{{DOC_ID}}`, `{{TITLE}}`, `{{TYPE}}`, `{{OWNER}}`, `{{VERSION}}`, `{{CREATED}}`, `{{ORGANISATION}}` in the body, header or footer. |
| `WORKING_FOLDER_ID`, `PUBLISHED_FOLDER_ID` | Filled by setup. Do not share these folders by hand: the script manages access file by file. |
| `PUBLISHED_READERS` | Optional: e-mails who receive read access to every published PDF. |
| `PUBLISH_LINK_SHARING` | `yes` to make published PDFs readable by anyone with the link; `no` otherwise. |
| `WEB_APP_URL` | Filled in at step 5. |
| `REMINDER_HOUR` | Hour of the daily reminders (default `8`). |

### 5. Deploy the web app

1. In the script editor, click **Deploy > New deployment**.
2. Click the gear next to *Select type* and choose **Web app**.
3. Fill in:
   - *Description*: `v1`;
   - **Execute as: Me** (your administrator account) – required, see
     [Identity and deployment mode](#identity-and-deployment-mode);
   - **Who has access: Anyone with Google account**.
4. Click **Deploy** and copy the **Web app URL** (it ends with `/exec`).
5. Paste it in the *Settings* tab, key `WEB_APP_URL`.

### 6. Install the daily reminder

In the sheet, click **Policy workflow > Install daily reminder trigger**.
(It replaces any earlier copy, so clicking twice is harmless.)

### 7. Open the web app

Click **Policy workflow > Show my link** and open the link. As administrator
you are recognised automatically; other people use the personal link that
arrives in their e-mails.

You are ready: open the **+ New document** tab to create the first document.

---

## Daily use

**Document Manager**

- *+ New document*: title, type, one owner, reviewers (optional), approvers
  (at least one). The Doc is created from the template, shared with the owner,
  and the owner gets an e-mail with a 4-week deadline.
- *Register* > *Details*: history of a document and manager actions:
  *Close review now* (stuck review), *Change participants* (someone left),
  *Start new minor/major revision* (published documents).

**Owner** – *My drafts*: open the Google Doc, write; then **Submit for review**.
After the review, process the comments and **Submit for approval**.

**Reviewer** – *My reviews*: open the Google Doc, select text and add comments
(*Insert > Comment*); then **Review done**.

**Approver** – *My approvals*: read the Doc, then **Approve**, or **Reject**
with a comment explaining what must change.

Everyone – *Register*: all published documents (with their PDF), plus the
documents you take part in. Document Managers see everything.

The red number on a tab is the number of documents waiting for **you**.

---

## Testing with 2–3 Google accounts

You need distinct Google accounts, because each person's role and access is
tied to their e-mail. Free accounts are enough. A Google account can be
created **with an existing e-mail address** (e.g. a work address) without
creating a Gmail box: on <https://accounts.google.com/signup> choose
*"Use your existing email"* / *"Use my current email address instead"*.

Use a separate **Chrome profile** or a **private window** for each account,
so that you are signed in to only one account per window. Open each person's
e-mail link in **that person's** window: in a window where the administrator
is signed in, the web app always recognises the administrator, whatever link
is used.

| Account | Role in the test |
|---|---|
| **A** – administrator (owns the script) | Document Manager |
| **B** | Owner |
| **C** | Reviewer **and** approver (with a 3rd account **D**, make D the approver) |

1. **Create** – as A, in the web app, *+ New document*: owner B, reviewer C,
   approver C (or D). Check: a row in *Documents* (status *Draft*, due in 28
   days), a line in *History*, a Doc in the working folder, and B received an
   e-mail.
2. **Draft** – as B, open the e-mail link, tab *My drafts*: the document is
   there. Open the Doc: B can edit. Try to open the Doc as C: access denied.
   B clicks **Submit for review**.
3. **Review** – C receives an e-mail. As C: the Doc opens in comment mode;
   add a comment. B can only comment now. C clicks **Review done** → status
   *Revision*, B gets an e-mail; C can still read the Doc.
4. **Revision** – as B, resolve the comment, change the text, **Submit for
   approval**.
5. **Rejection path** – as the approver, write a comment and click **Reject**:
   the document goes back to *Revision* and B gets the comment by e-mail.
   B submits again.
6. **Approval** – the approver clicks **Approve** → status *Published*,
   version `1.0`, publication date today. Check: PDF in the *Published*
   folder, Doc locked (banner *"This file is locked"*), everybody is viewer,
   everybody received the publication e-mail.
7. **New revision** – as A, *Register > Details > Start new minor revision*:
   status *Draft*, target version `1.1`, Doc unlocked for B.
8. **Reminders** – in the *Documents* tab, set the *Stage due date* of a
   non-published document to yesterday, then **Policy workflow > Send
   overdue reminders now**. The person who has to act and the Document
   Managers receive a reminder.
9. **Security checks** – open the web app link of B and change one letter of
   the `t=` value in the address bar: the page must ask for the personal link.
   As C, try to approve a document where C is not an approver: refused.

With only **two** accounts, use A as manager *and* reviewer/approver, B as
owner (A is still a viewer in Draft as manager, which is expected).

---

## Known limits

### E-mail quota

The e-mails are sent from the administrator's Gmail through Apps Script. A
free Google account may send to **about 100 recipients per day** (Google
Workspace: 1,500). When the quota is used up, the e-mail is skipped (logged
in *Executions*), but the action itself is still recorded. The remaining
quota is printed in the log of each reminder run.

Order of magnitude for one document with 1 owner, 3 reviewers, 2 approvers
and 1 manager: about 14 e-mails over its whole cycle (1 + 3 + 1 + 2 + 7 at
publication), plus reminders (one digest per late person per day). Fine for a
pilot with a few documents a week; not for dozens at once.

### Every participant needs a Google account

Drive can only share a Doc for editing/commenting with a Google account. The
account can be created with the person's existing work e-mail (see
[Testing](#testing-with-23-google-accounts)). The address in the register
must be **exactly** the address of that Google account. If someone has no
Google account, Drive falls back to sending its own invitation, and the
person must create an account before they can open the Doc.

### Identity and deployment mode

How the web app knows who is clicking depends on the deployment setting
*Execute as*:

- **Execute as: Me** (used by this prototype). The script runs with the
  administrator's rights, so it can change the sharing of the Docs and write
  the register, and participants need no access to the sheet. But with free
  Google accounts **Google does not tell the web app who the visitor is**
  (only the administrator is recognised). The prototype therefore uses a
  **personal link**: the web app URL plus the person's e-mail and a signature
  computed with a secret stored in the script properties (never in the
  code). Consequences:
  - whoever has a person's link can act as that person: links must not be
    forwarded. If it happens, **Policy workflow > Invalidate all personal
    links** makes every link stop working; new links go out with the next
    e-mails, or people request one on the web app home page;
  - the web app still requires being signed in to *a* Google account (access
    *Anyone with Google account*), but it does not check that it is the same
    person as the link;
  - opening the Google Doc itself is protected by Google normally: the person
    must be signed in with the account the Doc is shared with.
- **Execute as: User accessing the web app**. Google then gives the real
  identity of every visitor, but each participant must authorise the
  unverified script (scary warning screen), must be an **editor of the
  register sheet** (so they could change it directly, outside the audit log),
  and cannot change the sharing of Docs they do not own. **This prototype does
  not support that mode for actions.** It becomes the right choice with a
  Google Workspace domain, where *Execute as: Me* also reveals the visitor's
  address for users of the same domain (the code then uses it automatically
  and the personal link is not needed).

### Other limits

- **Single owner account.** All files belong to the administrator account:
  if it is deleted, everything goes. Keep the account safe (2-step
  verification) and back up the *Published* folder.
- **Storage**: 15 GB shared by Gmail, Drive and Photos on a free account.
- **Apps Script quotas (free account)**: 6 minutes per execution, 90 minutes
  of trigger run time per day, 20 triggers. Comfortable for this volume.
- **Locking** uses Drive's *content restriction*. The administrator (owner)
  could still unlock the Doc by hand; nobody else can, since nobody else is
  editor after publication.
- **Doc comments** stay in the Google Doc across versions (resolved comments
  remain in the comment history). The PDF has no comments.
- **The `{{VERSION}}` placeholder** is filled once at creation (`1.0`); the
  authoritative version is the one in the register and in the PDF name.
- Deadlines are **calendar days**, without public holidays.
- No delegation / out-of-office: a Document Manager changes the participants.
- The register sheet itself should be shared only with Document Managers (as
  viewers), so that the audit log cannot be edited by participants.

---

## Updating the code

After changing a file in the script editor, the web app keeps running the
old version until you publish a new one:
**Deploy > Manage deployments > pencil icon > Version: New version > Deploy**.
The URL stays the same, so `WEB_APP_URL` and the links already sent keep working.

### With clasp (optional, for developers)

The [`src`](src) folder has the layout expected by
[clasp](https://github.com/google/clasp):

```bash
npm install -g @google/clasp
```

```bash
clasp login
```

Copy `.clasp.json.example` to `.clasp.json` and put the *Script ID* of your
project (Project Settings > IDs) in it, then:

```bash
clasp push
```

`.clasp.json` is ignored by Git, so the script ID is never committed.

---

## Files

| File | Content |
|---|---|
| [`src/appsscript.json`](src/appsscript.json) | Manifest: time zone, Drive API v3 service, permissions, web app settings |
| [`src/Config.gs`](src/Config.gs) | Stages, deadlines, document types, sheet columns, Drive access matrix |
| [`src/Register.gs`](src/Register.gs) | Reading/writing the *Documents*, *History* and *Settings* tabs |
| [`src/Setup.gs`](src/Setup.gs) | Sheet menu, one-time setup, default template, trigger installation |
| [`src/Auth.gs`](src/Auth.gs) | Who is the user: Google identity or signed personal link |
| [`src/Sharing.gs`](src/Sharing.gs) | Drive permissions per stage, locking, sharing of PDFs |
| [`src/Workflow.gs`](src/Workflow.gs) | Creation and all stage transitions, with permission checks |
| [`src/Mail.gs`](src/Mail.gs) | E-mails to the people who have to act |
| [`src/Reminders.gs`](src/Reminders.gs) | Daily digest of overdue stages |
| [`src/WebApp.gs`](src/WebApp.gs) | Web app entry point and the functions the page calls |
| [`src/Index.html`](src/Index.html), [`Styles.html`](src/Styles.html), [`Script.html`](src/Script.html) | The web page |

---

## Possible next steps

- Export the *Documents* tab to **BigQuery** (scheduled Apps Script or a
  connected sheet) so that **OpenMetadata** can catalogue the policies and
  link them to business glossary terms.
- "Due soon" reminders a few days before each deadline.
- Periodic review date (e.g. re-review every published policy after 1 year).
- Retire/archive a document.
