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
python serve.py
```
`serve.py` is a static server with browser caching disabled (plain `python -m http.server 4200` also works, but browsers like Chrome may keep showing an old cached version of the UI; if so, hard refresh with Ctrl+Shift+R).

Open http://localhost:4200 and sign in with `bharath` / `password` or `randy` / `password`.

Port 4200 is required: the backend only allows CORS from `localhost:4200`.

## Features
- Chat history sidebar (ChatGPT style): every chat is a saved session in MySQL (`conversations`, `messages`, `conversation_memory`). The left sidebar lists your chats grouped by Today / Yesterday / Previous 7 days / Older, opens a chat with its full history, and lets you start a new chat, rename, or delete one. Chats are private to their owner, and the most recent chat reopens on login. On small screens the sidebar becomes a slide-out menu. Without MySQL configured, a local SQLite file is used instead.
- Benefit eligibility: the quarterly premium is 700 and a member whose total `Paid` premiums reach 28,000 (40 quarters) is eligible for insurance and pension. `get_eligibility` computes it from the `payments` table (see BR-01 to BR-04 in `devlopment_plan/requirements.md`).
- Login against `backend/app/data/users.json` (token-based, 1 hour expiry)
- Chat (landing page after login), ChatGPT-style: every question goes through a LangGraph agent. A deterministic `supervisor` handles confirm/cancel of a pending payment. Everything else first goes to a `faq` node: (1) an in-memory cache seeded with the 50 example questions from `devlopment_plan/requirements.md` (`backend/app/data/faq.json`, exact match after normalisation), then (2) a local Chroma vector DB (`backend/app/data/chroma`, semantic match) whose hits are written back into the cache. Only public answers are cached; member-specific questions (balance, pay, ...) are indexed without an answer so they are never matched to a similar public answer and go to the `agent` node instead (Groq model via `langchain-groq`, key from `GROQ_API_KEY` in `.env`). The agent decides which MCP tools to call (`get_balance`, `explain_premium`, `get_eligibility`, `get_policy_info`, `start_payment`), loops through a `tools` node (max 3 rounds), then answers. The user identity is injected server-side (never a model argument); the model can only prepare a payment quote, and a deterministic `pay_prompt` asks for `confirm`, so money is never moved by the model. If the LLM is disabled (`LLM_ENABLED=0`) or fails, the agent falls back to keyword routing and templated replies. Run the MCP server with `python -m mcp_server.server` in `backend` when `MCP_SERVER_URL` is set.

## Configuration
- MySQL (users, payments, and chat history; used by the MCP tools and the chat API): set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` in `.env`, then run `python -m mcp_server.init_db` in `backend` to create and seed the `plan_assistant` database with fictional demo data (safe to re-run; `--reset` drops and rebuilds both tables; refused when `APP_ENV=production`). Schema: `backend/mcp_server/schema.sql`. Amounts are stored as integer minor units. Without `MYSQL_DATABASE` (or with `DB_ENABLED=0`) the tools fall back to `accounts.json`.
- `JWT_SECRET` – token signing secret (set a real value outside local development)
- `TOKEN_TTL_SECONDS` – token lifetime, default 3600
- `FAQ_ENABLED` (default 1), `VECTOR_DB_ENABLED` (default 1), `VECTOR_DB_PATH`, `VECTOR_MAX_DISTANCE` (default 0.45, lower = stricter), `FAQ_CACHE_TTL_SECONDS` (default 3600, for vector hits written to the cache). The first run downloads Chroma's default embedding model (~80 MB).
- API URL: `API_BASE` constant in `frontend/js/app.js`

## Layout
```
backend/app/   auth.py, home.py, chat.py, main.py, data/*.json
frontend/      index.html, *.html views, styles.css, js/
```
