# Plan Assistant

AngularJS frontend + FastAPI backend (dummy data, no database yet).

## Prerequisites
- Python 3.10+ (tested on 3.12)
- Internet access in the browser (AngularJS is loaded from the Google CDN)

## Install
```powershell
cd backend
pip install -r requirements.txt
```

## Run
Open two terminals.

**Backend** (http://localhost:8000, API docs at /docs):
```powershell
cd backend
python -m uvicorn app.main:app --port 8000 --reload
```

**Frontend** (http://localhost:4200):
```powershell
cd frontend
python -m http.server 4200
```

Open http://localhost:4200 and sign in with `bharath` / `password` or `randy` / `password`.

Port 4200 is required: the backend only allows CORS from `localhost:4200`.

## Features
- Login against `backend/app/data/users.json` (token-based, 1 hour expiry)
- Chat (landing page after login), ChatGPT-style: every question goes through a LangGraph agent. A deterministic `supervisor` handles confirm/cancel of a pending payment. Everything else first goes to a `faq` node: (1) an in-memory cache seeded with the 50 example questions from `devlopment_plan/requirements.md` (`backend/app/data/faq.json`, exact match after normalisation), then (2) a local Chroma vector DB (`backend/app/data/chroma`, semantic match) whose hits are written back into the cache. Only public answers are cached; member-specific questions (balance, pay, ...) are indexed without an answer so they are never matched to a similar public answer and go to the `agent` node instead (Groq model via `langchain-groq`, key from `GROQ_API_KEY` in `.env`). The agent decides which MCP tools to call (`get_balance`, `explain_premium`, `get_policy_info`, `start_payment`), loops through a `tools` node (max 3 rounds), then answers. The user identity is injected server-side (never a model argument); the model can only prepare a payment quote, and a deterministic `pay_prompt` asks for `confirm`, so money is never moved by the model. If the LLM is disabled (`LLM_ENABLED=0`) or fails, the agent falls back to keyword routing and templated replies. Run the MCP server with `python -m mcp_server.server` in `backend` when `MCP_SERVER_URL` is set.

## Configuration
- `JWT_SECRET` – token signing secret (set a real value outside local development)
- `TOKEN_TTL_SECONDS` – token lifetime, default 3600
- `FAQ_ENABLED` (default 1), `VECTOR_DB_ENABLED` (default 1), `VECTOR_DB_PATH`, `VECTOR_MAX_DISTANCE` (default 0.45, lower = stricter), `FAQ_CACHE_TTL_SECONDS` (default 3600, for vector hits written to the cache). The first run downloads Chroma's default embedding model (~80 MB).
- API URL: `API_BASE` constant in `frontend/js/app.js`

## Layout
```
backend/app/   auth.py, home.py, chat.py, main.py, data/*.json
frontend/      index.html, *.html views, styles.css, js/
```
