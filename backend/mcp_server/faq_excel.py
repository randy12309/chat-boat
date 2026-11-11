"""Load the FAQ spreadsheet into Valkey: each question is embedded (the question only) and stored with its answer.

Run: python -m mcp_server.faq_excel [path]   The backend also does this at startup when Valkey is empty.
"""
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

import openpyxl
import valkey
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from mcp_server import cache, embeddings  # noqa: E402  (needs .env loaded first)
from mcp_server.vectordb import normalize  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "data" / "SAG_AFTRA_300_FAQ_Questions_updated.xlsx"
MARKER = "plan:faq:excel:loaded"
BATCH = 64


def excel_path() -> Path:
    return Path(os.getenv("FAQ_EXCEL_PATH") or DEFAULT_FILE)


def read_rows(path: Path) -> list[dict]:
    """Rows of the FAQ sheet, found by the 'Question No.' header so title lines above it are ignored."""
    sheet = openpyxl.load_workbook(path).active
    rows, in_table = [], False
    for values in sheet.iter_rows(values_only=True):
        number, topic, question, answer, answer_type, refs = (list(values) + [None] * 6)[:6]
        if not in_table:
            in_table = str(number or "").strip().lower().startswith("question no")
            continue
        if question and answer and str(number).strip().isdigit():
            rows.append({"no": int(number), "topic": topic, "question": str(question).strip(),
                         "answer": str(answer).strip(), "answer_type": answer_type, "policy_refs": refs})
    return rows


def load(path: Path | None = None, *, force: bool = False) -> int:
    """Embed every question and store question + embedding + answer in Valkey. Returns entries written.

    Skips when the same file was already loaded (Valkey keeps a marker with the file's hash), unless force=True.
    Entries have no TTL, so they stay until Valkey restarts.
    """
    path = path or excel_path()
    client = cache._faq_client()
    if client is None or not path.exists():
        return 0
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not force and client.get(MARKER) == digest:
        return 0
    rows = read_rows(path)
    written = 0
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        keys = [normalize(r["question"]) for r in chunk]
        vectors = embeddings.embed(keys)  # the question only; the answer is never embedded
        pipe = client.pipeline()
        for row, key, vector in zip(chunk, keys, vectors):
            entry = {"id": f"Q{row['no']:03d}", "answer": row["answer"], "source": f"faq:Q{row['no']:03d}",
                     "topic": row["topic"], "answer_type": row["answer_type"], "policy_refs": row["policy_refs"]}
            digest_key = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            pipe.set(f"{cache.SEMANTIC_PREFIX}{digest_key}", json.dumps({"q": key, "emb": cache.compact(vector), "answer": entry}))
            pipe.set(cache._faq_key(key), json.dumps(entry))
            written += 1
        pipe.execute()
    client.set(MARKER, digest)
    logger.info("Loaded %d FAQ questions from %s into Valkey", written, path.name)
    return written


if __name__ == "__main__":
    try:
        count = load(Path(sys.argv[1]) if len(sys.argv) > 1 else None, force=True)
    except valkey.ValkeyError as exc:
        raise SystemExit(f"Valkey is unavailable: {exc}")
    print(f"{count} FAQ questions embedded and stored in Valkey" if count else "Nothing loaded (is VALKEY_URL set and the file present?)")
