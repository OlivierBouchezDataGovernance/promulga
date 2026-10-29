/**
 * @fileoverview Drive sharing per stage, and locking of published documents.
 *
 * Uses the Drive advanced service (API v3) rather than DriveApp because it
 * can share WITHOUT sending Google's own "X shared a document with you"
 * e-mail: the workflow sends its own, clearer e-mail instead. If Drive
 * refuses a silent share (typically: the address has no Google account),
 * we fall back to DriveApp, which notifies the person by e-mail.
 */

/**
 * Computes the desired Drive role of each participant for a stage.
 * @param {Object} doc Row object.
 * @param {string} status Stage to apply (usually doc.Status).
 * @return {Object<string, string>} e-mail -> Drive role ('reader'|'commenter'|'writer').
 */
function desiredAccess_(doc, status) {
  const matrix = ACCESS_MATRIX[status];
  const groups = {
    owner: parseEmails_(doc.Owner),
    reviewers: parseEmails_(doc.Reviewers),
    approvers: parseEmails_(doc.Approvers),
    managers: getManagers_(),
  };
  const access = {};
  Object.keys(groups).forEach(function (group) {
    const role = matrix[group];
    if (!role) return;
    groups[group].forEach(function (email) {
      if (!access[email] || ROLE_RANK[role] > ROLE_RANK[access[email]]) access[email] = role;
    });
  });
  return access;
}

/**
 * Brings the Drive permissions of a file in line with `access`: adds missing
 * people, changes roles, and removes every other individual user. The file
 * owner (the script owner) is never touched; "anyone with the link"
 * permissions are left alone.
 * @param {string} fileId
 * @param {Object<string, string>} access e-mail -> role.
 */
function syncPermissions_(fileId, access) {
  const current = Drive.Permissions.list(fileId, {
    fields: 'permissions(id,emailAddress,role,type)',
  }).permissions || [];
  const seen = {};

  current.forEach(function (p) {
    if (p.type !== 'user' || p.role === 'owner') return;
    const email = String(p.emailAddress || '').toLowerCase();
    seen[email] = true;
    const wanted = access[email];
    try {
      if (!wanted) {
        Drive.Permissions.remove(fileId, p.id);
      } else if (wanted !== p.role) {
        Drive.Permissions.update({ role: wanted }, fileId, p.id);
      }
    } catch (err) {
      // Typically a permission inherited from a folder someone shared by hand:
      // it can only be changed on the folder. Logged, not fatal.
      console.warn('Could not change access of ' + email + ' on ' + fileId + ': ' + err.message);
    }
  });

  const owner = Session.getEffectiveUser().getEmail().toLowerCase();
  Object.keys(access).forEach(function (email) {
    if (seen[email] || email === owner) return;
    grant_(fileId, email, access[email]);
  });
}

/**
 * Grants one role silently, falling back to DriveApp (with notification).
 * @param {string} fileId
 * @param {string} email
 * @param {string} role Drive v3 role.
 */
function grant_(fileId, email, role) {
  try {
    Drive.Permissions.create(
      { type: 'user', role: role, emailAddress: email },
      fileId,
      { sendNotificationEmail: false });
  } catch (err) {
    console.warn('Silent share failed for ' + email + ' (' + err.message + '), retrying with notification.');
    const file = DriveApp.getFileById(fileId);
    if (role === 'writer') file.addEditor(email);
    else if (role === 'commenter') file.addCommenter(email);
    else file.addViewer(email);
  }
}

/**
 * Applies the access matrix of the document's current stage.
 * @param {Object} doc Row object (Status already updated).
 */
function applyStageSharing_(doc) {
  syncPermissions_(doc['Doc file ID'], desiredAccess_(doc, doc.Status));
}

/**
 * Locks or unlocks the working document (Drive "content restriction": even
 * editors cannot change the text while it is set). Only the restriction is
 * sent: Drive refuses other metadata changes on a read-only file.
 * @param {string} fileId
 * @param {boolean} locked
 */
function setLocked_(fileId, locked) {
  Drive.Files.update({
    contentRestrictions: [locked
      ? { readOnly: true, reason: 'Published version - start a new revision to change it.' }
      : { readOnly: false }],
  }, fileId);
}

/**
 * Hardens a new working document: editors (the owner) cannot re-share it,
 * and readers/commenters cannot download, print or copy it.
 * @param {string} fileId
 */
function protectWorkingFile_(fileId) {
  Drive.Files.update({ writersCanShare: false, copyRequiresWriterPermission: true }, fileId);
}

/**
 * Shares a published PDF with every participant, the Document Managers and
 * the optional PUBLISHED_READERS, and optionally with anyone who has the link.
 * @param {GoogleAppsScript.Drive.File} pdf
 * @param {Object} doc Row object.
 */
function sharePublishedPdf_(pdf, doc) {
  const access = desiredAccess_(doc, STATUS.PUBLISHED);
  parseEmails_(getSetting_('PUBLISHED_READERS')).forEach(function (e) { access[e] = 'reader'; });
  syncPermissions_(pdf.getId(), access);
  if (getSetting_('PUBLISH_LINK_SHARING').toLowerCase() === 'yes') {
    pdf.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
  }
}
