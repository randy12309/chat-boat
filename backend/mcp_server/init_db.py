"""Create the MySQL schema and seed it with fictional demo data.

Run: python -m mcp_server.init_db [--reset]   (--reset drops every app table first)

Seeds the BMOWNER tables: USER_ACCOUNT (login), users_profile (display name, standing in for MDM),
EPAYMENT_METHOD (saved cards and ACH) and EPAYMENT (payment history). Amounts follow the plan's
quarterly premium (BR-01, 700 USD), so eligibility against the 28,000 threshold (BR-02) is realistic.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from mcp_server import db  # noqa: E402  (needs .env loaded first)

DATA = Path(__file__).parent.parent / "app" / "data"

# Saved methods every seeded member gets: one card, one ACH. ENC_TOKEN is a gateway token, never a card number.
METHODS = (("Personal Visa", "CREDIT_CARD", "VISA"), ("Checking account", "ECHECK", None))

# Paid quarters of history per member, so eligibility differs across the demo accounts.
PAID_QUARTERS = {"randy": 6, "john": 12}


def _quarter_start(now: datetime, quarters_ago: int) -> datetime:
    """The first day of the quarter `quarters_ago` before `now`."""
    quarter = (now.month - 1) // 3 - quarters_ago
    return datetime(now.year + quarter // 4, quarter % 4 * 3 + 1, 1, 9, 30)


def seed(cur, username: str, password: str, display_name: str, amount: float) -> None:
    """One member: USER_ACCOUNT + users_profile, two saved methods, and an EPAYMENT history. Idempotent."""
    now = datetime.now().replace(microsecond=0)
    email = f"{username}@example.test"
    cur.execute(
        "INSERT IGNORE INTO user_account (type_code, status_code, user_name, email_id, mobile_number, "
        "enc_password, created_by, mobile_verified, email_verified, te_suid, status_updated_date) "
        "VALUES (1, 1, %s, %s, %s, %s, 'SEED', 'Y', 'Y', %s, %s)",
        (username, email, f"5550100{abs(hash(username)) % 1000:03d}", password, username, now),
    )
    cur.execute("SELECT account_id FROM user_account WHERE te_suid = %s", (username,))
    account_id = cur.fetchone()["account_id"]
    cur.execute(
        "INSERT INTO users_profile (te_suid, display_name, email) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE display_name = VALUES(display_name), email = VALUES(email)",
        (username, display_name, email),
    )
    cur.execute("SELECT COUNT(*) AS n FROM epayment_method WHERE user_account_id = %s", (account_id,))
    if cur.fetchone()["n"]:
        return  # already seeded; leave the member's data alone
    methods = {}
    for nickname, mode, card_type in METHODS:
        cur.execute(
            "INSERT INTO epayment_method (user_account_id, nickname, epay_mode, status, enc_token, card_type, "
            "enc_last_four, enc_exp_mm_yyyy, cardholder_name, created_date, tc_accepted_date) "
            "VALUES (%s, %s, %s, 'ACTIVE', %s, %s, 'DUMMY', 'DUMMY', %s, %s, %s)",
            (account_id, nickname, mode, f"TOK-{mode[:2]}-{username}", card_type, display_name, now, now),
        )
        methods[mode] = cur.lastrowid
    card, ach = methods["CREDIT_CARD"], methods["ECHECK"]

    def add(method: int, when: datetime, status: str, result: str | None, message: str, index: int, batch=None) -> None:
        is_card = method == card
        cur.execute(
            "INSERT INTO epayment (te_suid, src, amt, crcrd_cnf_nbr, crcrd_txn_id, epmt_timstmp, epmt_status, "
            "bat_nbr, gateway_suid, gateway_rank, result, echeck_cnf_nbr, internal_user_id, crcrd_type, "
            "response_message, epay_method_id) "
            "VALUES (%s, 'W', %s, %s, %s, %s, %s, %s, 1, 1, %s, %s, %s, %s, %s, %s)",
            (
                username, amount,
                f"CNF{username[:3].upper()}{index:04d}" if is_card and result == "A" else None,
                f"TXN{username[:3].upper()}{index:04d}" if is_card else None,
                when, status, batch, result,
                None if is_card else f"ECK{username[:3].upper()}{index:04d}",
                username, "V" if is_card else None, message, method,
            ),
        )

    # One approved, synced payment per past quarter (these are what count toward the threshold).
    paid_quarters = PAID_QUARTERS.get(username, 4)
    for quarter in range(paid_quarters, 0, -1):
        add(card, _quarter_start(now, quarter), "S", "A", "Approved", quarter)
    # A declined attempt on the card that was then retried and approved on the ACH account.
    add(card, now - timedelta(days=40), "F", "D", "Declined: insufficient funds", 900)
    if paid_quarters:
        add(ach, _quarter_start(now, 1) + timedelta(days=3), "S", "A", "Approved", 901)
    # This quarter's premium is staged and still owed (EPMT_STATUS 'P').
    add(ach, now - timedelta(days=4), "P", None, "Staged, awaiting backend sync", 902)


def seed_all() -> None:
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise SystemExit("Refusing to seed demo data when APP_ENV=production")
    users = json.loads((DATA / "users.json").read_text(encoding="utf-8"))
    accounts = json.loads((DATA / "accounts.json").read_text(encoding="utf-8"))
    quarterly = 700.00  # BR-01
    with db.connection() as conn, conn.cursor() as cur:
        for username, password in users.items():
            account = accounts.get(username, {})
            hashed = bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()
            seed(cur, username, hashed, account.get("display_name", username), quarterly)


if __name__ == "__main__":
    db.create_schema()
    if "--reset" in sys.argv:
        db.drop_schema()
        db.create_schema()
    seed_all()
    print("plan_assistant database ready")
