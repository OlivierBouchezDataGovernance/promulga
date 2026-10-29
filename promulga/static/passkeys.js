/* Passkeys (WebAuthn) for Promulga: add a passkey (My account) and sign in with it (login page).
   The server sends WebAuthn options as JSON with base64url strings; the browser API wants
   ArrayBuffers, and the answer goes back as JSON with base64url strings again. */
(function () {
  "use strict";

  function b64urlToBuffer(value) {
    var base64 = value.replace(/-/g, "+").replace(/_/g, "/");
    while (base64.length % 4) { base64 += "="; }
    var raw = atob(base64), bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) { bytes[i] = raw.charCodeAt(i); }
    return bytes.buffer;
  }

  function bufferToB64url(buffer) {
    var bytes = new Uint8Array(buffer), raw = "";
    for (var i = 0; i < bytes.length; i++) { raw += String.fromCharCode(bytes[i]); }
    return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok || data.ok === false) { throw new Error(data.error || ("HTTP " + r.status)); }
        return data;
      });
    });
  }

  function show(el, message, isError) {
    if (!el) { return; }
    el.textContent = message;
    el.className = "flash " + (isError ? "flash-error" : "flash-success");
    el.hidden = false;
  }

  function friendly(err) {
    if (err && err.name === "NotAllowedError") { return "Cancelled or timed out. Try again."; }
    if (err && err.name === "InvalidStateError") { return "This device already has a passkey for your account."; }
    if (err && err.name === "SecurityError") { return "Passkeys need HTTPS (or http://localhost)."; }
    return (err && err.message) || String(err);
  }

  var supported = !!(window.PublicKeyCredential && navigator.credentials);
  document.querySelectorAll("[data-passkey]").forEach(function (el) { el.hidden = !supported; });
  document.querySelectorAll("[data-passkey-unsupported]").forEach(function (el) { el.hidden = supported; });

  // ----------------------------------------------------------- add a passkey
  var addButton = document.getElementById("passkey-add");
  if (addButton) {
    addButton.addEventListener("click", function () {
      var status = document.getElementById("passkey-status");
      var name = (document.getElementById("passkey-name") || {}).value || "";
      addButton.disabled = true;
      post("/auth/passkey/register/options").then(function (opts) {
        opts.challenge = b64urlToBuffer(opts.challenge);
        opts.user.id = b64urlToBuffer(opts.user.id);
        (opts.excludeCredentials || []).forEach(function (c) { c.id = b64urlToBuffer(c.id); });
        return navigator.credentials.create({ publicKey: opts });
      }).then(function (cred) {
        var response = cred.response;
        return post("/auth/passkey/register/verify", {
          name: name,
          credential: {
            id: cred.id, rawId: bufferToB64url(cred.rawId), type: cred.type,
            response: {
              clientDataJSON: bufferToB64url(response.clientDataJSON),
              attestationObject: bufferToB64url(response.attestationObject),
              transports: response.getTransports ? response.getTransports() : []
            },
            clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {}
          }
        });
      }).then(function () {
        show(status, "Passkey added. You can now sign in with it.");
        setTimeout(function () { window.location.reload(); }, 900);
      }).catch(function (err) {
        show(status, friendly(err), true);
        addButton.disabled = false;
      });
    });
  }

  // -------------------------------------------------------- sign in with it
  var loginButton = document.getElementById("passkey-login");
  if (loginButton) {
    loginButton.addEventListener("click", function () {
      var status = document.getElementById("passkey-status");
      var next = loginButton.getAttribute("data-next") || "/admin";
      loginButton.disabled = true;
      post("/auth/passkey/login/options").then(function (opts) {
        opts.challenge = b64urlToBuffer(opts.challenge);
        (opts.allowCredentials || []).forEach(function (c) { c.id = b64urlToBuffer(c.id); });
        return navigator.credentials.get({ publicKey: opts });
      }).then(function (cred) {
        var response = cred.response;
        return post("/auth/passkey/login/verify", {
          next: next,
          credential: {
            id: cred.id, rawId: bufferToB64url(cred.rawId), type: cred.type,
            response: {
              clientDataJSON: bufferToB64url(response.clientDataJSON),
              authenticatorData: bufferToB64url(response.authenticatorData),
              signature: bufferToB64url(response.signature),
              userHandle: response.userHandle ? bufferToB64url(response.userHandle) : null
            },
            clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {}
          }
        });
      }).then(function (data) {
        window.location.href = data.redirect || next;
      }).catch(function (err) {
        show(status, friendly(err), true);
        loginButton.disabled = false;
      });
    });
  }
})();
