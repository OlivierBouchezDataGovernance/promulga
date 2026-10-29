# Installing Promulga

Promulga runs as three Docker containers on one Linux server:

| Container | Role |
|---|---|
| `app` | the application (portal + workspace), port 8000 inside the Docker network |
| `gotenberg` | converts Word / LibreOffice files to PDF at publication (internal only) |
| `caddy` | HTTPS reverse proxy with automatic Let's Encrypt certificates (ports 80 and 443) |

All data – database, uploaded files, published PDFs, logo and banner – is in
the Docker volume `promulga-data`. Back it up (see below).

> You run your own server. Promulga is provided as is, without warranty or
> support (see the README).

**Requirements:** a Linux server with 2 vCPU and 2–4 GB of RAM, Docker with
the Compose plugin, ports 80 and 443 reachable, and a host name pointing to the
server (for HTTPS). An SMTP account is optional.

- [Part A – Google Cloud VM, step by step](#part-a--google-cloud-vm-step-by-step)
- [Part B – configuration reference](#part-b--configuration-reference)
- [Part C – operations: updates, backups, logs](#part-c--operations)

---

## Part A – Google Cloud VM, step by step

This part assumes a Compute Engine VM with **Ubuntu 24.04**, for example an
`e2-standard-2` in `europe-west1-c`, with *HTTP traffic* and *HTTPS traffic*
allowed in its firewall settings. Replace `INSTANCE` by the name of your VM.

### A1. Start the VM and give it a fixed IP address

1. Google Cloud console > **Compute Engine > VM instances** > select the VM > **Start**.
   (Or, with the gcloud CLI: `gcloud compute instances start INSTANCE --zone europe-west1-c`.)
2. An ephemeral IP changes at each restart; make it permanent:
   **VPC network > IP addresses** > find the VM's external address >
   **⋮ > Promote to static IP address** > give it a name (e.g. `promulga-ip`).
   A static IP is free while the VM runs; Google charges a small fee for a
   reserved address attached to a *stopped* VM.
3. Check that **HTTP** and **HTTPS** traffic are allowed: VM details > *Edit* >
   *Firewalls*.

### A2. Give it a host name

HTTPS needs a name. Two options:

- **Your own domain (recommended):** at your DNS provider, add an `A` record,
  for example `promulga.your-domain.com → <static IP>`. Wait until
  `nslookup promulga.your-domain.com` returns the IP.
- **No domain, quick demo:** use a free wildcard DNS such as sslip.io. For IP
  `34.77.12.34` the name `34-77-12-34.sslip.io` already points to it. (Let's
  Encrypt limits certificates per domain, so this can occasionally fail;
  use your own domain for anything lasting.)

### A3. Install Docker on the VM

Open an SSH session: console > VM > **SSH** button (or
`gcloud compute ssh INSTANCE --zone europe-west1-c`). Then:

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git nano
```

```bash
curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh
```

```bash
sudo usermod -aG docker $USER
```

Close the SSH window and open a new one (so the `docker` group applies), then check:

```bash
docker compose version
```

### A4. Copy Promulga to the VM

**If the code is on GitHub:**

```bash
git clone https://github.com/<account>/<repository>.git promulga && cd promulga
```

**If it is only on your computer** (Windows, in the project folder, with the
Google Cloud SDK installed): make an archive of the last commit and copy it.

```bash
git archive --format=tar.gz -o promulga.tar.gz HEAD
```

```bash
gcloud compute scp promulga.tar.gz INSTANCE:~ --zone europe-west1-c
```

Then on the VM:

```bash
mkdir -p promulga && tar xzf promulga.tar.gz -C promulga && cd promulga
```

### A5. Configure

```bash
./deploy/setup.sh && nano .env
```

`setup.sh` creates `.env` (mode 600) and the `secrets/` folder: one file per
secret (SMTP password, GitHub token, SSO client secret, OnlyOffice key,
certificate password), readable by you only. **Passwords never go in `.env`.**

In `.env`, change at least:

| Variable | Example |
|---|---|
| `SITE_DOMAIN` | `promulga.your-domain.com` |
| `BASE_URL` | `https://promulga.your-domain.com` |
| `ADMIN_EMAIL` | your address (leave `ADMIN_PASSWORD` empty) |

Save with Ctrl+O, Enter, and quit with Ctrl+X. Leave `SMTP_*` empty for a demo:
e-mails then appear in *Admin > Outbox* instead of being sent. For e-mail,
fill `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_FROM` in `.env` and the
password alone in `secrets/smtp_password`. Check the result:

```bash
./deploy/check.sh
```

### A6. Start

```bash
docker compose up -d --build
```

The first build takes a few minutes. Follow the start-up:

```bash
docker compose logs -f app caddy
```

At the first start the application creates the administrator and prints a
**one-time link** (valid 24 h) to choose the password:

```bash
docker compose logs app | grep set-password
```

Open it once Caddy has its certificate. If you preferred to set
`ADMIN_PASSWORD`, Promulga makes you change it at the first sign-in; then
remove it from `.env`. *Admin > Security* lists what remains to fix.

With the online editor (about 2 GB of RAM more), set `ONLYOFFICE_URL=/onlyoffice`
in `.env` and start with:

```bash
docker compose --profile editor up -d --build
```

### A7. Optional: demo content

```bash
docker compose exec app promulga demo-data
```

It creates three taxonomies, four example participants (`@example.org`, no
password; their invitation links are in *Admin > Outbox*) and five documents
in different stages. See [QUICKSTART.md](QUICKSTART.md) for a demo scenario.

### A8. Costs and stopping

The VM is billed while it runs (roughly 50 USD per month for an
`e2-standard-2` running 24/7; an `e2-medium` with 4 GB is enough for Promulga).
Stopping the VM between demos stops the compute charges; the disk and the
static IP are still billed (a few USD per month). Everything restarts by
itself when the VM starts (`restart: unless-stopped`).

---

## Part B – configuration reference

Server settings are in `.env` (see the comments in `.env.example`). After a
change: `docker compose up -d`.

| Variable | Purpose |
|---|---|
| `SITE_DOMAIN` | host name(s) Caddy serves with HTTPS; `:80` for plain HTTP tests (then also `SECURE_COOKIES=false`) |
| `BASE_URL` | public URL used in e-mail links |
| `SECRET_KEY` | not needed: a random key is generated and kept in the data volume |
| `REF_PATTERN`, `FILENAME_PATTERN` | document references and file names, see [WORD-TEMPLATES.md](WORD-TEMPLATES.md) |
| `EXPORT_TAG`, `GITHUB_*`, `EXPORT_SITE_URL` | public export, see [INTEGRATIONS.md](INTEGRATIONS.md) |
| `SIGN_PDFS`, `SIGNING_CERT_FILE` | electronic seal of published PDFs |
| `ONLYOFFICE_URL` | `/onlyoffice` to enable the online editor (profile `editor`) |
| `HTTP_PORT`, `HTTPS_PORT` | ports published by Caddy |
| `SECURE_COOKIES` | `true` in production (HTTPS) |
| `TZ` | time zone of deadlines and reminders |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | first administrator, created only if there is no administrator yet (one-time link in the logs; a password set here must be changed at first sign-in) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_FROM`, `SMTP_STARTTLS`, `SMTP_SSL` | outgoing e-mail (password in `secrets/smtp_password`). Google Cloud blocks port 25: use 587 or 465 |
| `PUBLIC_HOST`, `ADMIN_HOST` | two separate sites, e.g. `docs.example.org` (portal) and `admin.example.org` (workspace); list both in `SITE_DOMAIN`, separated by a comma |
| `PORTAL_ALLOWED_IPS` | restrict the portal to networks, e.g. `10.0.0.0/8` (intranet). The workspace stays reachable |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_LABEL`, `OIDC_AUTO_CREATE_ROLE` | single sign-on with an OpenID Connect provider; redirect URI `BASE_URL/auth/oidc/callback` |
| `OIDC_ROLE_CLAIM`, `OIDC_ROLE_MAP` | roles from the provider's groups, e.g. `OIDC_ROLE_MAP=platform-admin=admin, dg-lead=manager, data-consumer=viewer`; refreshed at each sign-in |
| `DATABASE_URL` | PostgreSQL instead of SQLite (`postgresql+psycopg://user:pass@host/db`) |
| `MAX_UPLOAD_MB` | upload limit (default 25) |

Everything else – colours, logo, banner, footer, portal access, document
types, template, stage durations, reminders – is set in **Admin > Settings**.

**Single sign-on examples**

- *Microsoft Entra ID*: register an application, redirect URI (Web)
  `https://<host>/auth/oidc/callback`; `OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0`.
- *Google*: OAuth client of type "Web application";
  `OIDC_ISSUER=https://accounts.google.com`.
- *Keycloak*: `OIDC_ISSUER=https://<keycloak>/realms/<realm>`.
- *Authelia* (with LLDAP as directory): `OIDC_ISSUER=https://auth.example.org`, client with scopes
  `openid email profile groups`, `token_endpoint_auth_method: client_secret_basic`.

People must exist in Promulga (invited) unless `OIDC_AUTO_CREATE_ROLE` is set
(e.g. `viewer`). Matching is done on the e-mail address.

**E-mail without your own server:** any SMTP relay works. Many have a free
tier suitable for a pilot (a few hundred e-mails per day). Configure SPF/DKIM
for the sender domain as the provider explains, or messages may end in spam.

---

## Part C – operations

**Update to a new version**

```bash
cd ~/promulga && docker compose exec app promulga backup && git pull && docker compose up -d --build
```

The database schema is migrated automatically at start-up (also from 0.1).
Run `./deploy/setup.sh` again after an update: it adds any new secret file.

**Back up**: `promulga backup` writes a consistent archive of the database and
all files in the data volume (`/data/backups`); copy it off the server:

```bash
docker compose exec app promulga backup
```

```bash
docker compose cp app:/data/backups ./backups
```

Schedule it (cron on the host, e.g. every night) and add **disk snapshots** in
Google Cloud (*Compute Engine > Snapshots > Create snapshot schedule*). The
date of the last backup is shown in *Admin > Security*.

**Restore**: copy the archive into the container, stop the web server and
restore (this replaces the current data):

```bash
docker compose cp promulga-backup-20261105-020000.tar.gz app:/data/
```

```bash
docker compose stop app && docker compose run --rm app promulga restore /data/promulga-backup-20261105-020000.tar.gz --yes
```

Then `docker compose up -d`.

**Logs**

```bash
docker compose logs --tail 200 app
```

**Administrator lost their password**

```bash
docker compose exec app promulga create-admin you@example.org
```

It prints a one-time link to choose a new password.

**Commands inside the container:** `promulga reminders` (queue today's
reminders now), `promulga flush-outbox` (send pending e-mails), `promulga demo-data`,
`promulga backup`, `promulga export [--github]`.

Integrations (online editor, GitHub export, API, webhooks, electronic seal):
see [INTEGRATIONS.md](INTEGRATIONS.md).
