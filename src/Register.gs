/**
 * @fileoverview Read/write access to the register spreadsheet:
 * the `Documents` register, the `History` audit log and the `Settings` tab.
 *
 * Rows are exchanged with the rest of the code as plain objects whose keys
 * are the column headers of DOC_COLUMNS (e.g. row['Stage due date']).
 */

/**
 * Returns the register spreadsheet. The ID is stored by `setup()` so that the
 * web app and the time-driven trigger (which have no "active" spreadsheet)
 * open the same file as the menu functions.
 * @return {GoogleAppsScript.Spreadsheet.Spreadsheet}
 */
function getRegister_() {
  const id = PropertiesService.getScriptProperties().getProperty(PROP.SPREADSHEET_ID);
  if (id) return SpreadsheetApp.openById(id);
  const active = SpreadsheetApp.getActiveSpreadsheet();
  if (!active) throw new Error('Register not found: run "Policy workflow > Set up" from the sheet first.');
  return active;
}

/**
 * Returns a tab of the register, failing with a readable message if missing.
 * @param {string} name One of SHEETS.
 * @return {GoogleAppsScript.Spreadsheet.Sheet}
 */
function getSheet_(name) {
  const sheet = getRegister_().getSheetByName(name);
  if (!sheet) throw new Error('Tab "' + name + '" is missing: run "Policy workflow > Set up".');
  return sheet;
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

/** Per-execution cache of the Settings tab. */
let settingsCache_ = null;

/**
 * Returns all settings as a {key: value} object (values trimmed strings).
 * @return {Object<string, string>}
 */
function getSettings_() {
  if (settingsCache_) return settingsCache_;
  const values = getSheet_(SHEETS.SETTINGS).getDataRange().getValues();
  const settings = {};
  values.slice(1).forEach(function (r) {
    if (r[0]) settings[String(r[0]).trim()] = String(r[1] == null ? '' : r[1]).trim();
  });
  settingsCache_ = settings;
  return settings;
}

/**
 * Returns one setting.
 * @param {string} key
 * @return {string} The value, or '' if not set.
 */
function getSetting_(key) {
  return getSettings_()[key] || '';
}

/**
 * Writes one setting (creating the row if needed).
 * @param {string} key
 * @param {string} value
 */
function setSetting_(key, value) {
  const sheet = getSheet_(SHEETS.SETTINGS);
  const keys = sheet.getRange(1, 1, sheet.getLastRow(), 1).getValues().map(function (r) { return r[0]; });
  const index = keys.indexOf(key);
  if (index >= 0) {
    sheet.getRange(index + 1, 2).setValue(value);
  } else {
    sheet.appendRow([key, value, '']);
  }
  settingsCache_ = null;
}

/**
 * Returns the Document Managers' e-mails (lower case).
 * @return {string[]}
 */
function getManagers_() {
  return parseEmails_(getSetting_('DOCUMENT_MANAGERS'));
}

// ---------------------------------------------------------------------------
// Documents register
// ---------------------------------------------------------------------------

/**
 * Reads every document of the register.
 * @return {Object[]} Row objects, each with a hidden `_row` (1-based sheet row).
 */
function readDocuments_() {
  const sheet = getSheet_(SHEETS.DOCUMENTS);
  const values = sheet.getDataRange().getValues();
  const headers = values[0];
  return values.slice(1)
    .map(function (r, i) {
      const doc = { _row: i + 2 };
      headers.forEach(function (h, c) { doc[h] = r[c]; });
      return doc;
    })
    .filter(function (doc) { return doc.ID; });
}

/**
 * Finds one document by ID.
 * @param {string} docId e.g. "POL-0003".
 * @return {Object} The row object.
 * @throws {Error} If the ID is unknown.
 */
function getDocument_(docId) {
  const doc = readDocuments_().find(function (d) { return d.ID === docId; });
  if (!doc) throw new Error('Unknown document ' + docId + '.');
  return doc;
}

/**
 * Writes a row object back to its sheet row (all columns).
 * @param {Object} doc Row object returned by readDocuments_/getDocument_.
 */
function saveDocument_(doc) {
  doc['Last update'] = new Date();
  const sheet = getSheet_(SHEETS.DOCUMENTS);
  const row = DOC_COLUMNS.map(function (h) { return doc[h] === undefined ? '' : doc[h]; });
  sheet.getRange(doc._row, 1, 1, row.length).setValues([row]);
}

/**
 * Appends a new document to the register.
 * @param {Object} doc Row object without `_row`.
 * @return {Object} The same object with `_row` set.
 */
function appendDocument_(doc) {
  doc['Last update'] = new Date();
  const sheet = getSheet_(SHEETS.DOCUMENTS);
  sheet.appendRow(DOC_COLUMNS.map(function (h) { return doc[h] === undefined ? '' : doc[h]; }));
  doc._row = sheet.getLastRow();
  return doc;
}

/**
 * Computes the next free ID for a document type, e.g. "STD-0004".
 * @param {string} type A key of DOC_TYPES.
 * @return {string}
 */
function nextDocumentId_(type) {
  const prefix = DOC_TYPES[type];
  const max = readDocuments_().reduce(function (m, d) {
    const match = String(d.ID).match(new RegExp('^' + prefix + '-(\\d+)$'));
    return match ? Math.max(m, Number(match[1])) : m;
  }, 0);
  return prefix + '-' + ('0000' + (max + 1)).slice(-4);
}

// ---------------------------------------------------------------------------
// History (audit log)
// ---------------------------------------------------------------------------

/**
 * Appends one line to the audit log. Called for every transition and action.
 * @param {Object} doc Row object (after the change).
 * @param {string} fromStatus Status before the action.
 * @param {string} action Short verb, e.g. "Review done".
 * @param {string} actor E-mail of the person who acted (or "system").
 * @param {string=} comment Optional free text.
 */
function logHistory_(doc, fromStatus, action, actor, comment) {
  getSheet_(SHEETS.HISTORY).appendRow([
    new Date(), doc.ID, logVersion_(doc), fromStatus, doc.Status, action, actor, comment || '',
  ]);
}

/**
 * Version shown in the log: the published version once published, else the
 * version being prepared (e.g. "1.1 (draft)").
 * @param {Object} doc Row object.
 * @return {string}
 */
function logVersion_(doc) {
  if (doc.Status === STATUS.PUBLISHED) return normaliseVersion_(doc.Version);
  return normaliseVersion_(doc['Next version']) + ' (draft)';
}

/**
 * Returns the audit log of one document, oldest first.
 * @param {string} docId
 * @return {Object[]} Objects keyed by HISTORY_COLUMNS.
 */
function readHistory_(docId) {
  const values = getSheet_(SHEETS.HISTORY).getDataRange().getValues();
  return values.slice(1)
    .filter(function (r) { return r[1] === docId; })
    .map(function (r) {
      const entry = {};
      HISTORY_COLUMNS.forEach(function (h, c) { entry[h] = r[c]; });
      return entry;
    });
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/**
 * Splits a comma/semicolon/space separated list of e-mails, lower-cases,
 * de-duplicates and validates them.
 * @param {string|string[]} value
 * @return {string[]}
 * @throws {Error} If one entry is not an e-mail address.
 */
function parseEmails_(value) {
  const list = Array.isArray(value) ? value : String(value || '').split(/[,;\s]+/);
  const out = [];
  list.forEach(function (e) {
    const email = String(e).trim().toLowerCase();
    if (!email) return;
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new Error('Not an e-mail address: ' + email);
    if (out.indexOf(email) < 0) out.push(email);
  });
  return out;
}

/**
 * Returns today + n calendar days, at midnight (script time zone).
 * @param {number} days
 * @return {Date}
 */
function dueDate_(days) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + days);
  return d;
}

/**
 * Formats a date as yyyy-MM-dd in the script time zone ('' for empty).
 * @param {Date|string} value
 * @return {string}
 */
function formatDate_(value) {
  if (!value) return '';
  const d = value instanceof Date ? value : new Date(value);
  if (isNaN(d.getTime())) return String(value);
  return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd');
}
