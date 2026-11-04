# Database setup: the same data on another computer

Plan Assistant uses two databases. Both must hold the same data on the new computer.

| Database | Used for | Tables | Connection settings in `.env` |
|---|---|---|---|
| **MySQL 8** `plan_assistant` | Members and their activity | `users`, `payments`, `conversations`, `messages`, `conversation_memory` | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` |
| **PostgreSQL + pgvector** `plan_assistant` | Policy, FAQ, embeddings, settings | `plan_settings`, `policy_topics`, `faq_entries`, `faq_learned` | `PG_EMBEDDED=1` (development) **or** `PG_DSN` / `PG_HOST...` |

There are two ways to get "same database everywhere". Pick one.

- **Option A: one shared database server.** Every computer connects to the same host. No copying, and the data is always identical. Section 2.
- **Option B: copy the data to a local database on the new computer.** Each computer has its own copy. Section 3.

Commands are PowerShell. macOS/Linux differences are noted.

---

## 1. Install the database software on the new computer

**MySQL 8.0**
```powershell
winget install Oracle.MySQL          # or download MySQL Installer from https://dev.mysql.com/downloads/
mysql --version
```
macOS: `brew install mysql && brew services start mysql`. Ubuntu: `sudo apt install mysql-server`.

**PostgreSQL + pgvector**: nothing to install for development. `PG_EMBEDDED=1` starts a bundled PostgreSQL (`pgserver`) that is installed by `pip install -r backend/requirements.txt`. Install a real PostgreSQL 15+ with the `vector` extension only if you choose a shared server (section 2.2).

---

## 2. Option A: share one database server

### 2.1 MySQL on a shared host

On the host computer, create a dedicated user (do not use `root` remotely):

```sql
CREATE DATABASE IF NOT EXISTS plan_assistant CHARACTER SET utf8mb4;
CREATE USER 'plan_app'@'%' IDENTIFIED BY 'a-strong-password';
GRANT ALL PRIVILEGES ON plan_assistant.* TO 'plan_app'@'%';
FLUSH PRIVILEGES;
```

Allow remote connections: set `bind-address = 0.0.0.0` in `my.ini` (Windows: `C:\ProgramData\MySQL\MySQL Server 8.0\my.ini`) or `/etc/mysql/mysql.conf.d/mysqld.cnf`, restart MySQL, and open TCP **3306** in the host firewall for the other computers only.

```powershell
# Windows host: allow 3306 from one office subnet
New-NetFirewallRule -DisplayName "MySQL 3306" -Direction Inbound -Protocol TCP -LocalPort 3306 -RemoteAddress 192.168.1.0/24 -Action Allow
```

On every other computer, put the same values in `.env`:

```ini
MYSQL_HOST=192.168.1.20        # the host computer's IP or DNS name
MYSQL_PORT=3306
MYSQL_USER=plan_app
MYSQL_PASSWORD=a-strong-password
MYSQL_DATABASE=plan_assistant
```

Test from the new computer: `mysql -h 192.168.1.20 -u plan_app -p plan_assistant -e "SELECT COUNT(*) FROM users;"`

### 2.2 PostgreSQL + pgvector on a shared host

Run a PostgreSQL that has pgvector. Docker is the quickest:

```powershell
docker run -d --name plan-pg -p 5432:5432 -e POSTGRES_PASSWORD=a-strong-password -v plan_pg:/var/lib/postgresql/data pgvector/pgvector:pg16
docker exec plan-pg psql -U postgres -c "CREATE DATABASE plan_assistant"
docker exec plan-pg psql -U postgres -d plan_assistant -c "CREATE EXTENSION IF NOT EXISTS vector"
```

Then in every computer's `.env`, **remove `PG_EMBEDDED=1`** and set:

```ini
PG_DSN=postgresql://postgres:a-strong-password@192.168.1.20:5432/plan_assistant
```

Load the schema and content once (from any one computer):

```powershell
cd backend
python -m mcp_server.init_pg
```

Managed services work the same way (Azure Database for PostgreSQL, AWS RDS, Google Cloud SQL): enable the `vector` extension and use their connection string as `PG_DSN`.

---

## 3. Option B: copy the data to the new computer

Use this when the new computer runs its own databases. Do the **export** on the old computer and the **import** on the new one. Copy the two files between them (USB, network share, `scp`).

### 3.1 Export on the old computer

```powershell
cd E:\equester-project\PlanAssistant
New-Item -ItemType Directory -Force dbbackup | Out-Null

# --- MySQL: schema + data in one file ---
$env:MYSQL_PWD = "your-mysql-password"        # avoids the password prompt
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe" -u root --single-transaction --routines --triggers `
  --default-character-set=utf8mb4 --databases plan_assistant --result-file=dbbackup\mysql_plan_assistant.sql
Remove-Item Env:MYSQL_PWD

# --- PostgreSQL / pgvector (embedded server) ---
cd backend
$dsn = python -c "from dotenv import load_dotenv; load_dotenv('../.env'); from mcp_server import pgvec; pgvec.ensure_ready(); print(pgvec.dsn())"
$bin = python -c "import pgserver,os;print(os.path.join(os.path.dirname(pgserver.__file__),'pginstall','bin'))"
& "$bin\pg_dump.exe" --dbname="$dsn" --format=custom --no-owner --file=..\dbbackup\pg_plan_assistant.dump
cd ..
Get-ChildItem dbbackup
```

macOS/Linux MySQL: `mysqldump -u root -p --single-transaction --routines --triggers --databases plan_assistant > dbbackup/mysql_plan_assistant.sql`

If the old computer uses a normal PostgreSQL: `pg_dump --format=custom --no-owner -h HOST -U USER plan_assistant -f dbbackup/pg_plan_assistant.dump`

> The `dbbackup` files contain real member data and password hashes. Do **not** commit them. Add `dbbackup/` to `.gitignore` and delete the files after the import.

### 3.2 Import on the new computer

First finish steps 1 to 4 of [INSTALL.md](INSTALL.md) (clone, `.env`, `pip install`) but **skip** `init_db` and `init_pg`, because the import creates everything.

```powershell
cd PlanAssistant

# --- MySQL (the dump creates the database and tables) ---
cmd /c "mysql -u root -p < dbbackup\mysql_plan_assistant.sql"
# macOS/Linux: mysql -u root -p < dbbackup/mysql_plan_assistant.sql

# --- PostgreSQL / pgvector ---
```

Embedded PostgreSQL (development): start it once so it exists, restore into it, then stop.

```powershell
cd backend
python -c "from dotenv import load_dotenv; load_dotenv('../.env'); from mcp_server import pgvec; pgvec.ensure_ready()"   # creates the database and vector extension
$dsn = python -c "from dotenv import load_dotenv; load_dotenv('../.env'); from mcp_server import pgvec; print(pgvec.dsn())"
$bin = python -c "import pgserver,os;print(os.path.join(os.path.dirname(pgserver.__file__),'pginstall','bin'))"
& "$bin\pg_restore.exe" --no-owner --clean --if-exists --dbname="$dsn" ..\dbbackup\pg_plan_assistant.dump
```

If you can't run that in one session because the embedded server picks a new port each start, put the restore in the same PowerShell window as the `$dsn` lookup (as above) and do not restart the app in between.

Server PostgreSQL (shared host or Docker):

```powershell
psql "postgresql://postgres:pass@HOST:5432/postgres" -c "CREATE DATABASE plan_assistant"
psql "postgresql://postgres:pass@HOST:5432/plan_assistant" -c "CREATE EXTENSION IF NOT EXISTS vector"
pg_restore --no-owner --dbname="postgresql://postgres:pass@HOST:5432/plan_assistant" dbbackup\pg_plan_assistant.dump
```

### 3.3 Make the `.env` match

Same values as the old computer, except the host and password if they differ:

```ini
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=<this computer's MySQL password>
MYSQL_DATABASE=plan_assistant
PG_EMBEDDED=1                  # or PG_DSN=postgresql://...
PG_DATABASE=plan_assistant
JWT_SECRET=<copy the old one>  # logins stay valid only if this matches
MCP_INTERNAL_SECRET=<copy the old one>
```

---

## 4. Verify both databases match

Run on **each** computer and compare the numbers.

```powershell
# MySQL
mysql -u root -p plan_assistant -e "SELECT 'users' t, COUNT(*) n FROM users UNION ALL SELECT 'payments', COUNT(*) FROM payments UNION ALL SELECT 'conversations', COUNT(*) FROM conversations UNION ALL SELECT 'messages', COUNT(*) FROM messages; SELECT status, COUNT(*) FROM payments GROUP BY status;"

# PostgreSQL (use the $dsn from above, or PG_DSN)
psql "$dsn" -c "SELECT (SELECT count(*) FROM plan_settings) settings, (SELECT count(*) FROM policy_topics) policies, (SELECT count(*) FROM faq_entries) faq, (SELECT count(*) FROM faq_learned) learned"
```

Then check the app: `cd backend; python -m pytest -q`, start with `.\start.ps1`, sign in as `randy` / `password`, and ask "What is the grace period?" and "How much do I owe?".

---

## 5. Keeping them in sync later

| Need | Do this |
|---|---|
| Everyone sees live changes | Use Option A (one shared server) |
| Refresh a copy from the old computer | Repeat sections 3.1 and 3.2. `mysqldump --databases` includes `DROP TABLE IF EXISTS`, so the import replaces the tables |
| Only policy / FAQ text changed | Edit `backend/app/data/*.json`, then `python -m mcp_server.init_pg --reset` (this clears FAQ hit counts) |
| Only the plan thresholds changed | `python -m mcp_server.init_pg --overwrite-settings` |
| Add the demo users to an empty database | `python -m mcp_server.init_db` (inserts missing rows only) |

**Never** run `python -m mcp_server.init_db --reset` on a database that holds real data: it drops `users` and `payments`.

## 6. Troubleshooting

| Error | Fix |
|---|---|
| `Can't connect to MySQL server on 'HOST'` | MySQL not listening on the network (`bind-address`), firewall closed on 3306, or wrong IP |
| `Access denied for user` | Wrong password, or the user was created for `'localhost'` only; create it for `'%'` or the client IP |
| `Unknown database 'plan_assistant'` | Run the import in 3.2, or `CREATE DATABASE plan_assistant` |
| `extension "vector" is not available` | The PostgreSQL server has no pgvector. Use the `pgvector/pgvector` image or enable the extension on the managed service |
| `pg_restore: error: ... already exists` | Add `--clean --if-exists`, or restore into a fresh database |
| App says "Plan settings are unavailable" | MCP server cannot reach Postgres. Check `PG_DSN` / `PG_EMBEDDED`, restart `python -m mcp_server.server`, wait about 30 s |
| Users cannot log in after the move | `JWT_SECRET` differs from the old computer, or the `users` table was not imported |
