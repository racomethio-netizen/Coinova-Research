"""Administrative CLI: ``python -m nexus_api.cli <command>``.

create-owner   Create the single owner account (password from prompt or
               NEXUS_OWNER_PASSWORD for non-interactive provisioning).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from nexus_config import get_settings
from nexus_db import make_engine, make_session_factory, models, repo, transaction
from nexus_security.passwords import hash_password, validate_password_strength
from sqlalchemy import select


def create_owner(email: str) -> int:
    email = email.strip().lower()
    if "@" not in email:
        raise SystemExit("invalid email")
    password = os.environ.pop("NEXUS_OWNER_PASSWORD", None)
    if password is None:
        password = getpass.getpass("Owner password: ")
        if password != getpass.getpass("Repeat: "):
            raise SystemExit("passwords do not match")
    try:
        validate_password_strength(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    engine = make_engine(get_settings().database_url.get_secret_value(), pool_size=1)
    with transaction(make_session_factory(engine)) as s:
        if s.scalar(select(models.User.id).where(models.User.role == "owner")) is not None:
            raise SystemExit("an owner account already exists")
        user = models.User(email=email, password_hash=hash_password(password), role="owner")
        s.add(user)
        s.flush()
        repo.record_audit(s, actor="system:cli", action="owner_created", user_id=user.id,
                          target_type="user", target_id=str(user.id))
        uid = user.id
    engine.dispose()
    return uid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-owner")
    p.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "create-owner":
        uid = create_owner(args.email)
        print(f"owner created (id={uid})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
