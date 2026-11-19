"""MySQL access: the BMOWNER participant tables (user_account, epayment, epayment_method), chat history and agent flow.

Enabled when MYSQL_DATABASE is set. A member is identified everywhere by their login name, which is
USER_ACCOUNT.USER_NAME and equals TE_SUID. Payments are EPAYMENT rows: EPMT_STATUS 'P' is staged and
still owed, 'S' is synced to the backend, 'F' is failed. There is no due date or currency column in the
legacy schema, so the billing period is derived from EPMT_TIMSTMP and amounts are the plan currency.
"""
import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

# EPAYMENT stores no currency: plan amounts are in the plan's currency (see plan_settings, BR-01).
CURRENCY = "USD"


def enabled() -> bool:
    return bool(os.getenv("MYSQL_DATABASE")) and os.getenv("DB_ENABLED", "1") != "0"


def _connect(database: str | None):
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database,
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def connection():
    conn = _connect(os.getenv("MYSQL_DATABASE"))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_schema() -> None:
    name = os.getenv("MYSQL_DATABASE", "")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name):
        raise ValueError("MYSQL_DATABASE must be set to a simple identifier")
    conn = _connect(None)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        conn.commit()
    finally:
        conn.close()
    ensure_schema()


def _statements(sql: str) -> list[str]:
    """Split schema.sql into executable statements.

    Own-line `--` comments are dropped first, because a comment may itself contain a ';' (which would
    otherwise split a statement in half), and a ';' inside a quoted string is not a separator.
    """
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    statements, current, quoted = [], [], False
    for char in body:
        if char == "'":
            quoted = not quoted
        if char == ";" and not quoted:
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
    statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()]


def ensure_schema() -> None:
    """Create any missing tables in the existing database (idempotent), so new tables appear without re-running init_db."""
    with connection() as conn, conn.cursor() as cur:
        for statement in _statements(SCHEMA_FILE.read_text(encoding="utf-8")):
            cur.execute(statement)
        # Lightweight forward migration for installations created before retrieval backend tracing.
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() "
            "AND table_name = 'agent_events' AND column_name = 'storage_backend'"
        )
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE agent_events ADD COLUMN storage_backend VARCHAR(20) NULL AFTER status")
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() "
            "AND table_name = 'agent_turns' AND column_name = 'flow'"
        )
        if cur.fetchone() is None:
            cur.execute("ALTER TABLE agent_turns ADD COLUMN flow JSON NULL AFTER question")


def drop_schema() -> None:
    """Drop every table the app owns, children before parents. The BMOWNER tables are rebuilt by init_db."""
    with connection() as conn, conn.cursor() as cur:
        for table in (
            "agent_events", "agent_turns", "admins", "conversation_memory", "messages", "conversations",
            "epayment", "epayment_method", "users_profile", "user_account",
        ):
            cur.execute(f"DROP TABLE IF EXISTS {table}")


def _iso(value):
    return value.isoformat(sep=" ") if value is not None else None


# --- user_account / users_profile --------------------------------------------------------------------------------

def _account_id(cur, username: str) -> int | None:
    """ACCOUNT_ID for a login name, or None. Also matches TE_SUID, which equals USER_NAME for participants."""
    cur.execute(
        "SELECT account_id FROM user_account WHERE user_name = %s OR te_suid = %s LIMIT 1", (username, username)
    )
    row = cur.fetchone()
    return row["account_id"] if row else None


def get_user_account(username: str) -> dict | None:
    """Legacy USER_ACCOUNT row for a login name. Password hash and OTP are never returned."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT account_id, type_code, status_code, user_name, email_id, mobile_number, invalid_password_count, "
            "invalid_otp_count, mobile_verified, email_verified, te_suid, created_date, updated_date, "
            "status_updated_date FROM user_account WHERE user_name = %s OR te_suid = %s LIMIT 1",
            (username, username),
        )
        row = cur.fetchone()
    if row is None:
        return None
    for key in ("created_date", "updated_date", "status_updated_date"):
        row[key] = _iso(row[key])
    return row


def get_profile(username: str) -> dict | None:
    """Display profile joined with the login fields: {username, te_suid, email, display_name, status_code}."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ua.user_name AS username, ua.te_suid, ua.status_code, "
            "COALESCE(p.email, ua.email_id) AS email, p.display_name "
            "FROM user_account ua LEFT JOIN users_profile p ON p.te_suid = ua.te_suid "
            "WHERE ua.user_name = %s OR ua.te_suid = %s LIMIT 1",
            (username, username),
        )
        return cur.fetchone()


def get_user(username: str) -> dict | None:
    """Login record for a member: {username, email, display_name, password_hash}. None if unknown.

    password_hash is USER_ACCOUNT.ENC_PASSWORD, so authentication reads the legacy table.
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ua.user_name AS username, ua.te_suid, ua.status_code, "
            "COALESCE(p.email, ua.email_id) AS email, p.display_name, ua.enc_password AS password_hash "
            "FROM user_account ua LEFT JOIN users_profile p ON p.te_suid = ua.te_suid "
            "WHERE ua.user_name = %s OR ua.te_suid = %s LIMIT 1",
            (username, username),
        )
        return cur.fetchone()


def create_user(username: str, email: str, display_name: str, password_hash: str) -> bool:
    """Register a participant: one USER_ACCOUNT row plus its display profile. False if the name or email is taken.

    EMAIL_ID is VARCHAR2(64) in the legacy table, so the full address is kept in users_profile and only a
    short enough address is mirrored into USER_ACCOUNT.
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM user_account WHERE user_name = %s OR te_suid = %s LIMIT 1", (username, username)
            )
            if cur.fetchone() is not None:
                return False
            cur.execute("SELECT 1 FROM users_profile WHERE email = %s LIMIT 1", (email,))
            if cur.fetchone() is not None:
                return False
            cur.execute(
                "INSERT INTO user_account (type_code, status_code, user_name, email_id, enc_password, created_by, "
                "created_date, mobile_verified, email_verified, te_suid, status_updated_date) "
                "VALUES (1, 1, %s, %s, %s, 'SELF', NOW(), 'N', 'N', %s, NOW())",
                (username, email if len(email) <= 64 else None, password_hash, username),
            )
            cur.execute(
                "INSERT INTO users_profile (te_suid, display_name, email) VALUES (%s, %s, %s)",
                (username, display_name, email),
            )
    except pymysql.err.IntegrityError:
        return False
    return True


# --- admins -----------------------------------------------------------------------------------------------------

def is_admin(username: str) -> bool:
    with connection() as conn, conn.cursor() as cur:
        account_id = _account_id(cur, username)
        if account_id is None:
            return False
        cur.execute("SELECT 1 FROM admins WHERE account_id = %s LIMIT 1", (account_id,))
        return cur.fetchone() is not None


def sync_admins(usernames: list[str]) -> int:
    """Record the configured admin usernames (those that exist as participants) in admins. Returns rows added."""
    if not usernames:
        return 0
    marks = ", ".join(["%s"] * len(usernames))
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT IGNORE INTO admins (account_id, granted_by) SELECT account_id, 'ADMIN_USERS' "
            f"FROM user_account WHERE user_name IN ({marks})",
            usernames,
        )
        return cur.rowcount


# --- epayment_method ----------------------------------------------------------------------------------------------

def list_payment_methods(username: str) -> list[dict] | None:
    """Saved payment methods for a login name (tokens and encrypted fields excluded); None if no such account."""
    with connection() as conn, conn.cursor() as cur:
        account_id = _account_id(cur, username)
        if account_id is None:
            return None
        cur.execute(
            "SELECT epay_method_id, nickname, epay_mode, status, card_type, cardholder_name, created_date, "
            "updated_date, last_used_date, tc_accepted_date FROM epayment_method WHERE user_account_id = %s "
            "ORDER BY epay_method_id",
            (account_id,),
        )
        rows = cur.fetchall()
    for row in rows:
        for key in ("created_date", "updated_date", "last_used_date", "tc_accepted_date"):
            row[key] = _iso(row[key])
    return rows


# --- epayment -----------------------------------------------------------------------------------------------------

# One payment with the saved method it used. Every payment query joins through this shape.
_PAYMENT_SELECT = (
    "SELECT e.epmt_id, e.te_suid, e.amt, e.epmt_timstmp, e.due_date, e.epmt_status, e.result, e.bat_nbr, e.src, "
    "e.crcrd_cnf_nbr, e.echeck_cnf_nbr, e.crcrd_type, e.response_message, e.epay_method_id, "
    "m.nickname AS method_nickname, m.epay_mode AS method_mode "
    "FROM epayment e JOIN user_account ua ON ua.te_suid = e.te_suid "
    "LEFT JOIN epayment_method m ON m.epay_method_id = e.epay_method_id "
)


def _quarter(when: datetime | None) -> str | None:
    """The billing period an EPAYMENT belongs to, from its timestamp: e.g. 2026-Q4. The schema has no period column."""
    return None if when is None else f"{when.year}-Q{(when.month - 1) // 3 + 1}"


def _status(row: dict) -> str:
    """EPMT_STATUS + RESULT as one word: P is pending, F or a declined card is failed, S is paid."""
    if row["epmt_status"] == "P":
        return "Pending"
    if row["epmt_status"] == "F" or row["result"] == "D":
        return "Failed"
    return "Paid"


def _payment(row: dict) -> dict:
    """One EPAYMENT row in the shape the app and the frontend use."""
    when = row["epmt_timstmp"]
    due = row.get("due_date")
    return {
        "payment_id": str(row["epmt_id"]),
        "amount": float(row["amt"]),
        "currency": CURRENCY,
        "billing_period": _quarter(when),
        "due_date": due.isoformat() if due else None,
        "status": _status(row),
        "result": row["result"],
        "method": row["method_nickname"] or row["method_mode"],
        "method_id": row["epay_method_id"],
        "reference": row["crcrd_cnf_nbr"] or row["echeck_cnf_nbr"],
        "message": row["response_message"],
        "paid_at": _iso(when),
    }


def _nothing_owed() -> dict:
    """A known member with no outstanding premium. Keeps 'no account' (None) distinct from 'nothing due'."""
    return {
        "payment_id": None, "amount": 0.0, "currency": CURRENCY, "billing_period": None, "due_date": None,
        "status": "Paid", "result": None, "method": None, "method_id": None, "reference": None,
        "message": None, "paid_at": None,
    }


def list_due(username: str) -> list[dict]:
    """The member's outstanding premiums: EPAYMENT rows still staged (EPMT_STATUS 'P'), oldest first."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            _PAYMENT_SELECT + "WHERE ua.user_name = %s AND e.epmt_status = 'P' ORDER BY e.epmt_timstmp, e.epmt_id",
            (username,),
        )
        rows = cur.fetchall()
    return [_payment(r) for r in rows]


def latest_payment(username: str) -> dict | None:
    """The member's most recent payment whatever its status; a zeroed record if they have none, None if unknown."""
    with connection() as conn, conn.cursor() as cur:
        if _account_id(cur, username) is None:
            return None
        cur.execute(
            _PAYMENT_SELECT + "WHERE ua.user_name = %s ORDER BY e.epmt_timstmp DESC, e.epmt_id DESC LIMIT 1",
            (username,),
        )
        row = cur.fetchone()
    return _payment(row) if row else _nothing_owed()


def get_premium(username: str) -> dict | None:
    """The member's premium standing: what they still owe (oldest staged payment), else their last payment.

    None means the member is unknown, which the tools report as not_found.
    """
    due = list_due(username)
    return due[0] if due else latest_payment(username)


def total_paid(username: str) -> float | None:
    """Sum of premiums actually paid: synced and approved EPAYMENT rows only (BR-03). None if the member is unknown."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(e.amt), 0) AS total FROM user_account ua "
            "LEFT JOIN epayment e ON e.te_suid = ua.te_suid AND e.epmt_status = 'S' AND e.result = 'A' "
            "WHERE ua.user_name = %s GROUP BY ua.account_id",
            (username,),
        )
        row = cur.fetchone()
    return None if row is None else float(row["total"])


def list_epayments(username: str, status: str | None = None, limit: int = 20) -> list[dict] | None:
    """Staged payments for a login name, newest first, optionally filtered by EPMT_STATUS; None if no such account."""
    with connection() as conn, conn.cursor() as cur:
        if _account_id(cur, username) is None:
            return None
        sql = (
            "SELECT e.epmt_id, e.te_suid, e.src, e.amt, e.crcrd_cnf_nbr, e.crcrd_txn_id, e.epmt_timstmp, "
            "e.epmt_status, e.bat_nbr, e.result, e.echeck_cnf_nbr, e.crcrd_type, e.response_message, e.epay_method_id "
            "FROM epayment e JOIN user_account ua ON ua.te_suid = e.te_suid WHERE ua.user_name = %s"
        )
        params: list = [username]
        if status:
            sql += " AND e.epmt_status = %s"
            params.append(status)
        cur.execute(sql + " ORDER BY e.epmt_timstmp DESC, e.epmt_id DESC LIMIT %s", (*params, max(1, min(limit, 100))))
        rows = cur.fetchall()
    for row in rows:
        row["amt"] = float(row["amt"])
        row["epmt_timstmp"] = _iso(row["epmt_timstmp"])
    return rows


def mark_paid_id(username: str, payment_id: int | str, reference: str) -> str | None:
    """Confirm one of the member's own staged payments: synced ('S') and approved ('A'). Returns the reference.

    None when the payment is not theirs or is no longer pending, so a stale card cannot pay twice.
    The confirmation number lands in CRCRD_CNF_NBR for a card or ECHECK_CNF_NBR for an ACH debit.
    """
    batch = "B" + datetime.now().strftime("%Y%m%d")
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE epayment e JOIN user_account ua ON ua.te_suid = e.te_suid "
            "LEFT JOIN epayment_method m ON m.epay_method_id = e.epay_method_id "
            "SET e.epmt_status = 'S', e.result = 'A', e.response_message = 'Approved', "
            "e.bat_nbr = COALESCE(e.bat_nbr, %s), "
            "e.crcrd_cnf_nbr = IF(m.epay_mode = 'ECHECK', e.crcrd_cnf_nbr, %s), "
            "e.echeck_cnf_nbr = IF(m.epay_mode = 'ECHECK', %s, e.echeck_cnf_nbr) "
            "WHERE e.epmt_id = %s AND e.epmt_status = 'P' AND ua.user_name = %s",
            (batch, reference, reference, payment_id, username),
        )
        return reference if cur.rowcount == 1 else None


def mark_paid(username: str, reference: str) -> str | None:
    """Confirm the member's oldest staged payment. Returns the reference, or None when nothing is pending."""
    due = list_due(username)
    return mark_paid_id(username, due[0]["payment_id"], reference) if due else None


def stage_payment(username: str, amount: float, epay_method_id: int | None = None, due_date: str | None = None) -> dict | None:
    """Stage a new premium payment for the member (EPMT_STATUS 'P'), as the web payment flow does.

    Used to raise a bill that is then owed. Returns the new payment, or None if the member is unknown.
    """
    with connection() as conn, conn.cursor() as cur:
        account_id = _account_id(cur, username)
        if account_id is None:
            return None
        cur.execute(
            "INSERT INTO epayment (te_suid, src, amt, epmt_timstmp, due_date, epmt_status, response_message, epay_method_id) "
            "VALUES (%s, 'W', %s, NOW(), %s, 'P', 'Staged, awaiting backend sync', %s)",
            (username, amount, due_date, epay_method_id),
        )
        new_id = cur.lastrowid
        cur.execute(_PAYMENT_SELECT + "WHERE e.epmt_id = %s", (new_id,))
        row = cur.fetchone()
    return _payment(row)


def payment_reference() -> str:
    """A simulated gateway confirmation number, as a real gateway would return."""
    return f"SIM-{secrets.token_hex(6).upper()}"


def conversation_owner(username: str) -> int | None:
    """The ACCOUNT_ID that chat history rows are scoped by."""
    with connection() as conn, conn.cursor() as cur:
        return _account_id(cur, username)


__all__ = [
    "CURRENCY", "connection", "create_schema", "ensure_schema", "drop_schema", "enabled",
    "get_user_account", "get_profile", "get_user", "create_user", "is_admin", "sync_admins",
    "list_payment_methods", "list_due", "latest_payment", "get_premium", "total_paid", "list_epayments",
    "mark_paid", "mark_paid_id", "stage_payment", "payment_reference", "conversation_owner",
]
