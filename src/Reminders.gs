/**
 * @fileoverview Daily reminders for overdue stages (time-driven trigger).
 *
 * Installed by "Policy workflow > Install daily reminder trigger". Each day:
 *   - every person who still has to act on an overdue stage receives ONE
 *     digest e-mail listing all their overdue documents;
 *   - every Document Manager receives one digest of all overdue documents.
 * Digests keep the number of e-mails (and the Gmail quota) low.
 */

/**
 * Entry point of the daily trigger (can also be run from the menu).
 * @param {Object=} e Trigger event. When the function is not started by one
 *     of this project's triggers, only the script owner may run it (it is
 *     public, so this stops web app visitors from mass-sending reminders).
 */
function sendOverdueReminders(e) {
  const fromTrigger = e && e.triggerUid && ScriptApp.getProjectTriggers().some(function (t) {
    return t.getUniqueId() === String(e.triggerUid);
  });
  if (!fromTrigger) requireScriptOwner_();
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const overdue = readDocuments_().filter(function (d) {
    return d.Status !== STATUS.PUBLISHED && d['Stage due date'] instanceof Date && d['Stage due date'] < today;
  });
  if (overdue.length === 0) {
    console.info('No overdue documents.');
    return;
  }

  // Group the overdue items by the person who has to act.
  const byPerson = {};
  const managerLines = [];
  overdue.forEach(function (d) {
    const days = Math.round((today - d['Stage due date']) / 86400000);
    const who = pending_(d);
    const line = '<li><a href="' + escapeHtml_(d['Doc URL']) + '">' + escapeHtml_(d.ID + ' - ' + d.Title) +
      '</a>: ' + escapeHtml_(d.Status) + ' stage, due ' + formatDate_(d['Stage due date']) +
      ' (<b>' + days + ' day' + (days === 1 ? '' : 's') + ' late</b>)';
    managerLines.push(line + ' - waiting for ' + escapeHtml_(who.join(', ') || 'nobody') + '</li>');
    who.forEach(function (email) {
      (byPerson[email] = byPerson[email] || []).push(line + ' - you have to ' + escapeHtml_(expectedAction_(d)) + '</li>');
    });
  });

  let sent = 0;
  Object.keys(byPerson).forEach(function (email) {
    const ok = sendMail_(email, 'Reminder: ' + byPerson[email].length + ' overdue document(s)',
      '<p>The following governance document(s) are waiting for you and their deadline has passed:</p>' +
      '<ul>' + byPerson[email].join('') + '</ul>', email);
    if (ok) sent++;
  });
  getManagers_().forEach(function (email) {
    const ok = sendMail_(email, 'Overdue documents: ' + overdue.length,
      '<p>Overdue stages in the register:</p><ul>' + managerLines.join('') + '</ul>' +
      '<p>As Document Manager you can close a review that is stuck, or change participants, in the web app.</p>',
      email, 'register');
    if (ok) sent++;
  });
  console.info(overdue.length + ' overdue document(s), ' + sent + ' reminder e-mail(s) sent, ' +
    MailApp.getRemainingDailyQuota() + ' left in today\'s quota.');
}

/**
 * Plain-language description of what the pending people must do.
 * @param {Object} doc Row object.
 * @return {string}
 */
function expectedAction_(doc) {
  switch (doc.Status) {
    case STATUS.DRAFT: return 'finish the draft and submit it';
    case STATUS.REVIEW: return 'comment in the Doc and click "Review done"';
    case STATUS.REVISION: return 'process the comments and submit for approval';
    case STATUS.APPROVAL: return 'approve or reject';
    default: return 'act';
  }
}
