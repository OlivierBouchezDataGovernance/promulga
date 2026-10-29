/**
 * @fileoverview Who is using the web app?
 *
 * Free (consumer) Google accounts do not reveal their e-mail to a web app
 * deployed "Execute as: Me": Session.getActiveUser() returns '' for everyone
 * except the script owner. The prototype therefore identifies people with a
 * personal link: the web app URL plus their e-mail and an HMAC signature
 * computed with a secret that only exists in the script properties.
 *
 *   https://script.google.com/.../exec?u=alice@example.com&t=<signature>
 *
 * Resolution order:
 *   1. Session.getActiveUser() if Google gives it (script owner, or the
 *      "Execute as: User accessing the web app" mode, or same Workspace domain).
 *   2. Otherwise a valid (u, t) pair from the personal link.
 *
 * Limits (documented in the README): a personal link works for whoever has
 * it, so it must not be forwarded; "Invalidate all personal links" in the
 * sheet menu rotates the secret.
 */

/**
 * Signs an e-mail address with the link secret.
 * @param {string} email Lower-case e-mail.
 * @return {string} URL-safe signature (first 32 chars of base64url HMAC-SHA256).
 */
function signEmail_(email) {
  const secret = PropertiesService.getScriptProperties().getProperty(PROP.LINK_SECRET);
  if (!secret) throw new Error('Link secret missing: run "Policy workflow > Set up".');
  const bytes = Utilities.computeHmacSha256Signature(email, secret);
  return Utilities.base64EncodeWebSafe(bytes).replace(/=+$/, '').slice(0, 32);
}

/**
 * Builds the personal web app link of a participant.
 * @param {string} email
 * @param {string=} tab Optional tab to open: drafts | reviews | approvals | register.
 * @return {string} The link, or '' if WEB_APP_URL is not known yet.
 */
function personalLink_(email, tab) {
  const base = getWebAppUrl_();
  if (!base) return '';
  const e = String(email).toLowerCase();
  return base + '?u=' + encodeURIComponent(e) + '&t=' + signEmail_(e) + (tab ? '&tab=' + tab : '');
}

/**
 * The web app URL: the Settings value if filled in (recommended, because it
 * is always the /exec URL), else whatever Apps Script reports.
 * @return {string}
 */
function getWebAppUrl_() {
  const fromSettings = getSetting_('WEB_APP_URL');
  if (fromSettings) return fromSettings.replace(/\?.*$/, '');
  try {
    return ScriptApp.getService().getUrl() || '';
  } catch (err) {
    return '';
  }
}

/**
 * Resolves the current user from Google or from the personal-link parameters.
 * @param {{u: string, t: string}=} auth Parameters of the personal link.
 * @return {string} Lower-case e-mail, or '' if unknown.
 */
function resolveUser_(auth) {
  const google = (Session.getActiveUser().getEmail() || '').toLowerCase();
  if (google) return google;
  if (!auth || !auth.u || !auth.t) return '';
  const email = String(auth.u).trim().toLowerCase();
  // Constant-time comparison is not available; the signature is long enough
  // for a prototype and the secret can be rotated at any time.
  return signEmail_(email) === String(auth.t) ? email : '';
}

/**
 * Same as resolveUser_ but throws when nobody is identified. Used by every
 * server function called from the web page.
 * @param {{u: string, t: string}=} auth
 * @return {string}
 */
function requireUser_(auth) {
  const email = resolveUser_(auth);
  if (!email) throw new Error('You are not identified. Open the web app with your personal link (see your e-mails).');
  return email;
}

/**
 * @param {string} email
 * @return {boolean} True if the person is a Document Manager.
 */
function isManager_(email) {
  return getManagers_().indexOf(String(email).toLowerCase()) >= 0;
}
