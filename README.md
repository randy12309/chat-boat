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

**Registration:** `POST /api/v1/auth/register` with `username` (3-30 letters/digits/underscore), `email`, `display_name`, `password` (8-72 chars). A member is a row in the MySQL `user_account` table (the legacy BMOWNER table: `USER_NAME`, `EMAIL_ID`, `ENC_PASSWORD`); the display name lives in the app-owned `users_profile` table, because `USER_ACCOUNT` has no name columns. Passwords are stored as bcrypt hashes in `ENC_PASSWORD` (or `backend/app/data/registered_users.json` when MySQL is off). Returns 201 and a token; 409 if the username or email is taken. New members start with an empty premium record and inbox.

Port 4200 is required: the backend only allows CORS from `localhost:4200`.

## Features
- Chat history sidebar (ChatGPT style): every chat is a saved session in MySQL (`conversations`, `messages`, `conversation_memory`). The left sidebar lists your chats grouped by Today / Yesterday / Previous 7 days / Older, opens a chat with its full history, and lets you start a new chat, rename, or delete one. Chats are private to their owner, and the most recent chat reopens on login. On small screens the sidebar becomes a slide-out menu. Without MySQL configured, a local SQLite file is used instead.
- Benefit eligibility: the quarterly premium (700) and the eligibility threshold (28,000, i.e. 40 quarters) are rows in the Qdrant `plan_settings` collection, not code constants. A member whose total `Paid` premiums reach the threshold is eligible for insurance and pension. `get_eligibility` computes it from the MySQL `epayment` table (`SUM(AMT)` over rows with `EPMT_STATUS='S'` and `RESULT='A'`) and those settings (see BR-01 to BR-04 in `devlopment_plan/requirements.md`).
- Streaming replies: `POST /api/v1/chat/stream` returns server-sent events (`{"delta": "..."}` while the Groq model writes, `{"reset": true}` to discard a partial answer, then `{"done": true, "reply": ..., "sources": ...}`). The Angular chat shows the text as it arrives; cache/FAQ and direct replies arrive as a single delta. `POST /api/v1/chat` still returns the full reply in one response.
- Payments from the database: a premium is owed while its `epayment` row is still staged (`EPMT_STATUS='P'`), because the legacy schema stores a payment only once it is submitted. The home "Action needed" card shows the oldest staged row and disappears once nothing is due. `EPAYMENT` has no due date and no billing period, so the period shown is the quarter of `EPMT_TIMSTMP` and the amount is `AMT` (USD). Typing a payment request ("I want to make payment") makes the assistant ask "Is that right? Yes/No"; Yes shows a "Pay Your Premiums" card with selectable due periods, an authorization checkbox and Pay Now. The card uses `GET /api/v1/payments/due` and `POST /api/v1/payments/pay` (`{payment_ids, authorized}`); only the caller's own unpaid items can be paid, and the charge is simulated (demo).
- Login against `backend/app/data/users.json` (token-based, 1 hour expiry)
- Multi-agent chat (landing page after login), ChatGPT-style: a LangGraph graph with an LLM `supervisor` that reads the message and hands it over with a forced `route_request` tool call (`SUPERVISOR_MODEL`, default the chat model): `policy`, `account`, `policy_and_account`, `payment`, or `direct` (the supervisor answers small talk itself). `policy_agent` is a RAG agent: it retrieves plan text from the in-memory cache (most frequent public questions by `hit_count` in Qdrant, promoted rephrasings, recent matches with a TTL), then a Qdrant vector search over the embedded FAQ, then the MCP `get_policy_info` tool (Qdrant `policy_topics`, then the `sag_aftra_knowledge` collection built from the SAG-AFTRA policy reference PDF, whose matching question is expanded with the policy records it cites and their source links), and a streamed Groq call answers from that text. `account_agent` is a Groq tool-calling loop over the read-only MCP tools (`get_balance`, `explain_premium`, `get_eligibility`, `list_payment_methods`, `list_epayments`), max 3 rounds. For mixed questions both run **in parallel** and a model-free `join` step puts the policy answer first. `payment_agent` calls MCP `get_due_payments` and asks "Is that right?"; money moves only through the authenticated pay card. Answers and policies can use `{eligibility_threshold}`-style placeholders filled from `plan_settings`; only public answers are cached. The user identity is injected server-side as a signed principal (never a model argument). There is no keyword routing: if the LLM is disabled (`LLM_ENABLED=0`) or the supervisor fails, the user gets a "temporarily unavailable" reply. The done event and `ChatResponse.agent` report which agent answered. Run the MCP server with `python -m mcp_server.server` in `backend` when `MCP_SERVER_URL` is set. See `docs/architecture.html` for diagrams.

## Configuration
- Local Valkey and Qdrant containers: install and start Docker Desktop (on Windows, WSL 2 must be enabled), then run `docker compose up -d` from the project root (`valkey` on 6379 for the cache, `qdrant/qdrant` on 6333 for vectors, both bound to localhost) and set `VALKEY_URL=redis://127.0.0.1:6379/0` in the root `.env`. Run `docker compose exec valkey valkey-cli ping` to check Valkey. Stop them with `docker compose down`. The development containers have no authentication; Valkey has no persistence and Qdrant keeps its data in the `qdrant_storage` volume. They are not a production deployment.
- Valkey account cache (optional): install the backend dependencies and set `VALKEY_URL` in the environment or root `.env` for both the backend and MCP service, for example `redis://localhost:6379/0`. Use `rediss://` with authentication and TLS for a secured deployment. The URL schemes and Lua `redis.call` API are part of Valkey's protocol; the application uses the official `valkey` Python client and a Valkey server. Balance and eligibility entries expire after 120 seconds and are invalidated after successful payments. If Valkey is unavailable, reads fall back to the source; a failed invalidation can leave a cached result stale until expiry. Leave `VALKEY_URL` unset to disable this cache. Shared caching requires an authoritative shared database; the JSON/demo payment state is process-local.
- MySQL (member accounts, e-payments, and chat history; used by the MCP tools and the chat API): set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` in `.env`, then run `python -m mcp_server.init_db` in `backend` to create and seed the `plan_assistant` database with fictional demo data (safe to re-run; `--reset` drops and rebuilds every table; refused when `APP_ENV=production`). Schema: `backend/mcp_server/schema.sql`. Member and payment rows use the real legacy BMOWNER tables — `user_account`, `epayment_method`, `epayment` — with the ERD's column names (`TE_SUID`, `EPMT_STATUS`, `EPMT_TIMSTMP`, `AMT`, `ENC_PASSWORD`, …) and its status codes (`EPMT_STATUS` P staged / S synced / F failed, `RESULT` A approved / D declined). Amounts are `DECIMAL(10,2)` in major units (USD). `users_profile` and the chat/admin tables are app-owned additions. Without `MYSQL_DATABASE` (or with `DB_ENABLED=0`) the tools fall back to `accounts.json`.
- Qdrant vector database (FAQ, policies, plan settings, question cache statistics, PDF knowledge base): run the `qdrant/qdrant` image with `docker compose up -d qdrant` and set `QDRANT_URL=http://127.0.0.1:6333` (plus `QDRANT_API_KEY` if the server has a key). Without Docker, `QDRANT_PATH=<folder>` or `:memory:` runs Qdrant inside the Python process, which allows only one process at a time (so the separate MCP server cannot share it). The backend and MCP server create the empty collections on startup; nothing is seeded. Policy text enters Qdrant only when an administrator uploads a policy document (agentic chunking with Gemini via `GEMINI_API_KEY`, paragraph chunking if unavailable, then embedding). Plan settings are not seeded either and must be added to the `plan_settings` collection separately.
- `JWT_SECRET` – token signing secret (set a real value outside local development)
- System admin console (`/admin` in the Angular app, linked as **Admin** in the chat header for administrators). Tabs: Admins, User account (no `ENC_PASSWORD`), User profiles, Payment methods, E-payments, Conversations, Conversation messages, Chat history (full transcript, each question linked to its agent flow), Embeddings (Qdrant collections with the stored text, metadata and the full vector), Agent flow and Tool calls. All data comes from read-only `GET /api/v1/admin/...` endpoints with pagination and search. An administrator is a username in `ADMIN_USERS` (comma-separated, empty by default so nobody) or a row in the MySQL `admins` table, which is filled from `ADMIN_USERS` at startup. Every admin request is written to the application log with the admin's username. The tables `admins`, `agent_turns` and `agent_events` are created automatically at startup (or by `init_db`); the admin console and flow history need MySQL.
- Account answer cache expiry can be configured in days with `ACCOUNT_ANSWER_TTL_DAYS` (for example `10`). When omitted, `ACCOUNT_ANSWER_TTL_SECONDS` applies, defaulting to 120 seconds. Invalidation on app payments still applies; external account changes can leave cached answers stale until expiry.
- `FAQ_LOG_EACH_SIMILARITY=1` prints each policy FAQ similarity and threshold, including diagnostic comparisons for exact hits. It can produce many log lines and extra work; leave it disabled (`0`, the default) when detailed diagnostics are not needed. A semantic lookup compares at most 2,000 cached vectors.
- Agent flow storage: every chat question is saved as one `agent_turns` row plus ordered `agent_events` rows, written in the background after the answer so chat is never delayed. Each step records who handed what to whom (`from_actor` to `to_actor`), the function called (`tool_name`: `route_request` for the supervisor's assignment, `get_policy_info`, `get_balance`, `explain_premium`, `get_eligibility`, `get_due_payments` for MCP), its status and duration. Tool calls are attributed to the agent that made them, including when `policy_agent` and `account_agent` run in parallel. Signed principals and quote IDs are never stored. Flows older than `AGENT_FLOW_RETENTION_DAYS` (default 90) are deleted at startup.
- Document upload: `POST /api/v1/documents` (multipart field `file`, bearer token) parses a `.pdf`, `.docx` or `.txt` file, splits it into chunks with agentic chunking, embeds them and stores them in the Qdrant `uploaded_documents` collection (HTTP 201 with `doc_id`, `chunks`, `status` of `inserted`, `replaced` or `unchanged`). Only usernames listed in `ADMIN_USERS` (comma-separated, empty by default so nobody) may upload; `MAX_UPLOAD_BYTES` (default 10 MB) caps the size. The document ID is a hash of the file content, so re-uploading the same file changes nothing. Chat questions are answered from the Valkey FAQ cache first (cosine similarity >= 0.9, single best match, `FAQ_SEMANTIC_MIN_SIMILARITY`), then by a LangChain `QdrantVectorStore` top-5 similarity search over these chunks (printed as `[RAG]` lines).
- `TOKEN_TTL_SECONDS` – token lifetime, default 3600
- `FAQ_ENABLED` (default 1), `VECTOR_DB_ENABLED` (default 1), `VECTOR_MAX_DISTANCE` (default 0.45, lower = stricter), `FAQ_CACHE_TTL_SECONDS` (default 3600, for rephrasings matched through vector search), `FAQ_HOT_CACHE_SIZE` (default 100), `FAQ_PROMOTE_MIN_HITS` (default 3), `KNOWLEDGE_MAX_DISTANCE` (default 0.5, PDF question match), `QDRANT_ENABLED`, `QDRANT_TIMEOUT_SECONDS` (default 10), `QDRANT_PREFER_GRPC`. The first run downloads the `all-MiniLM-L6-v2` embedding model (~90 MB, via fastembed).
- API URL: `API_BASE` in `frontend-ng/src/app/core/api.ts`

## Layout
```
backend/app/   auth.py, users.py, home.py, chat.py, main.py, data/*.json
frontend-ng/   Angular 22 standalone app (src/app/core, src/app/pages)
frontend/      legacy AngularJS UI
```
