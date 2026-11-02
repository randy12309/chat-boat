"""MySQL access (users, payments, chat history). Enabled when MYSQL_DATABASE is set."""
import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


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
    with connection() as conn, conn.cursor() as cur:
        for statement in SCHEMA_FILE.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                cur.execute(statement)


def drop_schema() -> None:
    with connection() as conn, conn.cursor() as cur:
        for table in ("conversation_memory", "messages", "conversations", "payments", "users"):
            cur.execute(f"DROP TABLE IF EXISTS {table}")


def get_premium(username: str) -> dict | None:
    """Latest unpaid payment for the user, else the latest paid one; None if the user is unknown."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        if user is None:
            return None
        cur.execute(
            "SELECT id, amount_minor, currency, billing_period, due_date, status FROM payments "
            "WHERE user_id = %s ORDER BY (status = 'Paid'), due_date DESC, id DESC LIMIT 1",
            (user["id"],),
        )
        row = cur.fetchone()
    if row is None:
        return {"amount": 0.0, "currency": "USD", "due_date": None, "status": "Paid"}
    return {
        "payment_id": row["id"],
        "amount": row["amount_minor"] / 100,
        "currency": row["currency"],
        "billing_period": row["billing_period"],
        "due_date": row["due_date"].isoformat(),
        "status": row["status"],
    }


def total_paid_minor(username: str) -> int | None:
    """Sum of the user's Paid payments in minor units; None if the user is unknown."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT u.id, COALESCE(SUM(p.amount_minor), 0) AS total FROM users u "
            "LEFT JOIN payments p ON p.user_id = u.id AND p.status = 'Paid' "
            "WHERE u.username = %s GROUP BY u.id",
            (username,),
        )
        row = cur.fetchone()
    return None if row is None else int(row["total"])


def mark_paid(username: str, idempotency_key: str) -> str | None:
    """Mark the user's oldest outstanding payment as paid (simulated). Returns its reference, or None."""
    reference = f"SIM-{secrets.token_hex(6).upper()}"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE payments SET status = 'Paid', paid_at = NOW(), idempotency_key = %s, "
            "provider_reference = %s WHERE status <> 'Paid' AND user_id = "
            "(SELECT id FROM users WHERE username = %s) ORDER BY due_date LIMIT 1",
            (idempotency_key[:64], reference, username),
        )
        return reference if cur.rowcount == 1 else None
