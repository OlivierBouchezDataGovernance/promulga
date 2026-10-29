"""Passkeys: registration and sign-in with a software authenticator (user verification on)."""

from __future__ import annotations

import json
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256
from struct import pack

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2 import cbor
from fido2.cose import ES256

from .conftest import csrf_of, login


def b64(data: bytes) -> str:
    return urlsafe_b64encode(data).decode().rstrip("=")


def unb64(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


class Authenticator:
    """A minimal platform authenticator: one ES256 key, user present AND verified (like Windows Hello)."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.user_handle = None
        self.count = 0

    def create(self, options: dict, origin: str) -> dict:
        self.user_handle = unb64(options["user"]["id"])
        client_data = json.dumps({"type": "webauthn.create", "challenge": options["challenge"],
                                  "origin": origin}).encode()
        auth_data = (sha256(options["rp"]["id"].encode()).digest() + b"\x45" + pack(">I", 0) + b"\x00" * 16
                     + pack(">H", len(self.credential_id)) + self.credential_id
                     + cbor.encode(ES256.from_cryptography_key(self.key.public_key())))
        attestation = cbor.encode({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client_data), "attestationObject": b64(attestation),
                             "transports": ["internal"]}, "clientExtensionResults": {}}

    def get(self, options: dict, origin: str) -> dict:
        self.count += 1
        client_data = json.dumps({"type": "webauthn.get", "challenge": options["challenge"],
                                  "origin": origin}).encode()
        auth_data = sha256(options["rpId"].encode()).digest() + b"\x05" + pack(">I", self.count)
        signature = self.key.sign(auth_data + sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client_data), "authenticatorData": b64(auth_data),
                             "signature": b64(signature), "userHandle": b64(self.user_handle)},
                "clientExtensionResults": {}}


def _token(client, path="/admin/account"):
    return csrf_of(client.get(path).text)


def test_register_then_sign_in_with_passkey(client):
    device = Authenticator()
    login(client, "admin@example.org", "admin-password-123")
    headers = {"X-CSRF-Token": _token(client)}
    options = client.post("/auth/passkey/register/options", headers=headers).json()
    assert options["rp"]["id"] == "testserver" and options["authenticatorSelection"]["residentKey"] == "required"
    r = client.post("/auth/passkey/register/verify", headers=headers,
                    json={"name": "Test laptop", "credential": device.create(options, "http://testserver")})
    assert r.json() == {"ok": True}
    assert "Test laptop" in client.get("/admin/account").text

    # Sign out, then sign in without e-mail nor password.
    client.post("/auth/logout", data={"csrf": _token(client)})
    assert client.get("/admin", follow_redirects=False).status_code == 303
    page_token = _token(client, "/auth/login")
    options = client.post("/auth/passkey/login/options", headers={"X-CSRF-Token": page_token}).json()
    assert "allowCredentials" not in options or not options["allowCredentials"]   # discoverable
    r = client.post("/auth/passkey/login/verify", json={"next": "/admin",
                                                         "credential": device.get(options, "http://testserver")})
    assert r.json() == {"ok": True, "redirect": "/admin"}
    assert "Hello" in client.get("/admin").text
    assert "Strong authentication of administrators" in client.get("/admin/security").text


def test_passkey_rejects_wrong_origin_and_unknown_keys(client):
    device = Authenticator()
    login(client, "admin@example.org", "admin-password-123")
    headers = {"X-CSRF-Token": _token(client)}
    options = client.post("/auth/passkey/register/options", headers=headers).json()
    r = client.post("/auth/passkey/register/verify", headers=headers,
                    json={"credential": device.create(options, "https://evil.example")})
    assert r.status_code == 400 and "could not be verified" in r.json()["error"]
    assert client.post("/auth/passkey/register/options").status_code == 400      # no CSRF header

    client.post("/auth/logout", data={"csrf": _token(client)})
    options = client.post("/auth/passkey/login/options").json()
    stranger = Authenticator()
    stranger.user_handle = b"1"
    r = client.post("/auth/passkey/login/verify", json={"credential": stranger.get(options, "http://testserver")})
    assert r.status_code == 401 and "not known" in r.json()["error"]
