/**
 * @fileoverview One-time setup and the spreadsheet menu.
 *
 * The administrator runs "Policy workflow > Set up" once from the register
 * sheet. It is idempotent: running it again only adds what is missing.
 */

/**
 * Adds the "Policy workflow" menu when the register sheet is opened.
 * (Simple trigger: runs automatically, no installation needed.)
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Policy workflow')
    .addItem('Set up (run once)', 'setup')
    .addItem('Install daily reminder trigger', 'installDailyTrigger')
    .addSeparator()
    .addItem('Send overdue reminders now', 'sendOverdueReminders')
    .addItem('Show my web app link', 'showMyLink')
    .addItem('Invalidate all personal links', 'resetPersonalLinks')
    .addToUi();
}

/**
 * Creates the tabs, folders, template and secrets the workflow needs.
 * Safe to run several times.
 */
function setup() {
  requireScriptOwner_();
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const props = PropertiesService.getScriptProperties();
  props.setProperty(PROP.SPREADSHEET_ID, ss.getId());
  if (!props.getProperty(PROP.LINK_SECRET)) {
    props.setProperty(PROP.LINK_SECRET, Utilities.getUuid() + Utilities.getUuid());
  }

  // Tabs with frozen, bold headers.
  ensureSheet_(ss, SHEETS.DOCUMENTS, DOC_COLUMNS);
  ensureSheet_(ss, SHEETS.HISTORY, HISTORY_COLUMNS);
  const settingsSheet = ensureSheet_(ss, SHEETS.SETTINGS, ['Key', 'Value', 'Help']);

  // Versions must stay text: otherwise Sheets turns "1.0" into the number 1.
  const docs = ss.getSheetByName(SHEETS.DOCUMENTS);
  ['Version', 'Next version'].forEach(function (h) {
    docs.getRange(1, DOC_COLUMNS.indexOf(h) + 1, docs.getMaxRows(), 1).setNumberFormat('@');
  });
  ss.getSheetByName(SHEETS.HISTORY).getRange('C:C').setNumberFormat('@');

  // Default settings rows (existing values are kept).
  const existing = settingsSheet.getDataRange().getValues().map(function (r) { return r[0]; });
  SETTINGS_DEFAULTS.forEach(function (s) {
    if (existing.indexOf(s[0]) < 0) settingsSheet.appendRow(s);
  });
  settingsCache_ = null;

  const me = Session.getEffectiveUser().getEmail();
  if (!getSetting_('DOCUMENT_MANAGERS')) setSetting_('DOCUMENT_MANAGERS', me);

  // Drive folders, next to nothing in particular: "My Drive" root.
  if (!getSetting_('WORKING_FOLDER_ID')) {
    setSetting_('WORKING_FOLDER_ID', DriveApp.createFolder('Policy workflow - Working documents').getId());
  }
  if (!getSetting_('PUBLISHED_FOLDER_ID')) {
    setSetting_('PUBLISHED_FOLDER_ID', DriveApp.createFolder('Policy workflow - Published').getId());
  }
  if (!getSetting_('TEMPLATE_DOC_ID')) {
    setSetting_('TEMPLATE_DOC_ID', createDefaultTemplate_().getId());
  }

  const url = getWebAppUrl_();
  SpreadsheetApp.getUi().alert(
    'Setup complete',
    'Tabs, folders and template are ready.\n\n' +
    'Next steps (see README):\n' +
    '1. Deploy the web app (Deploy > New deployment > Web app).\n' +
    '2. Paste its /exec URL in Settings > WEB_APP_URL' + (url ? ' (current: ' + url + ')' : '') + '.\n' +
    '3. Run "Install daily reminder trigger".',
    SpreadsheetApp.getUi().ButtonSet.OK);
}

/**
 * Creates a tab with the given header row if it does not exist yet.
 * Missing header cells are added on the right of an existing tab.
 * @param {GoogleAppsScript.Spreadsheet.Spreadsheet} ss
 * @param {string} name
 * @param {string[]} headers
 * @return {GoogleAppsScript.Spreadsheet.Sheet}
 */
function ensureSheet_(ss, name, headers) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) sheet = ss.insertSheet(name);
  const current = sheet.getLastColumn() > 0
    ? sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0]
    : [];
  if (current.join('|') !== headers.join('|')) {
    if (current.filter(String).length === 0) {
      sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    } else if (headers.slice(0, current.length).join('|') === current.join('|')) {
      sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    } else {
      throw new Error('Tab "' + name + '" has unexpected headers. Expected: ' + headers.join(', '));
    }
  }
  sheet.getRange(1, 1, 1, headers.length).setFontWeight('bold');
  sheet.setFrozenRows(1);
  return sheet;
}

/**
 * Creates a minimal Google Docs template with the placeholders the workflow
 * fills in. The administrator can replace it by any Doc that uses the same
 * placeholders ({{DOC_ID}}, {{TITLE}}, {{TYPE}}, {{OWNER}}, {{VERSION}},
 * {{CREATED}}, {{ORGANISATION}}).
 * @return {GoogleAppsScript.Document.Document}
 */
function createDefaultTemplate_() {
  const doc = DocumentApp.create('Policy workflow - TEMPLATE');
  const body = doc.getBody();
  body.appendParagraph('{{ORGANISATION}}').setHeading(DocumentApp.ParagraphHeading.SUBTITLE);
  body.appendParagraph('{{TITLE}}').setHeading(DocumentApp.ParagraphHeading.TITLE);
  body.appendTable([
    ['Document ID', '{{DOC_ID}}'],
    ['Type', '{{TYPE}}'],
    ['Owner', '{{OWNER}}'],
    ['Version', '{{VERSION}} (see the register for the current published version)'],
    ['Created', '{{CREATED}}'],
  ]);
  ['1. Purpose', '2. Scope', '3. Definitions', '4. Policy statements',
    '5. Roles and responsibilities', '6. Compliance and exceptions', '7. References']
    .forEach(function (h) {
      body.appendParagraph(h).setHeading(DocumentApp.ParagraphHeading.HEADING1);
      body.appendParagraph('Write here.');
    });
  // Remove the empty first paragraph DocumentApp.create() adds.
  const first = body.getChild(0);
  if (first.getType() === DocumentApp.ElementType.PARAGRAPH && !first.asParagraph().getText()) {
    first.removeFromParent();
  }
  doc.saveAndClose();
  const file = DriveApp.getFileById(doc.getId());
  file.moveTo(DriveApp.getFolderById(getSetting_('WORKING_FOLDER_ID')));
  return file;
}

/**
 * Installs the daily time-driven trigger that sends overdue reminders.
 * Removes any previous copy first, so it never runs twice a day.
 */
function installDailyTrigger() {
  requireScriptOwner_();
  ScriptApp.getProjectTriggers()
    .filter(function (t) { return t.getHandlerFunction() === 'sendOverdueReminders'; })
    .forEach(function (t) { ScriptApp.deleteTrigger(t); });
  const hour = Math.min(23, Math.max(0, Number(getSetting_('REMINDER_HOUR')) || 8));
  ScriptApp.newTrigger('sendOverdueReminders').timeBased().everyDays(1).atHour(hour).create();
  SpreadsheetApp.getUi().alert('Daily reminders will be sent every day around ' + hour + ':00.');
}

/** Shows the administrator's own web app link (menu helper). */
function showMyLink() {
  requireScriptOwner_();
  const me = Session.getEffectiveUser().getEmail();
  const link = personalLink_(me);
  SpreadsheetApp.getUi().alert(link ? 'Your personal link:\n\n' + link
    : 'Set WEB_APP_URL in the Settings tab first (see README, step 5).');
}

/**
 * Generates a new link secret: every personal link sent so far stops working.
 * Use it if a link was forwarded to the wrong person. Participants get a new
 * link with the next e-mail, or ask for one on the web app home page.
 */
function resetPersonalLinks() {
  requireScriptOwner_();
  PropertiesService.getScriptProperties()
    .setProperty(PROP.LINK_SECRET, Utilities.getUuid() + Utilities.getUuid());
  SpreadsheetApp.getUi().alert('All personal links have been invalidated.');
}

/**
 * Menu functions are public, so a web app visitor could try to call them with
 * google.script.run from the browser console. This guard lets them run only
 * for the script owner (menu, script editor). In the web app ("Execute as:
 * Me"), visitors with a free Google account have no active-user e-mail, so
 * they are refused.
 * @throws {Error}
 */
function requireScriptOwner_() {
  const active = (Session.getActiveUser().getEmail() || '').toLowerCase();
  if (!active || active !== Session.getEffectiveUser().getEmail().toLowerCase()) {
    throw new Error('Only the owner of the script can run this function.');
  }
}
