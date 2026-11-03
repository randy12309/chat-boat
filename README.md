# Plan Assistant

Angular 22 frontend (`frontend-ng/`) + FastAPI backend.

## Prerequisites
- Python 3.10+ (tested on 3.12)
- Node.js 20.19+ / 22+ (tested on Node 26) for the Angular app

## Install
```powershell
cd backend
pip install -r requirements.txt
cd ..\frontend-ng
npm install
```

## Run
One command (starts MCP server, backend and frontend; Ctrl+C stops all):
```bash
./start.sh        # Git Bash / WSL / macOS / Linux
.\start.ps1       # PowerShell
```

Or manually, in separate terminals:

**Backend** (http://localhost:8000, API docs at /docs):
```powershell
cd backend
python -m uvicorn app.main:app --port 8000 --reload
```

**Frontend** (http://localhost:4200):
```powershell
cd frontend-ng
npm start
```
(The old AngularJS UI in `frontend/` is kept only for reference; `python serve.py` there serves it.)

Open http://localhost:4200 and sign in with `randy` / `password`, or click **Create an account**.

**Registration:** `POST /api/v1/auth/register` with `username` (3-30 letters/digits/underscore), `email`, `display_name`, `password` (8-72 chars). Passwords are stored as bcrypt hashes in the MySQL `users` table (or `backend/app/data/registered_users.json` when MySQL is off). Returns 201 and a token; 409 if the username or email is taken. New members start with an empty premium record and inbox.

Port 4200 is required: the backend only allows CORS from `localhost:4200`.

## Features
- Chat history sidebar (ChatGPT style): every chat is a saved session in MySQL (`conversations`, `messages`, `conversation_memory`). The left sidebar lists your chats grouped by Today / Yesterday / Previous 7 days / Older, opens a chat with its full history, and lets you start a new chat, rename, or delete one. Chats are private to their owner, and the most recent chat reopens on login. On small screens the sidebar becomes a slide-out menu. Without MySQL configured, a local SQLite file is used instead.
- Benefit eligibility: the quarterly premium (700) and the eligibility threshold (28,000, i.e. 40 quarters) are rows in the PostgreSQL `plan_settings` table, not code constants. A member whose total `Paid` premiums reach the threshold is eligible for insurance and pension. `get_eligibility` computes it from the MySQL `payments` table and those settings (see BR-01 to BR-04 in `devlopment_plan/requirements.md`).
- Streaming replies: `POST /api/v1/chat/stream` returns server-sent events (`{"delta": "..."}` while the Groq model writes, `{"reset": true}` to discard a partial answer, then `{"done": true, "reply": ..., "sources": ...}`). The Angular chat shows the text as it arrives; cache/FAQ and rule-based replies arrive as a single delta. `POST /api/v1/chat` still returns the full reply in one response.
- Login against `backend/app/data/users.json` (token-based, 1 hour expiry)
- Chat (landing page after login), ChatGPT-style: every question goes through a LangGraph agent. A deterministic `supervisor` handles confirm/cancel of a pending payment. Everything else first goes to a `faq` node: (1) an in-memory cache holding the most frequently asked public questions (ranked by `hit_count` in PostgreSQL, plus rephrasings asked at least `FAQ_PROMOTE_MIN_HITS` times, plus recently matched rephrasings with a TTL), then (2) an exact match in the `faq_entries` table, then (3) a pgvector cosine search over the embedded FAQ. Every answered question increments its `hit_count`, so the hot cache follows real usage after a restart. Policy rules are embedded in `policy_topics` and `get_policy_info` matches a topic by name or by meaning; answers and policies can use `{eligibility_threshold}`-style placeholders filled from `plan_settings`. Only public answers are cached; member-specific questions (balance, pay, ...) are indexed without an answer so they are never matched to a similar public answer and go to the `agent` node instead (Groq model via `langchain-groq`, key from `GROQ_API_KEY` in `.env`). The agent decides which MCP tools to call (`get_balance`, `explain_premium`, `get_eligibility`, `get_policy_info`, `start_payment`), loops through a `tools` node (max 3 rounds), then answers. The user identity is injected server-side (never a model argument); the model can only prepare a payment quote, and a deterministic `pay_prompt` asks for `confirm`, so money is never moved by the model. If the LLM is disabled (`LLM_ENABLED=0`) or fails, the agent falls back to keyword routing and templated replies. Run the MCP server with `python -m mcp_server.server` in `backend` when `MCP_SERVER_URL` is set.

## Configuration
- MySQL (users, payments, and chat history; used by the MCP tools and the chat API): set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` in `.env`, then run `python -m mcp_server.init_db` in `backend` to create and seed the `plan_assistant` database with fictional demo data (safe to re-run; `--reset` drops and rebuilds both tables; refused when `APP_ENV=production`). Schema: `backend/mcp_server/schema.sql`. Amounts are stored as integer minor units. Without `MYSQL_DATABASE` (or with `DB_ENABLED=0`) the tools fall back to `accounts.json`.
- PostgreSQL + pgvector (FAQ, policies, plan settings, question cache statistics): set `PG_EMBEDDED=1` to run a local PostgreSQL with pgvector from the `pgserver` package (data in `backend/app/data/pgdata`, no install needed), or point at your own server with `PG_DSN` or `PG_HOST`/`PG_PORT`/`PG_USER`/`PG_PASSWORD` (the server needs the `vector` extension). `PG_DATABASE` defaults to `plan_assistant`. Run `python -m mcp_server.init_pg` in `backend` to create the schema and load the seed data from `backend/app/data/faq.json`, `policies.json` and `plan_settings.json` (re-embeds only changed text, keeps hit counts and edited settings; `--reset` rebuilds the tables, `--overwrite-settings` restores seeded settings). The backend and MCP server also do this on startup. After the first load, edit the tables directly (or the seed files and re-run `init_pg`). Questions, policies and settings are not hardcoded: if the database is down the policy and eligibility tools return an error and FAQ lookups fall through to the agent. Schema: `backend/mcp_server/pg_schema.sql`. Learned rephrasings (`faq_learned`) store the user's wording of public questions.
- `JWT_SECRET` – token signing secret (set a real value outside local development)
- `TOKEN_TTL_SECONDS` – token lifetime, default 3600
- `FAQ_ENABLED` (default 1), `VECTOR_DB_ENABLED` (default 1), `VECTOR_MAX_DISTANCE` (default 0.45, lower = stricter), `POLICY_MAX_DISTANCE` (default 0.55), `FAQ_CACHE_TTL_SECONDS` (default 3600, for rephrasings matched through vector search), `FAQ_HOT_CACHE_SIZE` (default 100), `FAQ_PROMOTE_MIN_HITS` (default 3), `PG_ENABLED`. The first run downloads the `all-MiniLM-L6-v2` embedding model (~90 MB, via fastembed).
- API URL: `API_BASE` in `frontend-ng/src/app/core/api.ts`

## Layout
```
backend/app/   auth.py, users.py, home.py, chat.py, main.py, data/*.json
frontend-ng/   Angular 22 standalone app (src/app/core, src/app/pages)
frontend/      legacy AngularJS UI
```
