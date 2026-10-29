/**
 * @fileoverview The HtmlService web app: page delivery and the server
 * functions the page calls with google.script.run.
 *
 * Only functions WITHOUT a trailing underscore can be called from the
 * browser, so the public surface is kept to the `api*` functions below. Each
 * of them re-identifies the caller (requireUser_) and the workflow functions
 * re-check every permission: nothing sent by the browser is trusted.
 */

/** Tabs the page knows; used to validate the ?tab= parameter. */
const TABS = ['drafts', 'reviews', 'approvals', 'register', 'new'];

/**
 * Serves the page.
 * @param {Object} e Request event (e.parameter.u, .t, .tab).
 * @return {GoogleAppsScript.HTML.HtmlOutput}
 */
function doGet(e) {
  const params = (e && e.parameter) || {};
  const auth = { u: params.u || '', t: params.t || '' };
  const me = resolveUser_(auth);

  const template = HtmlService.createTemplateFromFile('Index');
  // Only a verified identity is handed to the page; '<' is escaped so the
  // JSON can never close the <script> tag it is printed in.
  template.bootJson = JSON.stringify({
    auth: me && auth.u ? { u: me, t: auth.t } : null,
    me: me,
    tab: TABS.indexOf(params.tab) >= 0 ? params.tab : 'drafts',
    org: getSetting_('ORGANISATION_NAME'),
  }).replace(/</g, '\\u003c');

  return template.evaluate()
    .setTitle('Document workflow')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/**
 * Inserts another HTML file (used by Index.html for Styles and Script).
 * @param {string} name File name without extension.
 * @return {string}
 */
function include(name) {
  return HtmlService.createHtmlOutputFromFile(name).getContent();
}

// ---------------------------------------------------------------------------
// API called by the page
// ---------------------------------------------------------------------------

/**
 * Everything the page needs to draw the tabs for the current user.
 * @param {{u: string, t: string}} auth
 * @return {Object}
 */
function apiDashboard(auth) {
  const me = requireUser_(auth);
  const manager = isManager_(me);
  const docs = readDocuments_()
    .map(function (d) { return summarise_(d, me); })
    .filter(function (d) { return manager || d.status === STATUS.PUBLISHED || d.involved; });
  return {
    me: me,
    isManager: manager,
    org: getSetting_('ORGANISATION_NAME'),
    types: Object.keys(DOC_TYPES),
    docs: docs,
  };
}

/**
 * Runs one workflow action and returns the refreshed dashboard.
 * @param {{u: string, t: string}} auth
 * @param {string} action submit | reviewDone | approve | reject | closeReview | newRevision | participants
 * @param {string} docId
 * @param {Object=} payload {comment}, {bump} or {owner, reviewers, approvers}.
 * @return {Object} apiDashboard result.
 */
function apiAction(auth, action, docId, payload) {
  const me = requireUser_(auth);
  payload = payload || {};
  const comment = String(payload.comment || '').slice(0, 2000);
  switch (action) {
    case 'submit': {
      const doc = getDocument_(docId);
      if (doc.Status === STATUS.REVISION) submitForApproval_(me, docId);
      else submitForReview_(me, docId);
      break;
    }
    case 'reviewDone': markReviewDone_(me, docId, comment); break;
    case 'approve': approve_(me, docId, comment); break;
    case 'reject': reject_(me, docId, comment); break;
    case 'closeReview': closeReview_(me, docId, comment); break;
    case 'newRevision': startNewRevision_(me, docId, payload.bump === 'major' ? 'major' : 'minor'); break;
    case 'participants': updateParticipants_(me, docId, payload); break;
    default: throw new Error('Unknown action ' + action);
  }
  return apiDashboard(auth);
}

/**
 * Creates a document (Document Managers only) and returns the dashboard.
 * @param {{u: string, t: string}} auth
 * @param {Object} form {title, type, owner, reviewers, approvers}
 * @return {Object} apiDashboard result plus `created` (new ID).
 */
function apiCreate(auth, form) {
  const me = requireUser_(auth);
  const id = createDocument_(me, form || {});
  const result = apiDashboard(auth);
  result.created = id;
  return result;
}

/**
 * Audit log of one document, if the caller may see the document.
 * @param {{u: string, t: string}} auth
 * @param {string} docId
 * @return {Object[]}
 */
function apiHistory(auth, docId) {
  const me = requireUser_(auth);
  const doc = summarise_(getDocument_(docId), me);
  if (!isManager_(me) && !doc.involved && doc.status !== STATUS.PUBLISHED) {
    throw new Error('You cannot see this document.');
  }
  return readHistory_(docId).map(function (h) {
    return {
      when: Utilities.formatDate(h.Timestamp, Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm'),
      version: String(h.Version),
      from: h['From status'],
      to: h['To status'],
      action: h.Action,
      actor: h.Actor,
      comment: h.Comment,
    };
  });
}

/**
 * "Send me my link" form of the home page (no identification needed).
 * Always answers the same way, so it does not reveal who is a participant.
 * @param {string} email
 * @return {string} Message to display.
 */
function apiRequestLink(email) {
  try {
    sendAccessLink_(email);
  } catch (err) {
    console.warn('Link request failed: ' + err.message);
  }
  return 'If this address takes part in the workflow, a personal link is on its way. Check your mailbox (and spam folder).';
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Converts a register row to what the page needs (no Date objects: they
 * cannot be returned through google.script.run).
 * @param {Object} d Row object.
 * @param {string} me Current user.
 * @return {Object}
 */
function summarise_(d, me) {
  const owner = parseEmails_(d.Owner);
  const reviewers = parseEmails_(d.Reviewers);
  const approvers = parseEmails_(d.Approvers);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const pending = pending_(d);
  return {
    id: d.ID,
    title: d.Title,
    type: d.Type,
    url: d['Doc URL'],
    owner: owner.join(', '),
    reviewers: reviewers,
    approvers: approvers,
    reviewedBy: parseEmails_(d['Reviewed by']),
    approvedBy: parseEmails_(d['Approved by']),
    status: d.Status,
    due: formatDate_(d['Stage due date']),
    overdue: d['Stage due date'] instanceof Date && d['Stage due date'] < today,
    version: normaliseVersion_(d.Version),
    nextVersion: normaliseVersion_(d['Next version']),
    published: formatDate_(d['Publication date']),
    pdfUrl: d['Last PDF URL'],
    pending: pending,
    isOwner: owner.indexOf(me) >= 0,
    isReviewer: reviewers.indexOf(me) >= 0,
    isApprover: approvers.indexOf(me) >= 0,
    myTurn: pending.indexOf(me) >= 0,
    involved: owner.concat(reviewers, approvers).indexOf(me) >= 0,
  };
}
