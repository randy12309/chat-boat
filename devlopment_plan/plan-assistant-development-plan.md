# Plan Assistant Development Plan

## 1. Purpose

This plan describes how to build the Plan Assistant as independently testable frontend and backend services. It follows the implementation target in [plan-assistant.md](../plan_assistant/plan-assistant.md).

The target stack is:

- **Frontend:** the existing AngularJS application.
- **API and domain backend:** FastAPI with SQLAlchemy and PostgreSQL.
- **Agent orchestration:** LangGraph, running in the backend.
- **Agent tools:** a separately configured FastMCP service called by LangGraph through an MCP client.
- **Initial development data:** dummy users and payments only; no live payment processing.
- **Conversation storage:** transition from existing cache-backed behavior to PostgreSQL-backed sessions, conversations, and ordered chat history.

The frontend, API backend, and MCP tool service must have separate responsibilities, configuration, tests, and deployment boundaries. This document is a delivery plan, not a claim that the listed application code already exists.

## 2. Scope and boundaries

### In scope

- AngularJS chat experience for signed-in and anonymous visitors.
- FastAPI endpoints for health, users, account/payment demo data, sessions, conversations, messages, and chat requests.
- PostgreSQL schema managed through SQLAlchemy models and migrations.
- Dummy user and payment seed data in development and test environments only.
- FastMCP tools for approved balance, policy retrieval, premium explanation, and payment-demo operations.
- LangGraph supervisor and specialist routing, guarded tool use, and conversation context.
- Cache compatibility during migration, with PostgreSQL as the eventual source of truth.
- Automated tests, local development setup, API/MCP contracts, and operational documentation.

### Not in the initial delivery

- Real payment capture or storage of payment card data.
- Production identity-provider integration until selected.
- Production plan-document ingestion and retrieval until approved sources are supplied.
- Production email/reminder delivery, unless separately prioritized and configured.
- Regulated eligibility or claims decisions.

## 3. Target service layout

Keep application boundaries separate. A suitable starting layout is:

```text
frontend/
  src/
    app/
      core/              # API client, auth/session, shared configuration
      chat/              # conversation UI and message flow
      home/              # welcome, premium alert, inbox, suggested questions
      payment/           # demo quote and explicit confirmation UI
      preferences/
    test/

backend/
  app/
    api/                 # FastAPI routers and request/response schemas
    core/                # settings, auth, logging, errors
    db/                  # SQLAlchemy engine, session, migrations
    models/              # SQLAlchemy ORM models
    repositories/        # persistence queries
    services/            # users, sessions, conversations, payments, chat
    agents/              # LangGraph graph, state, nodes, prompts
    mcp_client/          # typed client for the FastMCP service
  tests/

mcp_server/
  app/
    server.py            # FastMCP server and tool registration
    tools/               # typed, allowlisted tool handlers
    clients/             # authenticated internal FastAPI client
  tests/
```

Treat this as a logical layout; preserve an existing repository's established package structure where application code already exists. Do not have the AngularJS client connect directly to PostgreSQL, the cache, or MCP. The FastMCP service must not bypass FastAPI domain authorization or write database tables directly.

## 4. Delivery phases

Implement phases in order. Each phase should end with tests passing and its acceptance criteria met before the next phase depends on it.

### Phase 0 — Repository and environment discovery

**Backend**

- Confirm whether API, cache, database, or agent code already exists before scaffolding replacements.
- Record the current cache behavior, its keys/TTL, and which data is currently held only in cache.
- Select supported Python and PostgreSQL versions based on deployment constraints.
- Establish environment-specific configuration for local, test, and production settings. Do not commit credentials.

**Frontend**

- Identify AngularJS version, build/test tooling, route setup, current home/chat UI, and current cache/API assumptions.
- Identify existing UI components and style conventions to reuse.

**Deliverables**

- Confirmed repository map and local run instructions.
- A concise list of existing components to extend and gaps requiring new code.

**Exit criteria**

- Existing source and cache usage are understood; no duplicate app scaffold is created.
- Backend, database, frontend, and MCP service ownership boundaries are agreed.

### Phase 1 — Contracts and local development foundation

**Backend**

- Define versioned HTTP request/response schemas for chat, user/session identity, conversation history, and payment demo operations.
- Define typed MCP tool inputs and outputs separately from internal database models.
- Define consistent error responses, request IDs, timestamps, pagination rules, and status values.
- Create isolated dependency/configuration setup for FastAPI and FastMCP.

**Frontend**

- Define the AngularJS API-client interface around the HTTP contract; keep `$http` calls out of presentation components.
- Define loading, empty, retry, and error states for chat and history.

**Shared**

- Add local development instructions and environment variable examples without secrets.
- Add health/readiness checks for the API, MCP service, and PostgreSQL connectivity.
- Set up format, lint, and test commands consistent with existing project tooling.

**Exit criteria**

- Frontend and backend can be developed and tested independently.
- Contract examples and error behavior are agreed before implementing dependent flows.

### Phase 2 — PostgreSQL, SQLAlchemy, and dummy domain data

**Backend**

- Configure SQLAlchemy engine/session lifecycle and transaction handling.
- Add schema migrations; do not rely on application startup to silently mutate production schemas.
- Implement initial `users` and `payments` tables:
  - `users`: UUID or established project ID type, normalized unique email, display name, timestamps. Never store plaintext passwords.
  - `payments`: user foreign key, amount in integer minor units, currency, billing period, status, idempotency key, optional non-sensitive provider reference, timestamps.
- Add development/test-only seed data for clearly fictional users and payment records.
- Validate user ownership and payment state in domain services rather than in the UI or LLM.

**Frontend**

- Use API responses for account/premium display; never create authoritative balance values in the browser.
- Label any seeded/demo account data clearly as test data in development.

**Tests**

- Model constraints, foreign keys, migration upgrade, repository behavior, and seed restrictions.
- Ensure a production configuration cannot run the development seed routine.

**Exit criteria**

- A clean local database can be migrated and seeded deterministically.
- User and payment reads are served by FastAPI using SQLAlchemy; no live payment operation exists.

### Phase 3 — FastAPI domain and API foundation

Implement API routes, request validation, domain services, and repositories as separate layers. Keep route handlers small and avoid putting business rules into Pydantic schemas or AngularJS components.

Suggested endpoint groups (exact paths and versioning are to be finalized in Phase 1):

| Capability | Initial behavior |
|---|---|
| Health | Liveness and readiness; readiness verifies required dependencies. |
| User context | Return the authenticated/demo user's permitted profile and account summary. |
| Payments | Read dummy payment status, amount, due date, and history. |
| Conversations | Create/list/get conversations subject to identity and ownership. |
| Messages | Return ordered history and append a message with an atomic sequence. |
| Chat | Submit a user message and receive a grounded assistant response and allowed actions. |
| Demo payment | Quote and simulate a payment state transition only; never contact a live processor. |

**Security requirements**

- Establish an authentication seam, even if the first local implementation uses a clearly marked development identity provider.
- Do not accept client- or model-supplied user IDs as proof of identity.
- Validate ownership on every conversation, message, and payment access.
- Use explicit authorization dependencies and stable error codes.

**Exit criteria**

- API tests cover validation, authorization, ownership isolation, transactions, and error responses.
- A user cannot read another user's account, payment, or conversation by changing an identifier.

### Phase 4 — Session, conversation history, and cache migration

**Persistence**

Add backend-owned persistence for:

| Table | Initial purpose |
|---|---|
| `sessions` | Identity type, nullable user association, status, expiry, and timestamps. |
| `conversations` | Owner/session association, title or summary, status, and timestamps. |
| `messages` | Conversation ID, unique sequence number, role, content, timestamp, and safe metadata. |

- Enforce foreign keys and unique `(conversation_id, sequence_number)` ordering.
- Store only minimal context required to resume. Never store card data, credentials, or sensitive health information in AI memory.
- Apply retention and deletion policies once their values are approved.

**Migration steps**

1. Inventory cache-backed state and define a versioned serialization format.
2. Keep the cache available for active requests while adding PostgreSQL persistence behind FastAPI.
3. Persist each accepted user message and completed assistant message to PostgreSQL transactionally before reporting that the conversation is saved.
4. On cache miss, load authorized session/conversation state from PostgreSQL; do not turn an expired/missing session into a new authorized session.
5. Compare cache and database behavior in tests and, if safe, in non-production diagnostics.
6. Remove cache as the authority for history only after persistence, restore, ownership, expiry, and failure tests pass.
7. Retain cache only for short-lived/derived data where it has measured value; document invalidation and TTL behavior.

**Exit criteria**

- Conversations survive cache eviction and backend restarts.
- Messages are returned in order with correct ownership checks.
- Cache outages or misses do not lose acknowledged history or bypass authentication.

### Phase 5 — AngularJS frontend

Implement the UI against the FastAPI contract, with presentation and data access separated.

**Core modules**

- **API client:** centralize base URL, headers, request IDs, error normalization, and cancellation behavior.
- **Session/auth state:** represent anonymous and authenticated states explicitly; handle expiry and sign-in return flow.
- **Home:** welcome, premium alert, inbox summary, suggested questions, and chat entry. Anonymous users see public content only.
- **Chat:** ordered messages, input, sending/loading states, clarification prompts, citations, next-step actions, and feedback controls.
- **Conversation history:** list/reopen authorized conversations and restore messages from the API.
- **Payment card:** show a verified demo quote and exact amount/method; require a separate explicit confirmation; visibly label demo/simulated payment.
- **Accessibility:** keyboard operation, focus management, semantic labels, readable errors, and responsive layout.

**Behavior**

- A suggested question sends the same message as typing it.
- Opening an alert or inbox item attaches only the relevant authorized context.
- On account-specific anonymous requests, preserve the pending question safely through sign-in and resume it only after backend authentication.
- Do not render account details for an anonymous visitor.
- Never infer payment success from a button click; display the API's confirmed demo status.
- Do not place authorization or payment safety rules only in client code.

**Tests**

- AngularJS unit tests for API client, state transitions, error handling, and key components.
- End-to-end tests for anonymous public question, sign-in continuation, member history restore, and demo payment confirmation.
- Accessibility checks for keyboard navigation and key screen-reader labels.

**Exit criteria**

- Main flows work against a running API without direct database/cache access from the browser.
- The UI clearly distinguishes loading, empty, failed, and successful states.

### Phase 6 — FastMCP tools and LangGraph orchestration

**FastMCP service**

- Implement a separately configured server with an explicit allowlist of typed tools.
- Initially expose read-only/deterministic tools for balance lookup, public policy retrieval, and premium explanation data.
- Implement payment tools as quote and simulated execution only; no live processor integration in this plan.
- Authenticate internal calls to FastAPI and propagate a trusted principal/context. Do not trust model-generated identity or ownership arguments.
- Return structured results with provenance, status, and explicit error types.
- Add tool input validation, timeouts, rate limits, and contract tests.

**LangGraph**

- Define a typed graph state containing only needed conversation/task context and trusted identity metadata from the backend.
- Build supervisor routing and specialist nodes for Check Balance, Policy Q&A, Explain Premium, and Pay Premium.
- Bind only tools permitted for the current identity and task. Anonymous users receive public Policy Q&A only.
- Require deterministic backend confirmation and authorization gates before any simulated payment state change.
- Persist checkpoints alongside the conversation/session ownership boundary; never load a checkpoint before authorization.
- Add recursion/time limits, tool failure handling, safe fallback messages, and grounded answer/citation validation.
- Avoid placing secrets or sensitive data in prompts, graph state, logs, or checkpoint metadata.

**Tests**

- Graph routing and multi-agent cases.
- Anonymous tool exclusion and prompt-injection attempts.
- MCP input/output contract, auth failure, timeout, malformed output, and tool unavailability.
- Checkpoint restore and ownership enforcement.
- Verify model output alone cannot execute a payment or change authoritative account/payment data.

**Exit criteria**

- LangGraph runs through the FastAPI service and uses FastMCP only for explicitly allowed tasks.
- Every account fact comes from an authorized backend/tool result; unsupported answers are handled safely.

### Phase 7 — Integration, reliability, and release readiness

- Run frontend, FastAPI, FastMCP, and PostgreSQL together in a repeatable local environment.
- Add end-to-end coverage across the service boundaries and verify API/MCP schema compatibility.
- Test database and cache outages, expired sessions, duplicate message submissions, and agent/tool failures.
- Add structured logs and metrics with correlation IDs; redact PII and never log secrets or payment credentials.
- Add dependency and configuration checks, database backup/restore guidance, and migration rollback procedures.
- Confirm production-like configuration disables dummy seed data and simulated payment endpoints.
- Conduct product/security review of open policy decisions before any production rollout.

**Release gate**

- Required tests and static checks pass.
- Migrations can be applied to a clean database and existing development data can be backed up/restored.
- Ownership and anonymous-access tests pass.
- No live payment functionality or real payment credentials are present in the initial release.
- Deployment, rollback, monitoring, and incident ownership are documented.

## 5. Workstream acceptance checklist

### Frontend

- [ ] AngularJS app communicates only with versioned FastAPI endpoints.
- [ ] Member and anonymous experiences show only permitted data and tools.
- [ ] Chat, suggested questions, card context, follow-ups, conversation restore, and error states work.
- [ ] Payment demo requires explicit confirmation and never claims unverified success.
- [ ] Keyboard and responsive behavior meet the project's accessibility target.

### FastAPI and persistence

- [ ] Authentication/identity and ownership checks are enforced at every protected endpoint.
- [ ] SQLAlchemy models, migrations, constraints, and transaction boundaries are tested.
- [ ] Dummy user/payment data is restricted to development/test.
- [ ] Session, conversation, and message history restore from PostgreSQL independently of cache.
- [ ] Errors are explicit; no broad catches or success-shaped fallback responses.

### FastMCP and LangGraph

- [ ] MCP tools are typed, allowlisted, authenticated, and independently tested.
- [ ] LangGraph routing, state, checkpoint restore, and tool failures are tested.
- [ ] Anonymous requests cannot invoke member/account/payment tools.
- [ ] Model output cannot bypass backend domain rules, identity, authorization, or payment confirmation.
- [ ] Responses are grounded in retrieved or verified data and cite policy sources where relevant.

## 6. Suggested implementation sequence

1. Complete Phase 0 discovery and record existing cache/API/frontend behavior.
2. Agree contracts, identity approach for local development, and application boundaries.
3. Build PostgreSQL/SQLAlchemy setup, migrations, and dummy user/payment data.
4. Implement and test FastAPI domain services and authorization.
5. Add persistent sessions, conversations, messages, and cache fallback/migration.
6. Build AngularJS API client, home/chat, history, and demo payment UX.
7. Add FastMCP tools and LangGraph orchestration over the tested domain APIs.
8. Complete end-to-end, resilience, security, accessibility, and release checks.

Keep production authentication, retention policy, model/provider selection, plan document sources, and live payment integration behind explicit decision gates; do not guess these values during implementation.

## 7. Decisions to confirm before the dependent phase

These are not blockers to starting discovery, API/database scaffolding, or dummy-data development. Confirm each before implementing the affected production behavior:

| Decision | Needed before | Safe planning assumption |
|---|---|---|
| Existing cache technology, current cache data shape, and whether it must be migrated | Phase 4 | Preserve cache behind an adapter; PostgreSQL becomes source of truth for newly persisted records. |
| Identity provider and anonymous-session rules | Protected production APIs and sign-in continuation | Use an isolated development identity only; never treat a submitted user ID as authentication. |
| AngularJS version and current build/test stack | Frontend changes | Reuse the repository's existing version and conventions; do not upgrade frameworks as part of this plan. |
| Currency precision, payment statuses, and billing-period format | Payment schema/API contract | Store amounts in integer minor units; use explicit status enum and ISO period representation. Confirm domain-specific values. |
| Retention, deletion, and anonymous history expiry | Production session/history release | Use configurable expiry; do not retain anonymous history beyond its session until approved. |
| LLM provider/model and deployment region | Agent integration/release | Keep provider configuration isolated and secrets external; do not hardcode a vendor dependency into domain services. |
| Approved policy sources and citation format | Production Policy Q&A | Use test documents in development; refuse unsupported production questions. |
| Payment provider and authorization requirements | Any real payment integration | Simulate only. No live charge capability in this plan. |
| Reminder/email provider, schedule, and opt-in behavior | Notification implementation | Defer delivery; do not send emails from dummy records. |
| Deployment platform, environments, and operational ownership | Release | Keep services independently deployable and configuration-driven. |

## 8. Definition of done

A feature is complete only when its API/UI behavior, authorization, persistence, error handling, and tests are delivered together. A phase is complete only when its exit criteria are met and the next phase can use the documented contract without relying on undocumented cache state, model behavior, or manually seeded production data.
