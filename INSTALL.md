# Plan Assistant: installation and data migration

Commands are shown for Windows PowerShell first; macOS/Linux equivalents are noted where they differ.

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.12 | `python --version` |
| Node.js + npm | recent LTS or newer (tested on Node 26, Angular 22) | `node --version` |
| MySQL Server | 8.0 | `mysql --version` |
| Git | any | `git --version` |
| Groq API key | free tier works | https://console.groq.com |

PostgreSQL is **not** required for development: the app starts an embedded PostgreSQL with pgvector (`pgserver`). For a separate server use PostgreSQL 15+ with the `vector` extension (section 6).

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

MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-mysql-password
MYSQL_DATABASE=plan_assistant

# pgvector: use ONE of the three options
PG_EMBEDDED=1                 # development: embedded Postgres, nothing to install
# PG_DSN=postgresql://user:pass@host:5432/plan_assistant
# PG_HOST=host  PG_PORT=5432  PG_USER=postgres  PG_PASSWORD=...  
PG_DATABASE=plan_assistant
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
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS plan_assistant CHARACTER SET utf8mb4"
python -m mcp_server.init_db      # MySQL tables + demo users (randy, bharath, john) and their premiums
python -m mcp_server.init_pg      # pgvector tables + policy, FAQ and plan settings (embeds the text)
```

The first `init_pg` downloads the embedding model (`fastembed`), so it needs internet and takes a minute.

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

Open http://localhost:4200 and sign in with `randy` / `password` (also `bharath`, `john`).

**First start is slow:** the embedded Postgres can take 30 s or more before the first answer. If the MCP server says "Plan settings are unavailable", stop it, wait, and start it again.

## 7. Migrating the data to another machine or database

There are two stores to move. Take a backup of both before you start.

| Store | What it holds | Move it? |
|---|---|---|
| **MySQL** `plan_assistant` | users, payments, conversations, messages, conversation_memory | **Yes, this is the real member data** |
| **PostgreSQL / pgvector** | `plan_settings`, `policy_topics`, `faq_entries`, `faq_learned` | Rebuildable from the JSON seed files; migrate only to keep FAQ hit counts and learned phrasings |
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
mysql -u root -p plan_assistant -e "SELECT COUNT(*) users FROM users; SELECT COUNT(*) payments FROM payments; SELECT status, COUNT(*) FROM payments GROUP BY status;"
```

> Do **not** run `init_db --reset` on a database holding real data; it drops users and payments. Plain `init_db` is safe: it only inserts missing demo rows. In production (`APP_ENV=production`) the seeder refuses to run.

### 7.3 pgvector, option A: re-seed (simplest)

All policy and FAQ content lives in `backend/app/data/*.json`, so on the new database just run:

```powershell
cd backend
python -m mcp_server.init_pg
```

You lose only the FAQ `hit_count` values and learned phrasings; the cache re-learns from usage.

### 7.3 pgvector, option B: copy the data (keeps hit counts)

The embedded server picks a random port each start, so ask Python for its connection string, then dump with the bundled tools:

```powershell
cd backend
$old = python -c "from dotenv import load_dotenv; load_dotenv('../.env'); from mcp_server import pgvec; print(pgvec.dsn())"
$bin = python -c "import pgserver,os;print(os.path.join(os.path.dirname(pgserver.__file__),'pginstall','bin'))"
& "$bin\pg_dump.exe" --dbname="$old" --format=custom --no-owner --file=plan_assistant_pg.dump
```

If you run a normal PostgreSQL instead, use `pg_dump --format=custom --no-owner -h <host> -U <user> plan_assistant -f plan_assistant_pg.dump`.

Restore into the target (the `vector` extension must be available there):

```powershell
psql "postgresql://user:pass@newhost:5432/postgres" -c "CREATE DATABASE plan_assistant"
psql "postgresql://user:pass@newhost:5432/plan_assistant" -c "CREATE EXTENSION IF NOT EXISTS vector"
pg_restore --no-owner --dbname="postgresql://user:pass@newhost:5432/plan_assistant" plan_assistant_pg.dump
```

Then point the app at it in `.env` (replace `PG_EMBEDDED=1`):

```ini
PG_DSN=postgresql://user:pass@newhost:5432/plan_assistant
```

Verify:

```powershell
psql "postgresql://user:pass@newhost:5432/plan_assistant" -c "SELECT (SELECT count(*) FROM policy_topics) policies, (SELECT count(*) FROM faq_entries) faq, (SELECT count(*) FROM plan_settings) settings"
```

Managed services need `vector` enabled: Azure Database for PostgreSQL (allow-list `VECTOR`), AWS RDS/Aurora and Google Cloud SQL (`CREATE EXTENSION vector`).

### 7.4 Moving the embedded Postgres folder (copy, not dump)

Alternative for dev machines only: stop every server, then copy `backend\app\data\pgdata\` to the same path on the new machine. This only works between the same OS and architecture, and never while Postgres is running.

### 7.5 Fallback files

If users registered while MySQL was off, copy `backend/app/data/registered_users.json` to the new machine, or re-register them. Chat history in `sessions.db` can be copied the same way.

## 8. After migrating: smoke test

1. `cd backend; python -m pytest -q` should pass.
2. Start the three servers, sign in as `randy`, and ask:
   - "What is the grace period?" (policy agent, pgvector)
   - "How much do I owe?" (account agent, MySQL)
   - "I want to make a payment" (payment flow)
3. Ask the first question twice: the second answer should come from the cache.

## 9. Production checklist

- Set `APP_ENV=production`, a strong `JWT_SECRET` and `MCP_INTERNAL_SECRET`; load secrets from a secrets manager, not a file.
- Use managed MySQL and PostgreSQL (`PG_DSN`), not the embedded database; turn embedded fsync back on if you ever keep it (`PG_EMBEDDED_FSYNC=1`).
- Change the allowed CORS origin in `backend/app/main.py` to your real site.
- Do not seed demo users. Serve the built Angular files from a CDN and the API behind a load balancer. See "Production architecture" in `docs/architecture.html`.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| "The assistant is temporarily unavailable" | Check `GROQ_API_KEY`, the free-tier rate limit, and the backend log for "Supervisor routing attempt failed" |
| "Plan settings are unavailable" / eligibility not fetching | MCP server not running or Postgres still starting; restart `python -m mcp_server.server` and wait about 30 s |
| `Timeout starting server` | Embedded Postgres recovery; the app now sets `fsync = off`, so retry. Kill stale `postgres`/`python` processes by PID if it persists |
| `ERR_CONNECTION_REFUSED` on 4200 | Angular is not running; run `npm start` in `frontend-ng` |
| `only one usage of each socket address` | A previous MCP server is still on port 8765; stop it first |
| Browser shows CORS error | The UI must run on port 4200 |
