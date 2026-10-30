from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import router as auth_router
from app.home import router as home_router
from app.chat import router as chat_router

app = FastAPI(title="Plan Assistant API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(home_router)
app.include_router(chat_router)


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}
