/**
 * @fileoverview Constants of the governance document workflow.
 *
 * Everything an administrator may want to change without touching code
 * (folder IDs, template, Document Managers, organisation name) lives in the
 * `Settings` tab of the register sheet, not here. This file only holds the
 * structural constants: stage names, stage durations, sheet layouts and the
 * Drive access matrix.
 */

/** Workflow stages, in their normal order. */
const STATUS = Object.freeze({
  DRAFT: 'Draft',
  REVIEW: 'Review',
  REVISION: 'Revision',
  APPROVAL: 'Approval',
  PUBLISHED: 'Published',
});

/** Fixed duration of each active stage, in calendar days (4/2/2/2 weeks). */
const STAGE_DAYS = Object.freeze({
  [STATUS.DRAFT]: 28,
  [STATUS.REVIEW]: 14,
  [STATUS.REVISION]: 14,
  [STATUS.APPROVAL]: 14,
});

/** Document types offered in the creation form, with their ID prefix. */
const DOC_TYPES = Object.freeze({
  Policy: 'POL',
  Standard: 'STD',
  Procedure: 'PRC',
  Guideline: 'GDL',
});

/** Tab names of the register spreadsheet. */
const SHEETS = Object.freeze({
  DOCUMENTS: 'Documents',
  HISTORY: 'History',
  SETTINGS: 'Settings',
});

/**
 * Columns of the `Documents` tab. The first eleven are the register asked for
 * by the business; the others are technical bookkeeping used by the workflow.
 * Order matters: it is the column order in the sheet.
 */
const DOC_COLUMNS = Object.freeze([
  'ID',
  'Title',
  'Type',
  'Doc URL',
  'Owner',
  'Reviewers',          // comma-separated e-mail addresses
  'Approvers',          // comma-separated e-mail addresses
  'Status',
  'Stage due date',
  'Version',            // major.minor; draft versions are 0.x before the first publication
  'Publication date',
  'Doc file ID',        // Google Drive file ID of the working document
  'Reviewed by',        // reviewers who clicked "Review done" in the current round
  'Approved by',        // approvers who approved in the current round
  'Next version',       // version the document will get when it is published
  'Last PDF URL',       // PDF copy of the latest published version
  'Last update',
]);

/** Columns of the `History` (audit log) tab. */
const HISTORY_COLUMNS = Object.freeze([
  'Timestamp',
  'Doc ID',
  'Version',
  'From status',
  'To status',
  'Action',
  'Actor',
  'Comment',
]);

/**
 * Default content of the `Settings` tab: [key, default value, help text].
 * Empty defaults are filled in by `setup()`.
 */
const SETTINGS_DEFAULTS = Object.freeze([
  ['ORGANISATION_NAME', 'My organisation', 'Shown in e-mails and in the web app header.'],
  ['DOCUMENT_MANAGERS', '', 'Comma-separated e-mails of the Document Managers (can create documents and see everything). Filled with the script owner by setup.'],
  ['TEMPLATE_DOC_ID', '', 'Google Docs template copied for each new document. Created by setup if empty.'],
  ['WORKING_FOLDER_ID', '', 'Drive folder holding the working documents. Created by setup if empty.'],
  ['PUBLISHED_FOLDER_ID', '', 'Drive folder holding the read-only PDF copies. Created by setup if empty.'],
  ['PUBLISHED_READERS', '', 'Optional comma-separated e-mails who get read access to every published PDF.'],
  ['PUBLISH_LINK_SHARING', 'no', '"yes" = published PDFs are readable by anyone who has the link.'],
  ['WEB_APP_URL', '', 'Paste the /exec URL of the web app deployment here (used in e-mails).'],
  ['REMINDER_HOUR', '8', 'Hour of the day (0-23, script time zone) at which overdue reminders are sent.'],
]);

/** Drive roles, from weakest to strongest (Drive API v3 names). */
const ROLE_RANK = Object.freeze({ reader: 1, commenter: 2, writer: 3 });

/**
 * Drive access per stage and per participant group. A person who belongs to
 * several groups gets the strongest role. `null` means "no access".
 *
 * - Draft:     only the owner edits; reviewers and approvers do not see the
 *              work in progress yet.
 * - Review:    reviewers comment; the owner can reply to comments but the
 *              text is frozen while it is being reviewed.
 * - Revision:  the owner edits again; reviewers keep read access so they can
 *              see how their comments were handled.
 * - Approval:  everybody reads; approvers decide in the web app.
 * - Published: everybody reads; the file is also locked (Drive content
 *              restriction) and a PDF is copied to the Published folder.
 *
 * Document Managers always keep read access so they can follow up.
 */
const ACCESS_MATRIX = Object.freeze({
  [STATUS.DRAFT]: { owner: 'writer', reviewers: null, approvers: null, managers: 'reader' },
  [STATUS.REVIEW]: { owner: 'commenter', reviewers: 'commenter', approvers: null, managers: 'reader' },
  [STATUS.REVISION]: { owner: 'writer', reviewers: 'reader', approvers: null, managers: 'reader' },
  [STATUS.APPROVAL]: { owner: 'reader', reviewers: 'reader', approvers: 'reader', managers: 'reader' },
  [STATUS.PUBLISHED]: { owner: 'reader', reviewers: 'reader', approvers: 'reader', managers: 'reader' },
});

/** Script property names (values are generated, never written in code). */
const PROP = Object.freeze({
  SPREADSHEET_ID: 'SPREADSHEET_ID',
  LINK_SECRET: 'LINK_SECRET',
});
