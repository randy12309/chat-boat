"""Admin endpoint that parses an uploaded PDF, DOCX or TXT file, embeds its text and inserts it into the vector database."""
import asyncio
import hashlib
import logging
import os
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth import admin_user
from mcp_server import documents, vectordb

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

MAX_CHUNKS = 2000


def _max_upload_bytes() -> int:
    return int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    data = bytearray()
    while chunk := await file.read(64 * 1024):
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"File is larger than {limit} bytes")
    return bytes(data)


def _process(filename: str, data: bytes) -> dict:
    text = documents.extract_text(filename, data)
    chunks = documents.chunk_text(text)
    if len(chunks) > MAX_CHUNKS:
        raise documents.DocumentError(f"The file produces more than {MAX_CHUNKS} chunks")
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    return {**vectordb.ingest_document(doc_id, filename, chunks), "filename": filename, "characters": len(text)}


@router.post("", status_code=201)
async def upload_document(file: UploadFile = File(...), _admin: str = Depends(admin_user)):
    if not vectordb.enabled():
        raise HTTPException(status_code=503, detail="The vector database is not configured")
    filename = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename((file.filename or "").replace("\\", "/")))[:200]
    if not filename.lower().endswith(documents.ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=415, detail="Only .pdf, .docx and .txt files are accepted")
    data = await _read_limited(file, _max_upload_bytes())
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty")
    try:
        return await asyncio.to_thread(_process, filename, data)
    except documents.DocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logger.exception("Document ingestion failed")
        raise HTTPException(status_code=503, detail="The vector database is unavailable")
