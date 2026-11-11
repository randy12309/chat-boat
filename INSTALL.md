# Plan Assistant: installation and data migration

Commands are shown for Windows PowerShell first; macOS/Linux equivalents are noted where they differ.

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.12 | `python --version` |
| Node.js + npm | recent LTS or newer (tested on Node 26, Angular 22) | `node --version` |
| MySQL Server | 8.0 | `mysql --version` |
| Docker Desktop | recent (WSL 2 on Windows) | `docker --version` |
| Git | any | `git --version` |
| Groq API key | free tier works | https://console.groq.com |

Qdrant (the vector database) and Valkey (the optional cache) run as Docker containers from `compose.yaml`; start them with `docker compose up -d`.

For the optional Valkey cache, install Docker Desktop and enable WSL 2 on Windows. Run `wsl --install` from an administrator PowerShell and restart when prompted; install Docker Desktop, open it, and accept its terms. From the project root, run `docker compose up -d`, then `docker compose exec valkey valkey-cli ping` to verify it. This local-only container is unauthenticated and is not suitable for production.

## 2. Get the code

```powershell
git clone https://github.com/randyEq/PlanAssistant.git
cd PlanAssistant
git checkout feature/pgvector-faq-policy
```

## 3. Configure `.env`

Create `PlanAssistant\.env` (it is git-ignored; never commit it):

```ini
GROQ_API_KEY=your-groq-key
GROQ_MODEL=openai/gpt-oss-120b

MCP_SERVER_URL=http://127.0.0.1:8765/mcp
MCP_INTERNAL_SECRET=change-me-long-random-string
JWT_SECRET=change-me-long-random-string

# Optional local Valkey container
VALKEY_URL=redis://127.0.0.1:6379/0

MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-mysql-password
MYSQL_DATABASE=plan_assistant

# Qdrant vector database (docker compose up -d qdrant)
QDRANT_URL=http://127.0.0.1:6333
# QDRANT_API_KEY=...         # only if the server was started with an API key
# QDRANT_PATH=app/data/qdrant  # no Docker: embedded mode, ONE process only (set MCP_SERVER_URL empty too)
```

Generate secrets with `python -c "import secrets;print(secrets.token_urlsafe(48))"`.

## 4. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Create the databases and load the seed data (run from `backend`):

```powershell
docker compose up -d qdrant       # the qdrant/qdrant image on http://127.0.0.1:6333
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS plan_assistant CHARACTER SET utf8mb4"
python -m mcp_server.init_db      # MySQL tables + demo members (randy, john), their payment methods and e-payment history
python -m mcp_server.init_qdrant  # Qdrant collections + policy, FAQ, plan settings and the SAG-AFTRA PDF (embeds the text)
```

The first `init_qdrant` downloads the embedding model (`fastembed`), so it needs internet and takes a minute. The PDF `backend/app/data/SAG_AFTRA_RAG_Policy_Reference.pdf` is parsed into 27 policy and 300 question records; use `--pdf PATH` to load a newer edition.

Run the tests (they need no MySQL or Groq):

```powershell
python -m pytest -q
```

## 5. Frontend

```powershell
cd frontend-ng
npm install
npm start                 # ng serve on http://localhost:4200
```

Port **4200 is required**: the backend CORS allows only that origin (`backend/app/main.py`). Production build: `npm run build` (output in `frontend-ng/dist/`).

## 6. Start everything

One command from the repo root starts the MCP server (8765), backend (8000) and Angular (4200):

```powershell
.\start.ps1               # macOS/Linux: ./start.sh
```

Or manually, in three terminals:

```powershell
cd backend; python -m mcp_server.server                       # MCP tools  :8765
cd backend; python -m uvicorn app.main:app --port 8000        # API        :8000
cd frontend-ng; npm start                                      # UI         :4200
```

Open http://localhost:4200 and sign in with `randy` / `password` (also `john`).

**First start is slow:** the embedded Postgres can take 30 s or more before the first answer. If the MCP server says "Plan settings are unavailable", stop it, wait, and start it again.

**Demo trace:** the backend console prints one line per step of every question: who asked, which agent the supervisor chose, cache/Qdrant hits, each MCP tool called, and the answer. Set `TRACE_LOG=0` in `.env` to turn it off.

```text
22:24:50  #1  +0.00s  USER       randy asked: "What is the grace period and how much do I owe?"
22:24:52  #1  +1.89s  SUPERVISOR decided -> policy_agent AND account_agent in parallel
22:24:53  #1  +2.86s  ACCOUNT    LLM chose tools: get_balance
22:24:53  #1  +2.86s  MCP        call  get_balance (for the signed-in member)
22:24:54  #1  +3.99s  POLICY     Qdrant similarity match -> source=policy:grace period
```

## 7. Migrating the data to another machine or database

There are two stores to move. Take a backup of both before you start.

| Store | What it holds | Move it? |
|---|---|---|
| **MySQL** `plan_assistant` | user_account, users_profile, epayment_method, epayment, conversations, messages, conversation_memory, admins, agent_turns, agent_events | **Yes, this is the real member data** |
| **Qdrant** | `plan_settings`, `policy_topics`, `faq_entries`, `faq_learned`, `sag_aftra_knowledge` | Rebuildable from the JSON seed files and the PDF; migrate only to keep FAQ hit counts and learned phrasings |
| Files (dev fallback) | `backend/app/data/registered_users.json`, `sessions.db` | Only if MySQL was off when users registered |

### 7.1 MySQL: export from the old machine

```powershell
$mysqldump = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe"   # or just: mysqldump
& $mysqldump -u root -p --single-transaction --routines --triggers --default-character-set=utf8mb4 plan_assistant > plan_assistant.sql
```

macOS/Linux: `mysqldump -u root -p --single-transaction --routines --triggers plan_assistant > plan_assistant.sql`

### 7.2 MySQL: import on the new machine or server

```powershell
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS plan_assistant CHARACTER SET utf8mb4"
cmd /c "mysql -u root -p plan_assistant < plan_assistant.sql"      # PowerShell has no `<`, so use cmd
# macOS/Linux: mysql -u root -p plan_assistant < plan_assistant.sql
```

Remote server: add `-h <host> -P 3306 -u <user>`. Then set `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD` in the new `.env`.

Verify:

```powershell
mysql -u root -p plan_assistant -e "SELECT COUNT(*) accounts FROM user_account; SELECT COUNT(*) epayments FROM epayment; SELECT epmt_status, result, COUNT(*) FROM epayment GROUP BY epmt_status, result;"
```

> Do **not** run `init_db --reset` on a database holding real data; it drops `user_account`, `epayment` and every other table it owns. Plain `init_db` is safe: it only inserts missing demo rows. In production (`APP_ENV=production`) the seeder refuses to run.

### 7.3 Qdrant, option A: re-seed (simplest)

All policy and FAQ content lives in `backend/app/data` (JSON files and the PDF), so on the new Qdrant just run:

```powershell
cd backend
python -m mcp_server.init_qdrant
```

You lose only the FAQ `hit_count` values and learned phrasings; the cache re-learns from usage.

### 7.3 Qdrant, option B: copy the data (keeps hit counts)

Snapshot the collections on the old server and upload them to the new one (add an `api-key` header if the server has a key):

```powershell
foreach ($c in 'plan_settings','faq_entries','faq_learned') {
  $name = (Invoke-RestMethod -Method Post "http://oldhost:6333/collections/$c/snapshots").result.name
  Invoke-WebRequest "http://oldhost:6333/collections/$c/snapshots/$name" -OutFile "qdrant_$c.snapshot"
  curl.exe -X POST "http://newhost:6333/collections/$c/snapshots/upload?priority=snapshot" -F "snapshot=@qdrant_$c.snapshot"
}
cd backend; python -m mcp_server.init_qdrant     # loads the remaining collections
```

Then point the app at it in `.env` with `QDRANT_URL=http://newhost:6333`.

### 7.4 Moving the Qdrant data volume (copy, not snapshot)

Alternative for dev machines only: stop the container, then copy the `qdrant_storage` Docker volume. This only works between the same Qdrant version and architecture, and never while Qdrant is running.

### 7.5 Fallback files

If users registered while MySQL was off, copy `backend/app/data/registered_users.json` to the new machine, or re-register them. Chat history in `sessions.db` can be copied the same way.

## 8. After migrating: smoke test

1. `cd backend; python -m pytest -q` should pass.
2. Start the three servers, sign in as `randy`, and ask:
   - "What is the grace period?" (policy agent, Qdrant)
   - "How much do I owe?" (account agent, MySQL)
   - "I want to make a payment" (payment flow)
3. Ask the first question twice: the second answer should come from the cache.

## 9. Production checklist

- Set `APP_ENV=production`, a strong `JWT_SECRET` and `MCP_INTERNAL_SECRET`; load secrets from a secrets manager, not a file.
- Use managed MySQL and a secured Qdrant (`QDRANT_URL` with `QDRANT_API_KEY` over TLS, or Qdrant Cloud), not the embedded `QDRANT_PATH` mode.
- Change the allowed CORS origin in `backend/app/main.py` to your real site.
- Do not seed demo users. Serve the built Angular files from a CDN and the API behind a load balancer. See "Production architecture" in `docs/architecture.html`.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| "The assistant is temporarily unavailable" | Check `GROQ_API_KEY`, the free-tier rate limit, and the backend log for "Supervisor routing attempt failed" |
| "Plan settings are unavailable" / eligibility not fetching | MCP server not running or Qdrant not reachable; run `docker compose up -d qdrant`, check `QDRANT_URL`, restart `python -m mcp_server.server` |
| `Connection refused` on 6333 | The Qdrant container is not running; start Docker Desktop and `docker compose up -d qdrant` |
| `ERR_CONNECTION_REFUSED` on 4200 | Angular is not running; run `npm start` in `frontend-ng` |
| `only one usage of each socket address` | A previous MCP server is still on port 8765; stop it first |
| Browser shows CORS error | The UI must run on port 4200 |
