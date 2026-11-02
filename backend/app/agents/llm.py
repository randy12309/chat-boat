import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "15"))


def enabled() -> bool:
    return bool(os.getenv("GROQ_API_KEY")) and os.getenv("LLM_ENABLED", "1") != "0"


def get_model():
    """Groq chat model (key read from GROQ_API_KEY in .env)."""
    from langchain_groq import ChatGroq

    return ChatGroq(
        model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
        temperature=0.2,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=0,
    )


def clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
