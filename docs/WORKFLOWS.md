# Which workflow?

Promulga has two workflows. Each **document type** has one of them, set by an administrator in
*Settings > Document types*: a line `Name=PREFIX=icon` follows the Full workflow, a line
`Name=PREFIX=icon=approval` follows the Approval only workflow.

| | Full | Approval only |
|---|---|---|
| For | Governance documents: charter, policies, standards, procedures | Reference material that is not a governance document: FAQ, guides, notes, the sources of a chatbot |
| Stages | Draft > Review > Revision > Approval > Published | Upload > Approval > Published |
| Who starts it | A Document Manager (*+ New document*) | An Author uploads the files (*Upload*), as their owner; a Document Manager may too |
| Reviewers | Optional | None |
| The file | Word, checked out and in; title page and document control written into it | Kept as uploaded (.docx, .pdf, .md, .txt); the reading PDF gets a separate title page |
| Rejection | Back to Revision: the owner decides on every remark | Back to the owner, who uploads a corrected file and sends it again |
| Published text | Text of the PDF, for search | Clean text (headings kept), for search and for chatbots |

In both workflows at least one approver other than the owner approves, every action is in the audit log, and
the classification decides who may read the published version.

## Choosing

- The document sets rules, roles or decisions: **Full**. Its readers must trust that it was reviewed.
- The document informs or explains, and a check by a second person is enough: **Approval only**.
- Not sure: Full. A type can be switched later; the documents already published keep their history.

## Uploading documents (Approval only)

1. *Upload* in the workspace menu (shown when a type has the Approval only workflow).
2. Choose 1 to 20 files; the title of each document is taken from its file name and can be changed afterwards.
3. Choose the type, the classification, tags, the approvers (people or `@groups`) and, if needed, named readers.
4. *Send for approval*: each file becomes a document in Approval; the approvers receive one e-mail for the batch.
   A file already in Promulga (same SHA-256) is flagged.
5. Approvers open each document and approve or reject it, or decide several at once from *My tasks*.

## Roles and groups

| Role | May |
|---|---|
| Reader | Read the portal when it requires a sign-in |
| Author | Be owner, reviewer or approver of documents; upload Approval only documents |
| Document Manager | Create documents, change participants and metadata, steer the workflow |
| Administrator | Everything, plus users, tags, groups and settings |

Reviewer and approver are not global roles: they are chosen on each document. A **group** (*Groups*, or a group of
the single sign-on) is named with `@name` as reader, reviewer or approver. With *one member is enough*, the first
member who reviews or approves does it for the whole group; otherwise every member acts.

## Who may read

The document page says it in plain words:

- **Public**: anyone who can open the portal (External or Commercial publication, or an Internal document on a
  portal open without sign-in: Promulga warns about the latter).
- **Internal**: anyone signed in to the portal.
- **Restricted** (Limited distribution, Confidential): the owner, reviewers, approvers, named readers (people or
  groups) and Document Managers; for Audit - Confidential, the audience only.
