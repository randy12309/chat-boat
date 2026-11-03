"""Create the PostgreSQL/pgvector schema and load policy, FAQ and plan-setting seed data (embedding the text).

Run: python -m mcp_server.init_pg [--reset] [--overwrite-settings]
  --reset               drop the pgvector tables first (this also clears FAQ hit counts)
  --overwrite-settings  reset plan_settings to the values in app/data/plan_settings.json
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from mcp_server import pgvec  # noqa: E402  (needs .env loaded first)


def main(argv: list[str]) -> None:
    if not pgvec.enabled():
        raise SystemExit("Set PG_DSN, PG_HOST or PG_EMBEDDED=1 in .env first")
    if "--reset" in argv:
        pgvec.create_schema()
        pgvec.drop_schema()
    pgvec.create_schema()
    counts = pgvec.seed(overwrite="--overwrite-settings" in argv)
    print(f"pgvector database ready: {counts['settings']} settings, {counts['policies']} policies, {counts['faq']} FAQ entries (re)loaded")


if __name__ == "__main__":
    main(sys.argv[1:])
