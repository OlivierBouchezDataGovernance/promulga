/**
 * @fileoverview The document life cycle: creation and every stage transition.
 *
 *   Draft --submit--> Review --all reviewers done--> Revision --submit--> Approval
 *     ^                  (or Manager closes review)    ^                    |
 *     |                                                 +----- one reject --+
 *     |                                                                     | all approve
 *     +--------------- Manager starts a new revision <----------- Published <+
 *
 * If a document has no reviewers, "submit" in Draft goes straight to Approval.
 *
 * Every public action:
 *   - runs under a script lock (two people clicking at once cannot corrupt a row),
 *   - checks that the actor is allowed to do it in the current stage,
 *   - updates the register row, the Drive sharing and the History log,
 *   - e-mails the people who have to act next.
 *
 * `actor` is always the e-mail resolved by Auth.gs, never a value the browser
 * sends directly.
 */

// ---------------------------------------------------------------------------
// Creation
// ---------------------------------------------------------------------------

/**
 * Creates a new document from the template and starts its Draft stage.
 * Document Managers only.
 * @param {string} actor
 * @param {{title: string, type: string, owner: string, reviewers: string, approvers: string}} form
 * @return {string} The new document ID.
 */
function createDocument_(actor, form) {
  requireManager_(actor);
  const title = String(form.title || '').trim();
  if (!title) throw new Error('Title is required.');
  if (!DOC_TYPES[form.type]) throw new Error('Unknown type: ' + form.type);
  const owner = parseEmails_(form.owner);
  if (owner.length !== 1) throw new Error('Exactly one owner is required.');
  const reviewers = parseEmails_(form.reviewers);
  const approvers = parseEmails_(form.approvers);
  if (approvers.length === 0) throw new Error('At least one approver is required.');

  return withLock_(function () {
    const id = nextDocumentId_(form.type);
    const template = DriveApp.getFileById(getSetting_('TEMPLATE_DOC_ID'));
    const folder = DriveApp.getFolderById(getSetting_('WORKING_FOLDER_ID'));
    const file = template.makeCopy(id + ' - ' + title, folder);

    fillPlaceholders_(file.getId(), {
      DOC_ID: id,
      TITLE: title,
      TYPE: form.type,
      OWNER: owner[0],
      VERSION: '1.0',
      CREATED: formatDate_(new Date()),
      ORGANISATION: getSetting_('ORGANISATION_NAME'),
    });
    protectWorkingFile_(file.getId());

    const doc = appendDocument_({
      'ID': id,
      'Title': title,
      'Type': form.type,
      'Doc URL': file.getUrl(),
      'Owner': owner[0],
      'Reviewers': reviewers.join(', '),
      'Approvers': approvers.join(', '),
      'Status': STATUS.DRAFT,
      'Stage due date': dueDate_(STAGE_DAYS[STATUS.DRAFT]),
      'Version': '',
      'Publication date': '',
      'Doc file ID': file.getId(),
      'Reviewed by': '',
      'Approved by': '',
      'Next version': '1.0',
      'Last PDF URL': '',
    });
    applyStageSharing_(doc);
    logHistory_(doc, '', 'Created', actor);
    notifyStage_(doc);
    return id;
  });
}

/**
 * Replaces {{KEY}} placeholders in the body, header and footer of a Doc.
 * @param {string} docId Google Docs file ID.
 * @param {Object<string, string>} values
 */
function fillPlaceholders_(docId, values) {
  const gdoc = DocumentApp.openById(docId);
  [gdoc.getBody(), gdoc.getHeader(), gdoc.getFooter()].forEach(function (section) {
    if (!section) return;
    Object.keys(values).forEach(function (key) {
      // replaceText takes a regular expression; '$' in the value is escaped
      // so it is inserted literally.
      section.replaceText('\\{\\{' + key + '\\}\\}', String(values[key]).replace(/\$/g, '\\$'));
    });
  });
  gdoc.saveAndClose();
}

// ---------------------------------------------------------------------------
// Owner actions
// ---------------------------------------------------------------------------

/**
 * Draft -> Review (or -> Approval when there are no reviewers).
 * Owner (or a Document Manager on the owner's behalf).
 * @param {string} actor
 * @param {string} docId
 */
function submitForReview_(actor, docId) {
  withLock_(function () {
    const doc = getDocument_(docId);
    requireStatus_(doc, STATUS.DRAFT);
    requireOwnerOrManager_(doc, actor);
    if (parseEmails_(doc.Reviewers).length === 0) {
      moveTo_(doc, STATUS.APPROVAL, 'Submitted for approval (no reviewers)', actor);
    } else {
      moveTo_(doc, STATUS.REVIEW, 'Submitted for review', actor);
    }
  });
}

/**
 * Revision -> Approval. Owner (or a Document Manager).
 * @param {string} actor
 * @param {string} docId
 */
function submitForApproval_(actor, docId) {
  withLock_(function () {
    const doc = getDocument_(docId);
    requireStatus_(doc, STATUS.REVISION);
    requireOwnerOrManager_(doc, actor);
    moveTo_(doc, STATUS.APPROVAL, 'Submitted for approval', actor);
  });
}

// ---------------------------------------------------------------------------
// Reviewer actions
// ---------------------------------------------------------------------------

/**
 * A reviewer declares their review done. When the last reviewer is done,
 * the document moves to Revision.
 * @param {string} actor
 * @param {string} docId
 * @param {string=} comment Optional summary (detailed remarks belong in the Doc comments).
 */
function markReviewDone_(actor, docId, comment) {
  withLock_(function () {
    const doc = getDocument_(docId);
    requireStatus_(doc, STATUS.REVIEW);
    if (parseEmails_(doc.Reviewers).indexOf(actor) < 0) throw new Error('You are not a reviewer of ' + docId + '.');
    const done = parseEmails_(doc['Reviewed by']);
    if (done.indexOf(actor) >= 0) throw new Error('You already marked your review as done.');
    done.push(actor);
    doc['Reviewed by'] = done.join(', ');
    saveDocument_(doc);
    logHistory_(doc, doc.Status, 'Review done', actor, comment);
    advanceIfComplete_(doc, actor);
  });
}

/**
 * Document Manager closes the review stage even if some reviewers did not
 * respond (fixed deadlines: the stage does not wait forever).
 * @param {string} actor
 * @param {string} docId
 * @param {string=} comment
 */
function closeReview_(actor, docId, comment) {
  requireManager_(actor);
  withLock_(function () {
    const doc = getDocument_(docId);
    requireStatus_(doc, STATUS.REVIEW);
    const missing = pending_(doc).join(', ');
    moveTo_(doc, STATUS.REVISION, 'Review closed by manager', actor,
      (comment ? comment + ' ' : '') + (missing ? '(no response from ' + missing + ')' : ''));
  });
}

// ---------------------------------------------------------------------------
// Approver actions
// ---------------------------------------------------------------------------

/**
 * An approver approves. When the last approver approves, the document is published.
 * @param {string} actor
 * @param {string} docId
 * @param {string=} comment
 */
function approve_(actor, docId, comment) {
  withLock_(function () {
    const doc = getDocument_(docId);
    requireStatus_(doc, STATUS.APPROVAL);
    requireApprover_(doc, actor);
    const done = parseEmails_(doc['Approved by']);
    if (done.indexOf(actor) >= 0) throw new Error('You already approved this version.');
    done.push(actor);
    doc['Approved by'] = done.join(', ');
    saveDocument_(doc);
    logHistory_(doc, doc.Status, 'Approved', actor, comment);
    advanceIfComplete_(doc, actor);
  });
}

/**
 * An approver rejects: the document goes back to Revision and every approval
 * of this round is cancelled. A comment is mandatory.
 * @param {string} actor
 * @param {string} docId
 * @param {string} comment
 */
function reject_(actor, docId, comment) {
  if (!String(comment || '').trim()) throw new Error('Please explain why you reject the document.');
  withLock_(function () {
    const doc = getDocument_(docId);
    requireStatus_(doc, STATUS.APPROVAL);
    requireApprover_(doc, actor);
    moveTo_(doc, STATUS.REVISION, 'Rejected', actor, comment);
  });
}

// ---------------------------------------------------------------------------
// Manager actions on existing documents
// ---------------------------------------------------------------------------

/**
 * Published -> Draft: starts the next version of a published document.
 * The Google Doc is unlocked; the published PDF stays untouched.
 * @param {string} actor
 * @param {string} docId
 * @param {string} bump 'minor' (1.0 -> 1.1) or 'major' (1.3 -> 2.0).
 */
function startNewRevision_(actor, docId, bump) {
  requireManager_(actor);
  withLock_(function () {
    const doc = getDocument_(docId);
    requireStatus_(doc, STATUS.PUBLISHED);
    doc['Next version'] = bumpVersion_(doc.Version, bump);
    setLocked_(doc['Doc file ID'], false);
    moveTo_(doc, STATUS.DRAFT, 'New ' + (bump === 'major' ? 'major' : 'minor') + ' revision started', actor,
      'Target version ' + doc['Next version']);
  });
}

/**
 * Replaces the owner, reviewers and/or approvers of a document that is not
 * published (e.g. someone left). Sharing is re-applied immediately, and the
 * stage completes if every remaining reviewer/approver has already acted.
 * @param {string} actor
 * @param {string} docId
 * @param {{owner: string, reviewers: string, approvers: string}} form
 */
function updateParticipants_(actor, docId, form) {
  requireManager_(actor);
  const owner = parseEmails_(form.owner);
  if (owner.length !== 1) throw new Error('Exactly one owner is required.');
  const reviewers = parseEmails_(form.reviewers);
  const approvers = parseEmails_(form.approvers);
  if (approvers.length === 0) throw new Error('At least one approver is required.');
  withLock_(function () {
    const doc = getDocument_(docId);
    if (doc.Status === STATUS.PUBLISHED) throw new Error('Start a new revision before changing participants.');
    const before = 'owner ' + doc.Owner + '; reviewers ' + doc.Reviewers + '; approvers ' + doc.Approvers;
    doc.Owner = owner[0];
    doc.Reviewers = reviewers.join(', ');
    doc.Approvers = approvers.join(', ');
    // Forget actions of people who are no longer participants.
    doc['Reviewed by'] = parseEmails_(doc['Reviewed by']).filter(function (e) { return reviewers.indexOf(e) >= 0; }).join(', ');
    doc['Approved by'] = parseEmails_(doc['Approved by']).filter(function (e) { return approvers.indexOf(e) >= 0; }).join(', ');
    saveDocument_(doc);
    applyStageSharing_(doc);
    logHistory_(doc, doc.Status, 'Participants changed', actor, 'Before: ' + before);
    if (doc.Status === STATUS.REVIEW && reviewers.length === 0) {
      moveTo_(doc, STATUS.REVISION, 'Review skipped (no reviewers left)', actor);
    } else {
      advanceIfComplete_(doc, actor);
    }
  });
}

// ---------------------------------------------------------------------------
// Transitions
// ---------------------------------------------------------------------------

/**
 * Moves a document to a new stage: resets the per-round bookkeeping, sets the
 * due date, saves, re-shares, logs and notifies.
 * @param {Object} doc Row object.
 * @param {string} status Target stage.
 * @param {string} action Text for the History log.
 * @param {string} actor
 * @param {string=} comment
 */
function moveTo_(doc, status, action, actor, comment) {
  const from = doc.Status;
  doc.Status = status;
  doc['Stage due date'] = STAGE_DAYS[status] ? dueDate_(STAGE_DAYS[status]) : '';
  if (status === STATUS.REVIEW) doc['Reviewed by'] = '';
  if (status === STATUS.APPROVAL || status === STATUS.REVISION) doc['Approved by'] = '';
  saveDocument_(doc);
  applyStageSharing_(doc);
  logHistory_(doc, from, action, actor, comment);
  notifyStage_(doc, { action: action, actor: actor, comment: comment });
}

/**
 * Checks whether every reviewer (in Review) or approver (in Approval) has
 * acted, and if so moves the document on.
 * @param {Object} doc Row object.
 * @param {string} actor The person whose action completed the stage.
 */
function advanceIfComplete_(doc, actor) {
  if (pending_(doc).length > 0) return;
  if (doc.Status === STATUS.REVIEW) {
    moveTo_(doc, STATUS.REVISION, 'All reviews done', actor);
  } else if (doc.Status === STATUS.APPROVAL) {
    publish_(doc, actor);
  }
}

/**
 * Approval -> Published: fixes the version number and publication date,
 * locks the Google Doc, stores a PDF copy in the Published folder and gives
 * everyone read access.
 * @param {Object} doc Row object.
 * @param {string} actor
 */
function publish_(doc, actor) {
  const from = doc.Status;
  const version = normaliseVersion_(doc['Next version']) || '1.0';
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  // 1. PDF copy of the approved content (taken before anything else changes).
  const source = DriveApp.getFileById(doc['Doc file ID']);
  const pdf = DriveApp.getFolderById(getSetting_('PUBLISHED_FOLDER_ID'))
    .createFile(source.getAs(MimeType.PDF))
    .setName(doc.ID + ' - ' + doc.Title + ' - v' + version + '.pdf')
    .setDescription('Published ' + formatDate_(today) + ', approved by ' + doc['Approved by'] + '.');

  // 2. Register.
  doc.Status = STATUS.PUBLISHED;
  doc.Version = version;
  doc['Publication date'] = today;
  doc['Stage due date'] = '';
  doc['Last PDF URL'] = pdf.getUrl();
  saveDocument_(doc);

  // 3. Lock the working Doc and give everybody read access.
  applyStageSharing_(doc);
  setLocked_(doc['Doc file ID'], true);
  sharePublishedPdf_(pdf, doc);

  logHistory_(doc, from, 'Published v' + version, actor, pdf.getUrl());
  notifyStage_(doc, { action: 'Published', actor: actor });
}

// ---------------------------------------------------------------------------
// Who has to act?
// ---------------------------------------------------------------------------

/**
 * People who still have to act in the current stage.
 * @param {Object} doc Row object.
 * @return {string[]} E-mails.
 */
function pending_(doc) {
  switch (doc.Status) {
    case STATUS.DRAFT:
    case STATUS.REVISION:
      return parseEmails_(doc.Owner);
    case STATUS.REVIEW: {
      const done = parseEmails_(doc['Reviewed by']);
      return parseEmails_(doc.Reviewers).filter(function (e) { return done.indexOf(e) < 0; });
    }
    case STATUS.APPROVAL: {
      const done = parseEmails_(doc['Approved by']);
      return parseEmails_(doc.Approvers).filter(function (e) { return done.indexOf(e) < 0; });
    }
    default:
      return [];
  }
}

// ---------------------------------------------------------------------------
// Versions
// ---------------------------------------------------------------------------

/**
 * Normalises a version read from the sheet ("1" or 1 -> "1.0").
 * @param {string|number} v
 * @return {string} "major.minor", or '' if empty.
 */
function normaliseVersion_(v) {
  const s = String(v == null ? '' : v).trim();
  if (!s) return '';
  const parts = s.split('.');
  return Number(parts[0]) + '.' + (parts[1] ? Number(parts[1]) : 0);
}

/**
 * @param {string} current Published version, e.g. "1.2".
 * @param {string} bump 'minor' or 'major'.
 * @return {string} e.g. "1.3" or "2.0".
 */
function bumpVersion_(current, bump) {
  const v = normaliseVersion_(current) || '0.0';
  const major = Number(v.split('.')[0]);
  const minor = Number(v.split('.')[1]);
  return bump === 'major' ? (major + 1) + '.0' : major + '.' + (minor + 1);
}

// ---------------------------------------------------------------------------
// Guards
// ---------------------------------------------------------------------------

/** @throws {Error} if the document is not in the expected stage. */
function requireStatus_(doc, status) {
  if (doc.Status !== status) {
    throw new Error(doc.ID + ' is in stage ' + doc.Status + ', not ' + status + '. Refresh the page.');
  }
}

/** @throws {Error} unless actor is a Document Manager. */
function requireManager_(actor) {
  if (!isManager_(actor)) throw new Error('Only a Document Manager can do this.');
}

/** @throws {Error} unless actor is the owner or a Document Manager. */
function requireOwnerOrManager_(doc, actor) {
  if (parseEmails_(doc.Owner).indexOf(actor) < 0 && !isManager_(actor)) {
    throw new Error('Only the owner of ' + doc.ID + ' (or a Document Manager) can do this.');
  }
}

/** @throws {Error} unless actor is an approver of the document. */
function requireApprover_(doc, actor) {
  if (parseEmails_(doc.Approvers).indexOf(actor) < 0) throw new Error('You are not an approver of ' + doc.ID + '.');
}

/**
 * Runs fn under the script lock (max 30 s wait) so that concurrent clicks
 * are processed one after the other.
 * @param {function(): *} fn
 * @return {*} fn's result.
 */
function withLock_(fn) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    settingsCache_ = null;
    return fn();
  } finally {
    SpreadsheetApp.flush();
    lock.releaseLock();
  }
}
