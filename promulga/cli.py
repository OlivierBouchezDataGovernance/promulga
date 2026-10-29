"""Command line: ``promulga <command>`` (inside Docker: ``docker compose exec app promulga <command>``).

Commands
  serve                     run the web server (what the Docker image does)
  create-admin EMAIL        create or promote an administrator and print a set-password link
  reminders                 queue today's reminders now (ignores the configured hour)
  flush-outbox              send pending e-mails now
  demo-data                 load example taxonomies, users and documents (for a demo instance)
  backup [--output DIR]     archive the database and all files (default: DATA_DIR/backups)
  restore ARCHIVE --yes     restore an archive made by 'backup' (replaces the current data!)
  export [--github]         build the public package (zip in DATA_DIR/exports) or push it to GitHub
  db-revision MESSAGE       (developers) generate a migration after changing the models
"""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from . import db as database
from .config import get_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promulga", description="Promulga governance document workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    admin = sub.add_parser("create-admin")
    admin.add_argument("email")
    admin.add_argument("--password", action="store_true", help="type a password instead of getting a link")
    sub.add_parser("reminders")
    sub.add_parser("flush-outbox")
    sub.add_parser("demo-data")
    backup = sub.add_parser("backup")
    backup.add_argument("--output", default="")
    restore = sub.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--yes", action="store_true", help="confirm that the current data is replaced")
    exp = sub.add_parser("export")
    exp.add_argument("--github", action="store_true")
    rev = sub.add_parser("db-revision")
    rev.add_argument("message")
    args = parser.parse_args(argv)
    _drop_root()

    if args.command == "serve":
        import uvicorn
        # One worker: the in-process scheduler must run exactly once.
        uvicorn.run("promulga.main:app", host=args.host, port=args.port, proxy_headers=True,
                    forwarded_allow_ips="*", workers=1)
        return 0

    cfg = get_config()
    if args.command == "restore":       # before opening the database
        return _restore(cfg, Path(args.archive), args.yes)
    database.init_engine(cfg.database_url)

    if args.command == "create-admin":
        from .models import ROLE_ADMIN, User
        from .security import check_password_strength, create_token, hash_password
        with database.session_scope() as db:
            email = args.email.strip().lower()
            user = db.query(User).filter_by(email=email).first() or User(email=email, name="Administrator")
            user.role, user.active = ROLE_ADMIN, True
            db.add(user)
            db.flush()
            if args.password:
                password = getpass.getpass("Password: ")
                check_password_strength(password)
                user.password_hash = hash_password(password)
                print(f"Administrator {email} ready. Sign in at {cfg.base_url}/auth/login")
            else:
                token = create_token(db, user, "invite", 48)
                print(f"Administrator {email} ready. Choose the password here (valid 48 h):")
                print(f"  {cfg.base_url}/auth/set-password?token={token}")
    elif args.command == "reminders":
        from . import reminders
        with database.session_scope() as db:
            print(f"{reminders.run(db)} e-mail(s) queued.")
    elif args.command == "flush-outbox":
        from . import mailer
        with database.session_scope() as db:
            print(f"{mailer.flush_outbox(db)} e-mail(s) sent.")
    elif args.command == "demo-data":
        from .demo import load_demo
        with database.session_scope() as db:
            print(load_demo(db))
    elif args.command == "backup":
        return _backup(cfg, Path(args.output) if args.output else cfg.data_dir / "backups")
    elif args.command == "export":
        from . import export
        with database.session_scope() as db:
            files = export.build(db)
            if args.github:
                sha = export.push_github(files, "Promulga: public export (command line)")
                print(f"Pushed: {sha or 'no change'}")
            else:
                out = cfg.data_dir / "exports"
                out.mkdir(exist_ok=True)
                target = out / f"public-documents-{datetime.now():%Y%m%d-%H%M}.zip"
                target.write_bytes(export.zip_bytes(files))
                print(f"{len(files)} file(s) in {target}")
    elif args.command == "db-revision":
        from . import migrate
        migrate.new_revision(database.get_engine(), args.message)
    return 0


def _drop_root() -> None:
    """'docker compose exec app promulga ...' runs as root: switch to the app user, so that the
    files it creates (database journal, backups, signing key) stay readable by the web server."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        import pwd
        entry = pwd.getpwnam("promulga")
    except (ImportError, KeyError):
        return
    os.setgid(entry.pw_gid)
    os.setuid(entry.pw_uid)
    os.environ["HOME"] = entry.pw_dir


BACKUP_ITEMS = ["files", "assets", "signing", "secret_key"]


def _backup(cfg, folder: Path) -> int:
    """Consistent archive: SQLite online backup + files. PostgreSQL: use pg_dump for the database."""
    from . import site_settings
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"promulga-backup-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(target, "w:gz") as tar:
        if cfg.database_url.startswith("sqlite:///"):
            source = sqlite3.connect(cfg.database_url.removeprefix("sqlite:///"))
            copy = Path(tmp) / "promulga.sqlite3"
            dest = sqlite3.connect(copy)
            source.backup(dest)
            dest.close()
            source.close()
            tar.add(copy, arcname="promulga.sqlite3")
        else:
            print("PostgreSQL database: back it up with pg_dump; files are archived here.")
        for item in BACKUP_ITEMS:
            path = cfg.data_dir / item
            if path.exists():
                tar.add(path, arcname=item)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    with database.session_scope() as db:
        site_settings.set_many(db, {"last_backup": datetime.now().strftime("%Y-%m-%d %H:%M")})
    print(f"Backup written: {target} ({target.stat().st_size // 1024} KB). Copy it off the server.")
    return 0


def _restore(cfg, archive: Path, confirmed: bool) -> int:
    if not confirmed:
        print("This REPLACES the database and all files. Stop the application first, then add --yes.")
        return 1
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            top = member.name.split("/")[0]
            if top not in BACKUP_ITEMS + ["promulga.sqlite3"] or member.issym() or member.islnk() \
                    or ".." in Path(member.name).parts:
                raise SystemExit(f"Unexpected entry in the archive: {member.name}")
        tar.extractall(cfg.data_dir, filter="data")
    print(f"Restored into {cfg.data_dir}. Start the application again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
