"""Load the FAQ spreadsheet into Valkey: each question is embedded (the question only) and stored with its answer.

Run: python -m mcp_server.faq_excel [path]   The backend also does this at startup when Valkey is empty.
"""
import hashlib
import io
import json
import logging
import math
import os
import secrets
import sys
import tempfile
import zipfile
from pathlib import Path

import openpyxl
import valkey
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from mcp_server import cache, embeddings  # noqa: E402  (needs .env loaded first)
from mcp_server.cache_config import number
from mcp_server.vectordb import normalize  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "data" / "SAG_AFTRA_300_FAQ_Questions_updated.xlsx"
MARKER = "plan:faq:excel:loaded"
UPLOADED_FILE = Path(__file__).resolve().parents[1] / "app" / "data" / "uploaded_policy_faq.xlsx"


def excel_path() -> Path:
    return UPLOADED_FILE if UPLOADED_FILE.exists() else Path(os.getenv("FAQ_EXCEL_PATH") or DEFAULT_FILE)


def read_rows(path: Path, *, strict: bool = False) -> list[dict]:
    """Rows of the FAQ sheet, found by the 'Question No.' header so title lines above it are ignored."""
    sheet = openpyxl.load_workbook(path).active
    rows, in_table = [], False
    for line, values in enumerate(sheet.iter_rows(values_only=True), 1):
        number, topic, question, answer, answer_type, refs = (list(values) + [None] * 6)[:6]
        if not in_table:
            in_table = str(number or "").strip().lower().startswith("question no")
            continue
        if question and answer and str(number).strip().isdigit():
            rows.append({"no": int(number), "topic": topic, "question": str(question).strip(),
                         "answer": str(answer).strip(), "answer_type": answer_type, "policy_refs": refs})
        elif strict and any(value is not None and str(value).strip() for value in values):
            raise FaqUploadError(f"Row {line} needs a numeric question number, question and answer")
    return rows


class FaqUploadError(ValueError):
    """The supplied FAQ workbook cannot be imported."""


def replace_uploaded(data: bytes) -> int:
    """Validate and embed the workbook before replacing the shared public FAQ cache."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > number("FAQ_UPLOAD_MAX_EXPANDED_BYTES", 40 * 1024 * 1024):
                raise FaqUploadError("Workbook expands beyond the allowed size")
        rows = read_rows(io.BytesIO(data), strict=True)
    except FaqUploadError:
        raise
    except Exception as exc:
        raise FaqUploadError("Invalid XLSX workbook") from exc
    max_rows = number("FAQ_UPLOAD_MAX_ROWS", 2000)
    if not rows or len(rows) > max_rows:
        raise FaqUploadError(f"Workbook must contain 1 to {max_rows} FAQ rows with numbers, questions and answers")
    keys = [normalize(row["question"]) for row in rows]
    max_question = number("FAQ_MAX_QUERY_CHARS", 300)
    max_answer = number("FAQ_MAX_ANSWER_CHARS", 10000)
    if any(not key or len(row["question"]) > max_question or len(row["answer"]) > max_answer for key, row in zip(keys, rows)):
        raise FaqUploadError(f"Questions must be 1 to {max_question} characters and answers at most {max_answer} characters")
    if len(set(keys)) != len(keys) or len({row["no"] for row in rows}) != len(rows):
        raise FaqUploadError("Question numbers and normalized questions must be unique")
    client = cache._faq_client()
    if client is None:
        raise RuntimeError("Valkey is not configured")
    entries = []
    batch_size = number("FAQ_EMBED_BATCH_SIZE", 64)
    for start in range(0, len(rows), batch_size):
        chunk, chunk_keys = rows[start:start + batch_size], keys[start:start + batch_size]
        vectors = embeddings.embed(chunk_keys)
        if len(vectors) != len(chunk_keys) or any(not v or not any(v) or not all(math.isfinite(float(x)) for x in v) for v in vectors):
            raise RuntimeError("Could not create valid FAQ embeddings")
        for row, key, vector in zip(chunk, chunk_keys, vectors):
            entry = {"id": f"Q{row['no']:03d}", "answer": row["answer"], "source": f"faq:Q{row['no']:03d}",
                     "topic": str(row["topic"] or ""), "answer_type": str(row["answer_type"] or ""),
                     "policy_refs": str(row["policy_refs"] or "")}
            digest_key = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            entries.append((cache._faq_key(key), json.dumps(entry)))
            entries.append((f"{cache.SEMANTIC_PREFIX}{digest_key}", json.dumps({"q": key, "emb": cache.compact(vector), "answer": entry})))
    UPLOADED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=UPLOADED_FILE.parent, suffix=".xlsx", delete=False) as tmp:
        temp_path = Path(tmp.name)
        tmp.write(data)
    try:
        old_keys = list(client.scan_iter(match="plan:faq:*", count=500))
        if len(old_keys) > 10000:
            raise RuntimeError("FAQ cache is too large for a safe replacement")
        pipe = client.pipeline(transaction=True)
        if old_keys:
            pipe.delete(*old_keys)
        for key, value in entries:
            pipe.set(key, value)
        pipe.set(MARKER, hashlib.sha256(data).hexdigest())
        pipe.set("plan:faq:revision", secrets.token_hex(16))
        pipe.execute()
        os.replace(temp_path, UPLOADED_FILE)
    finally:
        temp_path.unlink(missing_ok=True)
    return len(rows)


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
    batch_size = number("FAQ_EMBED_BATCH_SIZE", 64)
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
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
