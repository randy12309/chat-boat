import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin import router as admin_router
from app.agents import faq, flow_store, trace
from app.auth import admin_usernames, router as auth_router
from app.documents import router as documents_router
from app.home import router as home_router
from app.chat import router as chat_router
from app.payments import router as payments_router
from mcp_server import db

logger = logging.getLogger(__name__)


def _prepare_mysql() -> None:
    """Create any missing tables, record the configured admins and drop flows past the retention period."""
    try:
        db.ensure_schema()
        db.sync_admins(sorted(admin_usernames()))
        flow_store.purge()
    except Exception:
        logger.warning("Could not prepare the MySQL tables", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if db.enabled():
        await asyncio.to_thread(_prepare_mysql)
    # Prepare the database, load the embedding model and warm the cache in the background so the first chat isn't slow.
    if faq.enabled():
        asyncio.create_task(asyncio.to_thread(faq.get_service().warm_up))
    yield
    await asyncio.to_thread(trace.wait_pending)


app = FastAPI(title="Plan Assistant API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(home_router)
app.include_router(chat_router)
app.include_router(payments_router)
app.include_router(documents_router)
app.include_router(admin_router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}
