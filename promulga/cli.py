"""Command line: ``promulga <command>`` (inside Docker: ``docker compose exec app promulga <command>``).

Commands
  serve                     run the web server (what the Docker image does)
  create-admin EMAIL        create or promote an administrator and print a set-password link
  reminders                 queue today's reminders now (ignores the configured hour)
  flush-outbox              send pending e-mails now
  demo-data                 load example taxonomies, users and documents (for a demo instance)
"""

from __future__ import annotations

import argparse
import getpass
import sys

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
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn
        # One worker: the in-process scheduler must run exactly once.
        uvicorn.run("promulga.main:app", host=args.host, port=args.port, proxy_headers=True,
                    forwarded_allow_ips="*", workers=1)
        return 0

    cfg = get_config()
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
