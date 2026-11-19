# Plan Assistant: Valkey cache production plan

**Status:** Proposed plan, not a production-ready implementation. The current workspace contains partial, untested cache changes from an earlier interrupted implementation. Review, complete or revert those changes before treating any behavior below as implemented. This plan cannot guarantee zero production incidents; its gates are intended to expose risks before rollout and provide a safe recovery path.

## 1. Goals and scope

- Share public policy cache entries across users. Keep private account cache entries isolated by the authenticated user.
- For an eligible account question: check the user's cache, call the account agent and its MCP tools on a miss, pass the tool result to the LLM, and cache an eligible successful response for that user.
- Support semantic matches with a cosine similarity **strictly greater than 0.85**, subject to the safety checks below. A score alone is not proof of equivalent meaning.
- Keep the number of users in the account **answer cache** configurable: start with 4 in development and evaluate 10,000 for production. This limit must not prevent other users from accessing the application.
- Design for 90,000 registered users and load-test for 20,000 concurrent users. These are planning inputs, not verified capacity claims.
- Preserve existing API response shapes, server-controlled identity, payment correctness and Unix/Windows startup behavior. Do not cache payment mutations or use cache as the system of record.

## 2. Current project behavior to verify before implementation

The existing system has multiple caches, not just one:

| Area | Existing behavior | Production concern |
| --- | --- | --- |
| Policy FAQ | Public exact lookup in Valkey; local process memory fallback; semantic lookup scans up to 2,000 Valkey entries; Qdrant/MCP retrieval on a miss. | A scan is bounded but can miss a better match and gets expensive with growth. Local entries can outlive source updates. |
| Policy answers | On a FAQ hit the policy agent returns the stored answer directly. On a miss it retrieves policy text, remembers that text, then asks the LLM to write an answer. | Cached content is not necessarily the same LLM-written answer. Version and source changes must be handled. |
| Account data | `get_balance` and `get_eligibility` use user-hashed Valkey keys, a 120-second TTL and a generation check for writes racing invalidation. | Those raw tool caches are separate from the proposed answer cache and do not enforce a four-user limit. |
| Other account data | Payment methods and payment history use separate user-hashed keys with 300/120-second TTLs. `explain_premium` currently reads the source directly. | These keys have separate invalidation behavior and must be included in the freshness review. |
| Mutations | Both the HTTP payment route and MCP payment execution invalidate some caches. | Verify every successful mutation path and all external writers; payment history invalidation is not identical across the two paths. |
| Local infrastructure | `compose.yaml` runs a single 256 MB Valkey with `allkeys-lfu`, no persistence, and localhost binding; Qdrant is a separate local service. | This is a development configuration, not a high-availability or capacity design. Global LFU eviction can remove any cache key, including coordination metadata. |

The backend authenticates users and mints a short-lived MCP principal. Cache identity must come from that trusted server-side identity, never from the question, an LLM tool argument, a client-supplied user ID, or an embedding. Confirm whether the authenticated username is a stable, unique identifier across tenants and account changes; if not, use the canonical immutable account ID and tenant ID resolved by the server.

## 3. Proposed read flows

### Account

1. Authenticate and authorize the caller. Determine the trusted tenant/account identity and route the question to the account agent.
2. Determine whether the request is eligible for answer caching. Reject ambiguous, context-dependent, multi-intent, sensitive or unsupported requests; include relevant parameters such as payment status, date range, limit and locale in the cache identity. Do not cache errors, incomplete answers, tool failures, unverified assertions or responses with no successful relevant source read.
3. Check the user's exact answer entry first. Only consider a semantic candidate from the **same user and same validated intent, parameters, account and response context**. Require similarity > 0.85, a compatible model/version, and all freshness and authorization checks. Negation, time period, amounts, account scope and requested detail must match. If equivalence is uncertain, miss. Begin with a narrowly evaluated allowlist, such as simple balance requests; enable more intents only after evaluation.
4. On a miss, capture the relevant source version, run the existing account agent and MCP reads, and let the LLM produce the answer. Preserve the existing response, source attribution and streaming behavior. Write only after successful completion and only if the source version remains valid. A first-time user follows this same miss path.
5. Return a cache hit with the same response shape and sources as the original response. A cache outage or bad entry becomes a miss; it must never cause a cross-user lookup or an invented answer.

Do not cache an LLM answer solely because an MCP call succeeded: validate that the answer is grounded in the relevant result and is appropriate for reuse. The safer default for financially sensitive results is to cache structured, validated tool data and regenerate an explanation. If answer-level reuse cannot meet the product's accuracy evaluation, keep it disabled for that intent even though raw account caching remains available. Do not let a conversation-history-dependent answer be reused in a different context.

### Policy

1. Check the shared public FAQ exact cache, then a version-compatible semantic candidate, then the existing Qdrant/MCP policy retrieval flow on a miss.
2. Store only content confirmed to be public. Include policy/document revision, plan or jurisdiction scope where relevant, language, embedding model version and answer schema/prompt version in cache metadata. A policy must not be treated as universal if it varies by plan or member context.
3. Define whether an entry contains reviewed FAQ text, retrieved policy text or an LLM-generated answer. Preserve source attribution and ensure any learned answer is derived from current public sources. The current system stores retrieved text on a miss, not necessarily the generated answer.
4. Invalidate or version policy entries and each worker's local memory on upload, replacement, deletion or plan-setting changes. Exact and semantic entries must move together. Preserve a safe fallback while rebuilding indexes.

## 4. Data model, isolation and limits

- Use separate versioned namespaces for shared policy entries, private account data, private account answers, active-user membership and coordination metadata. Use an HMAC of the canonical tenant/account ID with a managed secret for key identifiers; ordinary hashes alone do not prevent guessing common usernames. Never put raw identifiers, tokens, full payment credentials or sensitive questions in key names, metrics or logs.
- Define and bound the stored fields: intent and parameter fingerprint, normalized question where genuinely needed, answer or validated payload, source and source revision, created/expiry times, embedding and model version. Set maximum lengths, entries per user and overall memory limits. Minimize personal data and apply the organization's retention, deletion and access policies; a hash does not anonymize cached personal data.
- Define active as **most recently used for eligible account-answer caching**, not most frequent. Use an atomic shared LRU or a tested alternative. On admission or access, update activity; when above the configured limit, evict a victim's answer entries. Expired or independently evicted users must not leave an unbounded index. LRU is an approximation of “most active”; a frequency-based policy requires a separate product decision.
- Start with 4 cached users in development. Make maximum users, per-user entries, TTL, semantic threshold and feature switches configurable, validated at startup and consistently deployed. Define behavior for zero/disabled and invalid settings explicitly. Increasing the limit to 10,000 is an operational change only after capacity validation. Existing raw account caches need their own memory bounds and are not automatically included in the answer-user limit.
- Set TTLs by data type and approved freshness requirements. The existing 120-second balance TTL is a baseline for evaluation, not an approved service-level guarantee. An answer's expiry cannot exceed the freshness of its oldest input or outlive a source/permission revision. Avoid sliding expiry that extends stale content indefinitely.
- Do not assume multiple-key Lua scripts work in Valkey Cluster: keys in a script must be in the same hash slot. The global LRU and per-user keys cannot simply be atomically scripted across arbitrary cluster slots. Choose and test a deployment-compatible design (for example, a single primary/shard for this bounded index, or a sharded admission/eviction design with explicitly weaker global ordering). Document limits and failover semantics before choosing production topology.

## 5. Freshness, mutations and failure handling

- Map every writer: HTTP payments, MCP payment execution, payment method changes, administrative changes, batch jobs and external database updates. Invalidate all dependent raw data and answer entries only after a committed mutation, including payment history and eligibility where affected. Payment decisions and due-payment execution always read an authoritative source and never rely on generated answer text.
- A simple delete after a database commit is not atomic with the database transaction. Specify an agreed staleness bound and implement a durable invalidation mechanism, such as a transactional outbox/change-data-capture event with retry and monitoring, or use an authoritative source version checked on reads. If invalidation delivery or cache version cannot be trusted, bypass affected cached answers until recovery. TTL is a backstop, not a substitute for the required correctness guarantee.
- Prevent an old in-flight loader from repopulating a cache after invalidation using versioned keys or compare-and-write. Coordination/version data must not be lost or reset in a way that makes an old version valid again; account for Valkey eviction, restart, failover, lost writes and version expiry. A Valkey-only generation token is not durable across such events. Define a clear consistency model for a cache read racing a commit and verify it under failure injection.
- On Valkey timeout, invalid data or outage, bypass the cache with bounded timeouts, connection pools and circuit breaking. Protect MCP, database, embedding service and LLM with concurrency limits, load shedding and per-key request coalescing so a cold cache does not create a stampede. Return the existing safe unavailable response when authoritative dependencies cannot serve the request; do not serve expired personal financial data by default.
- Use versioned namespaces for migration and a feature switch that disables reads and writes without deleting data. Account for multiple API and MCP workers, restarts, clock skew, replayed events and changes to the embedding model.

## 6. Policy cache improvements

- Replace production-scale key scans with a bounded, measured indexed vector lookup, potentially using a separate Qdrant collection with public-only payloads and version filters. Decide whether Valkey remains the exact-answer store and Qdrant the semantic index; define how index write failures, TTL expiry and invalidation stay consistent. Never put private account answers in a shared public policy collection.
- Evaluate the policy threshold separately from account matching. The existing FAQ default is 0.9; do not lower it automatically to 0.85. Build a labeled set of policy paraphrases and dangerous near-matches, including negation and different numbers. Establish acceptable false-hit limits before enabling semantic hits.
- Bound and synchronize the per-process memory cache, use expiry/version checks, and measure its value. Ensure administrative updates invalidate every worker or make old entries unreachable through a new version. Validate embedding dimensions and model identity instead of comparing incompatible vectors.
- Ensure FAQ import/warm-up and normal writes use consistent expiration and revision rules. Test partial imports and concurrent updates. Monitor stale semantic entries and index growth.

## 7. Security, deployment and operations

- Deploy Valkey on a private network with TLS, authentication and least-privilege ACLs; restrict administrative commands and access to cached personal data. Store credentials and HMAC secrets in a secret manager, rotate them with a namespace migration, and do not commit secrets. Confirm retention, deletion, encryption-at-rest and audit requirements with the data owner.
- Size and isolate policy, account and coordination workloads so global eviction cannot silently undermine correctness. Select maxmemory and eviction policies with measured object overhead and reserve. A production high-availability topology must define failover, replication lag, network partitions, backup/recovery needs and behavior after a cold restart. Cached data is disposable, but invalidation guarantees must survive their stated failure cases.
- Review existing admin inspection endpoints so private account questions, answers, embeddings and identifiers are never exposed through public-policy inspection. Log aggregate cache events and source versions without personal payloads. Use low-cardinality metrics for hits, misses, bypass reasons, semantic scores, evictions, memory, latency, errors, invalidation lag, fallback load and model calls; avoid per-user metric labels.
- Establish dashboards, alert thresholds, owners and runbooks for cache outage, memory pressure, stale data, invalidation backlog, unusual semantic hits, Qdrant outage and rollback. Test alerts rather than relying only on dashboards.

## 8. Capacity and performance validation

The figures 90,000 registered users, 20,000 concurrent users and 10,000 cached users do not define requests per second, session length, question mix or required latency. Agree on peak and burst request rates, read/write mix, cache hit target, latency percentiles, freshness requirements, availability objectives and traffic distribution before sizing.

Estimate memory as `cached users × entries per user × measured bytes per entry`, plus user indexes, key/object overhead, raw account and policy caches, replication, allocator fragmentation, embedding storage, temporary buffers and operational headroom. Measure actual serialized and resident size with realistic answers and embeddings; do not assume the development 256 MB limit is sufficient. Account for database, MCP, LLM, network, connection-pool and embedding capacity, not just Valkey throughput.

Load-test a production-like multi-worker topology at expected peak and beyond it, including 20,000 concurrent sessions, cold start, low hit rate, user churn beyond the active-user limit, bursts, hot users, policy updates and cache loss. Measure end-to-end p50/p95/p99 latency, error rate, resource saturation and downstream request rates. Run sustained soak and failover tests. Record measured capacity and bottlenecks; 10,000 cached users is a target configuration, not a proven capacity.

## 9. Implementation and release gates

1. **Baseline and requirements:** review the partial workspace changes; agree freshness/accuracy SLOs, canonical identity, data classification, traffic model and production topology. Inventory source writers and cache dependencies.
2. **Design and test first:** choose the invalidation protocol, eligible intents and cluster-compatible admission design. Write focused tests for the existing behavior and proposed invariants, including external updates and failure races.
3. **Build behind independent flags:** implement private account caching and policy improvements with versioned keys, bounded resources, instrumentation and source checks. Keep semantic account hits disabled until evaluated. Preserve API paths, response shapes and live streaming.
4. **Verify:** run backend `python -m pytest -q` and the project's frontend build/tests if frontend behavior is affected. Add integration tests against real Valkey for TTL, eviction, atomicity and multi-worker behavior; test the actual production topology. Include authorization, user/tenant isolation, distinct parameters, >0.85 boundary, dangerous near-matches, policy revisions, invalidation failure, payment races, restart/failover, corrupted entries and outage fallback. Test both payment paths and external update delivery. Run load, soak and security testing with synthetic data.
5. **Shadow and canary:** initially populate or compare without serving cached answers, measuring correctness and hit quality without logging private content. Enable exact account hits for a small cohort, then approved semantic intents, then expand only when agreed accuracy, freshness, latency, error and downstream-load thresholds are met. Keep policy and account switches independent.
6. **Operate and roll back:** designate an owner, on-call runbook and explicit stop/rollback thresholds. Disable reads and writes by feature flag, fall back to authoritative sources under rate limits, and switch namespace or purge only affected cache entries if necessary. Validate rollback under production-like load and continue monitoring invalidation backlog and stale-hit reports.

## 10. Acceptance criteria and open decisions

Before release, demonstrate that one user's data cannot be returned to another; a new user's eligible miss reaches MCP and can populate only their own cache; safe exact and approved semantic hits preserve the API response; the fifth development user evicts the intended LRU user; and a score of exactly 0.85 does not qualify. Confirm that successful payments and all identified writers meet the agreed freshness SLO, including race, outage and failover cases. Confirm that policy changes cannot continue to serve obsolete cached policy beyond the approved window. Show real test evidence for capacity, failure recovery and rollback.

Decisions needed from product and operations: immutable account/tenant identity; permitted answer staleness per intent; which account questions may reuse LLM answers; approved semantic false-hit rate; peak request rate and latency/error targets; production Valkey topology and budget; whether “most active” means recent or frequent; external change-event availability; and legal/security retention rules.
