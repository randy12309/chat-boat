"""Create the Qdrant collections and load the plan settings and the policy PDF (embedding the text).

The FAQ spreadsheet is not stored here: it lives in Valkey (python -m mcp_server.faq_excel).

Run: python -m mcp_server.init_qdrant [--reset] [--overwrite-settings] [--pdf PATH]
  --reset               drop the collections first
  --overwrite-settings  reset plan_settings to the values in app/data/plan_settings.json
  --pdf PATH            ingest this policy reference PDF instead of app/data/SAG_AFTRA_RAG_Policy_Reference.pdf
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from mcp_server import vectordb  # noqa: E402  (needs .env loaded first)


def main(argv: list[str]) -> None:
    if not vectordb.enabled():
        raise SystemExit("Set QDRANT_URL (the qdrant/qdrant server) or QDRANT_PATH (embedded mode) in .env first")
    if "--pdf" in argv:
        os.environ["KNOWLEDGE_PDF"] = argv[argv.index("--pdf") + 1]
    vectordb.create_schema()
    if "--reset" in argv:
        vectordb.drop_schema()
        vectordb.create_schema()
    counts = vectordb.seed(overwrite="--overwrite-settings" in argv)
    print(f"Qdrant ready: {counts['settings']} settings, {counts['knowledge']} PDF policy records (re)loaded")


if __name__ == "__main__":
    main(sys.argv[1:])
