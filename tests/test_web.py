"""HTTP tests: the two sites, authentication, CSRF and a full cycle through the forms."""

from __future__ import annotations

import re

from .conftest import csrf_of, login, make_pdf


def _set_password_link(client, email: str) -> str:
    """Reads the invitation link of `email` from the outbox (as SMTP is off in tests)."""
    from promulga import db as database
    from promulga.models import OutboxMail
    with database.session_scope() as db:
        mail = (db.query(OutboxMail).filter_by(to=email).filter(OutboxMail.subject.like('%invited%'))
                .order_by(OutboxMail.id.desc()).first())
        return re.search(r'href="http://testserver(/auth/set-password\?token=[^"]+)"', mail.html).group(1)


def _accept_invite(client, email: str, password: str = "secret-password-1"):
    link = _set_password_link(client, email).replace("&amp;", "&")
    page = client.get(link)
    token = re.search(r'name="token" value="([^"]+)"', page.text).group(1)
    r = client.post("/auth/set-password", data={"csrf": csrf_of(page.text), "token": token, "purpose": "invite",
                                                "password": password, "password2": password, "name": email[:3]},
                    follow_redirects=False)
    assert r.status_code == 303


def _act(client, doc_url: str, action: str, **data):
    page = client.get(doc_url)
    r = client.post(f"{doc_url}/action/{action}", data={"csrf": csrf_of(page.text), **data}, follow_redirects=True)
    assert r.status_code == 200
    return r


def test_portal_public_and_admin_protected(client):
    assert client.get("/").status_code == 200
    assert "Governance documents" in client.get("/").text
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/auth/login")
    assert client.get("/healthz").json() == {"status": "ok"}


def test_login_and_csrf(client):
    assert login(client, "admin@example.org", "wrong").headers["location"].startswith("/auth/login")
    r = client.post("/auth/login", data={"email": "admin@example.org", "password": "admin-password-123"})
    assert r.status_code == 400   # no CSRF token
    assert login(client, "admin@example.org", "admin-password-123").headers["location"] == "/admin"
    assert "Hello" in client.get("/admin").text


def test_full_cycle_over_http(client):
    login(client, "admin@example.org", "admin-password-123")
    form = client.get("/admin/documents/new")
    r = client.post("/admin/documents/new", data={
        "csrf": csrf_of(form.text), "title": "Access Review Procedure", "doc_type": "Procedure",
        "summary": "Quarterly access reviews.", "owner": "owner@example.org", "reviewers": "rev@example.org",
        "approvers": "app@example.org"}, files={"file": ("draft.pdf", make_pdf("Access review"), "application/pdf")},
        follow_redirects=False)
    assert r.status_code == 303
    doc_url = r.headers["location"]

    client.post("/auth/logout", data={"csrf": csrf_of(client.get("/admin").text)})
    _accept_invite(client, "owner@example.org")
    _act(client, doc_url, "submit")

    client.post("/auth/logout", data={"csrf": csrf_of(client.get("/admin").text)})
    _accept_invite(client, "rev@example.org")
    page = client.get(doc_url)
    assert "Review done" in page.text
    client.post(f"{doc_url}/comments", data={"csrf": csrf_of(page.text), "body": "Add the frequency."})
    assert "Add the frequency." in client.get(doc_url).text
    _act(client, doc_url, "review_done")

    client.post("/auth/logout", data={"csrf": csrf_of(client.get("/admin").text)})
    login(client, "owner@example.org", "secret-password-1")
    page = client.get(doc_url)
    client.post(f"{doc_url}/checkin", data={"csrf": csrf_of(page.text), "note": "frequency added"},
                files={"file": ("v2.pdf", make_pdf("Access review v2"), "application/pdf")})
    _act(client, doc_url, "submit")

    client.post("/auth/logout", data={"csrf": csrf_of(client.get("/admin").text)})
    _accept_invite(client, "app@example.org")
    r = _act(client, doc_url, "approve")
    assert "Published" in r.text

    portal = client.get("/d/PRC-0001")
    assert portal.status_code == 200 and "Read version 1.0" in portal.text
    pdf = client.get("/d/PRC-0001/v1.0.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "PRC-0001" in client.get("/search?q=access").text

    # A participant cannot act outside their role.
    r = client.post(f"{doc_url}/action/new_revision", data={"csrf": csrf_of(client.get(doc_url).text)},
                    follow_redirects=True)
    assert "Only a Document Manager" in r.text


def test_portal_login_mode_and_viewer(client):
    login(client, "admin@example.org", "admin-password-123")
    page = client.get("/admin/settings")
    client.post("/admin/settings/portal", data={"csrf": csrf_of(page.text), "portal_access": "login"})
    page = client.get("/admin/users")
    client.post("/admin/users/new", data={"csrf": csrf_of(page.text), "email": "viewer@example.org",
                                          "name": "Vi", "role": "viewer"})
    client.post("/auth/logout", data={"csrf": csrf_of(client.get("/admin").text)})
    assert client.get("/", follow_redirects=False).status_code == 303
    _accept_invite(client, "viewer@example.org")
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 403


def test_readable_button_text_and_type_icons(client):
    from promulga.site_settings import readable_on
    assert readable_on("#2563eb") == "#ffffff" and readable_on("#d4af5c") == "#0f172a"
    login(client, "admin@example.org", "admin-password-123")
    page = client.get("/admin/settings")
    client.post("/admin/settings/workflow", data={
        "csrf": csrf_of(page.text), "doc_types": "Policy=POL=shield-check\nNote=NOT=pin\nOdd=ODD=unknown-icon",
        "draft_days": 28, "review_days": 14, "revision_days": 14, "approval_days": 14})
    text = client.get("/admin/settings").text
    assert "Note=NOT=pin" in text and "Odd=ODD=file-text" in text
    assert "--c-on-accent: #ffffff" in client.get("/theme.css").text


def test_banner_html_is_sanitised(client):
    login(client, "admin@example.org", "admin-password-123")
    page = client.get("/admin/settings")
    client.post("/admin/settings/look", data={
        "csrf": csrf_of(page.text), "site_name": "Policy Hub", "tagline": "",
        "banner_html": '<h1 onclick="x()">Hi</h1><script>alert(1)</script>', "footer_html": "<p>Footer</p>",
        "color_primary": "#112233", "color_accent": "not-a-colour"})
    home = client.get("/").text
    assert "Policy Hub" in home and "<script>alert" not in home and "onclick" not in home
    css = client.get("/theme.css").text
    assert "#112233" in css and "not-a-colour" not in css
