# Plan Assistant: UI flow test cases

Use these manual tests to check the user interface, the answer, and the **Admin → Agent activity → View flow** JSON. Each table has five cases for a main flow. These are expected paths, not guarantees about exact LLM wording or routing: the supervisor and account agent use an LLM, so a different route or additional relevant tool calls are possible. Treat incorrect routing or an unsupported answer as a test failure and record the actual JSON.

## Before testing

1. Start MySQL, Qdrant, Valkey, the backend, MCP server and frontend. Configure a working `GROQ_API_KEY`; confirm `QDRANT_URL` and `VALKEY_URL` point to the running services. Restart backend and MCP processes after changing `.env`.
2. Sign in with a test administrator such as Randy. Use test member accounts with known, different balances and eligibility. Confirm that the test account has a pending premium for payment-card tests. Use only approved test data and a safe test environment.
3. Upload a current policy document that contains the rules being tested. A question is expected to produce a policy answer only when the policy or FAQ contains the answer. Empty Qdrant or missing plan settings can cause a not-found or unavailable response.
4. Open the Admin page in another tab and select **Agent activity**. Refresh after each question; flow writes are asynchronous and can appear shortly after the answer. Select **View flow** for the question. The JSON is an ordered array with `step`, `event`, `agent`, `to`, `tool`, `cache_or_storage` and `status`. Do not expect every property to be non-null.
5. Start a **new chat** where indicated. Account answer caching applies to signed-in, account-only requests of at most 300 characters. Standalone balance questions can use it even when the conversation has history; other questions with history bypass it. Its default TTL is 120 seconds. Existing raw MCP account-data caches are separate.
6. For an initial miss, use a question that is not already cached or wait for relevant cache entries to expire. Avoid clearing shared caches or paying real premiums. For each case record the question, user, time, result, JSON, pass/fail and any observed deviation.

The expected flows below describe meaningful milestones. The JSON can also contain question, LLM, answer and join events. Parallel policy/account steps can interleave; the recorded order is the observed order, not proof that the branches ran one after the other. The system does not currently expose every internal MCP account-data cache hit in activity JSON, so do not infer a MySQL read or a raw-cache miss solely from a tool result's `cache_or_storage` field.

## A. Policy flow — five cases

| ID | UI action / question | Expected answer and flow |
| --- | --- | --- |
| P1 | In a new chat ask: “What is the premium grace period?” Use a policy that explicitly contains this rule and a question that is not already cached. | `supervisor → policy_agent → FAQ cache miss → get_policy_info → policy_agent LLM → answer`. The answer must match the uploaded rule and cite its source. |
| P2 | Repeat the exact P1 question in a new chat before cache expiry. | `supervisor → policy_agent → FAQ cache hit → answer`. A cached public answer may be returned without an LLM rewrite or MCP call. Compare with the current policy source. |
| P3 | Ask a supported rephrasing, for example “How long is the grace period for a premium?” after P1 is cached. | A semantic FAQ hit is possible if its score meets the configured policy threshold (default 0.9); otherwise expect the retrieval path in P1. The answer must address the same rule. Similarity is not shown in every flow event. |
| P4 | Ask “Can I add a spouse as a dependent?” with the relevant policy uploaded. | `supervisor → policy_agent → FAQ lookup → cache hit or get_policy_info → answer`. Verify the answer uses the actual eligibility and enrollment conditions in the source. |
| P5 | Ask a clearly unrelated policy question for which no relevant FAQ or uploaded text exists, such as “What is the plan's moon-travel benefit?” | `supervisor → policy_agent → cache miss → get_policy_info`. If retrieval finds nothing, the UI should say it has no policy information; it must not invent a benefit. If the vector store returns unrelated text, flag any unsupported answer. |

## B. Account flow — five cases

| ID | UI action / question | Expected answer and flow |
| --- | --- | --- |
| A1 | Sign in as a test member. In a new chat ask “What is my balance?” with no matching answer cached. | `supervisor → account_agent → user account answer cache miss → get_balance → account_agent LLM → answer`. Check the balance and billing status against that member's authoritative account data. |
| A2 | Within 120 seconds of A1, ask the exact same question in the **same chat** as the same user. | If A1 was successfully cached, `supervisor → account_agent → user account answer cache hit → answer`, without a new account MCP tool call. In **Admin → Account cache**, inspect that username and check the answer and remaining TTL. |
| A3 | Within the same TTL, start another new chat as that user and ask “Check my balance.” | A semantic hit is possible for this supported balance wording if the cached entry has an embedding and cosine similarity is **greater than 0.85**. Otherwise expect a miss and `get_balance`. Verify the response refers to the same balance; a hit is not guaranteed. |
| A4 | Sign out, sign in as a different test member, and ask “What is my balance?” in a new chat. | `supervisor → account_agent → that user's cache → get_balance on miss → answer`. The first user's answer must never be returned to the second user. Compare each answer with its own account data. |
| A5 | Ask “Am I eligible for insurance and pension based on my payments?” in a new chat. | `supervisor → account_agent → cache lookup → get_eligibility on miss → answer`. Verify the eligibility and amounts against current plan settings and payment records. If settings are unavailable, expect a safe failure, not invented eligibility. |

Also inspect saved methods and payment history with “Show my saved payment methods” and “Show my recent payments.” Relevant tools are `list_payment_methods` and `list_epayments`; these require the configured database. Do not display full card or bank secrets.

## C. Combined policy and account flow — five cases

For these cases, use source documents containing the named rules. The policy and account agents execute in parallel. A combined request currently does **not** use the account-answer cache, although individual MCP reads may use their own data cache.

| ID | UI action / question | Expected answer and flow |
| --- | --- | --- |
| C1 | “What is my balance and what is the grace period?” | `supervisor → policy_agent + account_agent`; the policy branch checks FAQ/Qdrant and the account branch normally calls `get_balance`; `join → answer`. Verify both parts independently. |
| C2 | “Am I eligible for insurance, and what are the plan's general eligibility rules?” | Parallel policy retrieval and account `get_eligibility`, then join. The answer should distinguish the user's current result from general plan rules. |
| C3 | “What do I owe and what does the policy say about late payment?” | Parallel policy lookup and an account balance read, then join. Verify the user's amount and the documented late-payment rule separately. |
| C4 | “Explain my premium amount and the plan's general premium rules.” | Parallel policy lookup and account `explain_premium` when the agent needs the breakdown, then join. Verify that plan settings are available and the numerical breakdown matches the tool data. |
| C5 | “Am I eligible for pension, and what does the policy say about pension eligibility?” | Parallel policy lookup and `get_eligibility`, then join. The response must not turn a general rule into an unsupported personal guarantee. |

## D. Payment flow — five cases

**Use an isolated test account and simulated/test payments only.** Do not confirm a payment unless the test environment and account are approved. The payment agent checks due items; the actual payment is performed later through the authenticated UI payment endpoint, not by the LLM. The payment endpoint's internal activity is not necessarily represented in the question's agent-flow JSON.

| ID | UI action / question | Expected answer and flow |
| --- | --- | --- |
| D1 | With an unpaid test premium, ask “I want to pay my premium.” | `supervisor → payment_agent → get_due_payments → confirmation prompt`. The UI asks whether the user intends to pay; asking alone must not pay anything. |
| D2 | On the D1 prompt, decline or leave without confirming. | No payment mutation occurs; the premium remains due. Verify through the account/payment view. The initial question's JSON should show `get_due_payments`, not `execute_payment`. |
| D3 | With an approved simulated test account, continue from the prompt to the pay card but do not authorize payment. | The UI should not complete the payment without authorization. The due amount and status should remain unchanged. |
| D4 | In an approved test environment, select the user's own due item, authorize and complete payment. Then ask for the balance in a new chat. | The UI reports the test payment result. A subsequent account read must reflect the committed payment rather than an old account-answer cache entry. Check the authoritative record and the new activity JSON; the original chat flow does not necessarily include the HTTP payment action. |
| D5 | Use a test account with no premium due and ask “I want to pay my premium.” | `supervisor → payment_agent → get_due_payments → no-premium-due reply`; no payment should be made. |

## E. Direct and sign-in flow — five cases

| ID | UI action / question | Expected answer and flow |
| --- | --- | --- |
| E1 | Ask “Hello.” | `supervisor → direct answer`; no account or policy MCP tool is needed. |
| E2 | Ask “Thank you.” | `supervisor → direct answer`; no account or policy MCP tool is needed. |
| E3 | Ask “What is a deductible in general?” | The supervisor may provide a general answer directly. It must not present an invented plan-specific deductible or member account data. |
| E4 | Where the UI permits an anonymous chat, ask “What is my balance?” while signed out. | `supervisor → sign_in`; the reply requests sign-in and no member account MCP tool is called. If the UI requires login before chat, verify the login redirect instead and mark the chat case not applicable. |
| E5 | Where anonymous chat is available, ask “What is my balance and what is the grace period?” | The account portion requests sign-in; the policy portion can still use `policy_agent` and public policy retrieval. No private account tool should be called. If login is mandatory before chat, mark not applicable. |

## Cache, isolation and activity checks

- **Four-user limit:** with the default `ACCOUNT_CACHE_MAX_USERS=4`, use five separate test members and eligible first-turn account questions. Inspect the account-answer cache for each member. After inserting the fifth, the least recently used user's answer entries should be evicted. The raw MCP data caches have separate keys and are not covered by this four-user answer-cache limit. Inspecting a user in the admin cache view should not itself count as user activity.
- **Expiry:** inspect a cached answer, wait past its reported TTL, then repeat the question in a new chat. Expect a miss and source/tool path. Actual times vary by processing delay.
- **JSON order:** verify `step` values increase and that the routing, cache and tool milestones match the question. A `tool_call` and its `tool_result` are separate events. Concurrent branches can interleave.
- **Privacy:** a normal member must not reach admin pages or the admin cache endpoint. Never use one user's expected amount as another's expected result. Do not copy private answers into a public bug report.
- **Availability:** with Valkey unavailable in a test environment, account requests should fall back to MCP/source reads when available. A Qdrant, MySQL or LLM outage may produce an unavailable response; no test should expect invented account data. Restore the service after the test.

## Result template

| Field | Record |
| --- | --- |
| Case ID / date | |
| Test user and environment | |
| Question and relevant setup | |
| Expected result and flow | |
| Actual answer and activity JSON | |
| Pass / fail / not applicable | |
| Notes or issue reference | |
