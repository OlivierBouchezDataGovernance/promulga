/**
 * @fileoverview E-mail notifications.
 *
 * Uses MailApp (sends from the script owner's Gmail). A free Gmail account
 * may send to about 100 recipients per day from Apps Script, so:
 *   - only people who have to act next are e-mailed (plus everyone once at
 *     publication);
 *   - each e-mail goes to one person (it contains their personal link);
 *   - when the daily quota is exhausted, the e-mail is skipped and logged,
 *     but the workflow transition itself still succeeds.
 */

/**
 * Sends the e-mails that belong to the stage a document has just entered.
 * @param {Object} doc Row object (after the transition).
 * @param {{action: string, actor: string, comment: string}=} event What happened.
 */
function notifyStage_(doc, event) {
  event = event || {};
  const due = formatDate_(doc['Stage due date']);
  const docLink = '<a href="' + escapeHtml_(doc['Doc URL']) + '">' + escapeHtml_(doc.ID + ' - ' + doc.Title) + '</a>';
  const why = event.comment ? '<p><b>Comment from ' + escapeHtml_(event.actor) + ':</b><br>' +
    escapeHtml_(event.comment).replace(/\n/g, '<br>') + '</p>' : '';

  switch (doc.Status) {
    case STATUS.DRAFT:
      parseEmails_(doc.Owner).forEach(function (to) {
        sendMail_(to, 'Please draft ' + doc.ID + ' (due ' + due + ')',
          '<p>You are the owner of ' + docLink + ' (' + escapeHtml_(doc.Type) + ', version ' +
          escapeHtml_(doc['Next version']) + ').</p>' +
          '<p>Please write the draft directly in the Google Doc. When it is ready, open the web app ' +
          'and click <b>Submit</b>. Due date: <b>' + due + '</b>.</p>', to, 'drafts');
      });
      break;

    case STATUS.REVIEW:
      pending_(doc).forEach(function (to) {
        sendMail_(to, 'Please review ' + doc.ID + ' (due ' + due + ')',
          '<p>' + escapeHtml_(doc.Owner) + ' asks you to review ' + docLink + '.</p>' +
          '<p>Add your remarks as <b>comments in the Google Doc</b> (select text, then Insert &gt; Comment). ' +
          'When you have finished, click <b>Review done</b> in the web app. Due date: <b>' + due + '</b>.</p>',
          to, 'reviews');
      });
      break;

    case STATUS.REVISION:
      parseEmails_(doc.Owner).forEach(function (to) {
        const intro = event.action === 'Rejected'
          ? '<p>' + docLink + ' was <b>rejected</b> during approval and comes back to you for revision.</p>'
          : '<p>The review of ' + docLink + ' is finished.</p>';
        sendMail_(to, 'Please revise ' + doc.ID + ' (due ' + due + ')',
          intro + why +
          '<p>Process the comments in the Google Doc (reply, resolve, correct the text), then click ' +
          '<b>Submit for approval</b> in the web app. Due date: <b>' + due + '</b>.</p>', to, 'drafts');
      });
      break;

    case STATUS.APPROVAL:
      pending_(doc).forEach(function (to) {
        sendMail_(to, 'Please approve ' + doc.ID + ' (due ' + due + ')',
          '<p>' + docLink + ' (version ' + escapeHtml_(doc['Next version']) + ') is submitted for your approval.</p>' +
          '<p>Read the Google Doc, then click <b>Approve</b> or <b>Reject</b> (with a comment) in the web app. ' +
          'Due date: <b>' + due + '</b>.</p>', to, 'approvals');
      });
      break;

    case STATUS.PUBLISHED: {
      const everyone = parseEmails_([doc.Owner, doc.Reviewers, doc.Approvers].join(',')).concat(getManagers_())
        .filter(function (e, i, all) { return all.indexOf(e) === i; });
      everyone.forEach(function (to) {
        sendMail_(to, doc.ID + ' version ' + doc.Version + ' is published',
          '<p>' + escapeHtml_(doc.Title) + ' (' + escapeHtml_(doc.Type) + ') version <b>' +
          escapeHtml_(doc.Version) + '</b> is approved and published on ' + formatDate_(doc['Publication date']) + '.</p>' +
          '<p>Official copy (PDF): <a href="' + escapeHtml_(doc['Last PDF URL']) + '">' +
          escapeHtml_(doc.ID + ' v' + doc.Version) + '</a></p>', to, 'register');
      });
      break;
    }
  }
}

/**
 * Sends one e-mail with the standard footer (personal link), unless the
 * daily quota is exhausted.
 * @param {string} to Recipient e-mail.
 * @param {string} subject
 * @param {string} htmlBody Already escaped HTML.
 * @param {string} linkFor E-mail whose personal link is added (usually `to`).
 * @param {string=} tab Web app tab the link opens.
 * @return {boolean} True if sent.
 */
function sendMail_(to, subject, htmlBody, linkFor, tab) {
  if (MailApp.getRemainingDailyQuota() < 1) {
    console.warn('Daily e-mail quota exhausted; not sent to ' + to + ': ' + subject);
    return false;
  }
  const org = getSetting_('ORGANISATION_NAME') || 'Document workflow';
  const link = personalLink_(linkFor, tab);
  const footer = link
    ? '<p><a href="' + escapeHtml_(link) + '"><b>Open the document workflow</b></a> ' +
      '(this is your personal link: please do not forward it).</p>'
    : '<p>(The web app link is not configured yet: ask your Document Manager.)</p>';
  MailApp.sendEmail({
    to: to,
    subject: '[' + org + '] ' + subject,
    htmlBody: '<div style="font-family:Arial,sans-serif;font-size:14px">' + htmlBody + footer +
      '<p style="color:#777;font-size:12px">Automatic message from the ' + escapeHtml_(org) +
      ' governance document workflow.</p></div>',
    name: org + ' document workflow',
  });
  return true;
}

/**
 * Sends a person their personal link (from the web app home page). Only
 * known participants and managers receive it, so the form cannot be used to
 * spam arbitrary addresses.
 * @param {string} email
 */
function sendAccessLink_(email) {
  const target = parseEmails_(email)[0];
  if (!target) return;
  const known = getManagers_().indexOf(target) >= 0 || readDocuments_().some(function (d) {
    return parseEmails_([d.Owner, d.Reviewers, d.Approvers].join(',')).indexOf(target) >= 0;
  });
  if (!known) {
    console.info('Link requested for unknown address ' + target + ' (ignored).');
    return;
  }
  // Throttle: one link e-mail per address per 10 minutes.
  const cache = CacheService.getScriptCache();
  if (cache.get('link:' + target)) return;
  cache.put('link:' + target, '1', 600);
  sendMail_(target, 'Your link to the document workflow',
    '<p>Here is your personal link to the governance document workflow. Bookmark it.</p>', target);
}

/**
 * Escapes text for inclusion in HTML.
 * @param {*} value
 * @return {string}
 */
function escapeHtml_(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
