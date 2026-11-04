"""Create the MySQL schema and seed it with fictional demo data.

Run: python -m mcp_server.init_db [--reset]   (--reset drops users and payments first)
"""
import json
import os
import sys
import uuid
from pathlib import Path

import bcrypt
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from mcp_server import db  # noqa: E402  (needs .env loaded first)

DATA = Path(__file__).parent.parent / "app" / "data"
NAMESPACE = uuid.UUID("6f1f2c0e-3b7a-4f55-9a53-0d5f6e1c2a10")  # fixed so seeding is deterministic


def seed() -> None:
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise SystemExit("Refusing to seed demo data when APP_ENV=production")
    users = json.loads((DATA / "users.json").read_text(encoding="utf-8"))
    accounts = json.loads((DATA / "accounts.json").read_text(encoding="utf-8"))
    with db.connection() as conn, conn.cursor() as cur:
        for username, password in users.items():
            user_id = str(uuid.uuid5(NAMESPACE, username))
            display = accounts.get(username, {}).get("display_name", username)
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            cur.execute(
                "INSERT INTO users (id, username, email, display_name, password_hash, is_demo) "
                "VALUES (%s, %s, %s, %s, %s, 1) ON DUPLICATE KEY UPDATE display_name = VALUES(display_name)",
                (user_id, username, f"{username}@example.test", display, hashed),
            )
            premium = accounts.get(username, {}).get("premium")
            if not premium:
                continue
            period = premium.get("billing_period") or premium["due_date"][:7]  # e.g. 2026-11 or a quarter, 2026-Q4
            cur.execute(
                "INSERT IGNORE INTO payments "
                "(id, user_id, amount_minor, currency, billing_period, due_date, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    str(uuid.uuid5(NAMESPACE, f"{username}:{period}")),
                    user_id,
                    round(premium["amount"] * 100),
                    premium["currency"],
                    period,
                    premium["due_date"],
                    premium["status"],
                ),
            )


if __name__ == "__main__":
    db.create_schema()
    if "--reset" in sys.argv:
        db.drop_schema()
        db.create_schema()
    seed()
    print("plan_assistant database ready")
