# Trying Promulga on a laptop with WSL (Ubuntu)

A local test before a real server: everything runs in your Ubuntu WSL
distribution and you open it in the Windows browser at `http://localhost`.
No domain, no certificate, no e-mail: invitations and reminders are read in
*Admin > Outbox*.

Tested with Ubuntu 24.04 on WSL 2 and the Ubuntu `docker.io` package.

## 1. Open Ubuntu and check Docker

Start the **Ubuntu** app (or `wsl` in a Windows terminal), then:

```bash
docker compose version
```

If `docker` answers "permission denied", add yourself to the `docker` group,
then close and reopen the Ubuntu window:

```bash
sudo usermod -aG docker $USER
```

If it answers "Cannot connect to the Docker daemon", start the service:

```bash
sudo service docker start
```

> **Docker Desktop is also installed on Windows?** Use one Docker at a time.
> Either the Ubuntu one (this guide), or Docker Desktop with *Settings >
> Resources > WSL integration* enabled for Ubuntu. Two engines competing for
> port 80 is the most common cause of "address already in use".

## 2. Get the code into Linux

The project lives in your Windows folder, which Ubuntu sees under `/mnt/c`.
Clone it into your Linux home: it is faster than working on `/mnt/c`, and it
takes exactly what is committed (no local test files).

```bash
git clone /mnt/c/Users/olivi/OneDrive/work/projects/policy-workflow ~/promulga && cd ~/promulga
```

Later, to take the latest committed version:

```bash
cd ~/promulga && git pull
```

## 3. Configure for local use

```bash
cp .env.example .env && nano .env
```

Set these values (leave the others as they are):

```
SITE_DOMAIN=:80
BASE_URL=http://localhost
SECURE_COOKIES=false
ADMIN_EMAIL=you@example.org
ADMIN_PASSWORD=a-long-local-password
```

`SITE_DOMAIN=:80` tells Caddy to serve plain HTTP without asking for a
certificate; `SECURE_COOKIES=false` is required for sign-in over HTTP. Save
with Ctrl+O, Enter, quit with Ctrl+X.

## 4. Start

```bash
docker compose up -d --build
```

The first build downloads the images and takes a few minutes. Check that the
three containers are up (`app` must say *healthy* after ~30 seconds):

```bash
docker compose ps
```

Open **http://localhost** in your Windows browser (WSL 2 forwards the port
automatically). Click *Sign in* with `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

## 5. Load the demo content

```bash
docker compose exec app promulga demo-data
```

Then follow the demo scenario of [QUICKSTART.md](QUICKSTART.md): the example
users' invitation links are in *Admin > Outbox*; open each one in a private
browser window to play the reviewer, the owner and the approver.

## 6. Stop, restart, reset

| Action | Command |
|---|---|
| Stop (data kept) | `docker compose down` |
| Start again | `docker compose up -d` |
| Rebuild after `git pull` | `docker compose up -d --build` |
| Follow the logs | `docker compose logs -f app` |
| **Erase everything** (database and files) | `docker compose down -v` |

## Port 80 already used?

Use port 8080 instead: in `docker-compose.yml`, under `caddy > ports`, replace
`"80:80"` by `"8080:80"`, set `BASE_URL=http://localhost:8080` in `.env`, run
`docker compose up -d` and open http://localhost:8080.

## Next: a real server

When the test is convincing, follow [INSTALL.md](INSTALL.md) for the Google
Cloud VM: the same files, with a real domain name, HTTPS and (optionally) SMTP.
