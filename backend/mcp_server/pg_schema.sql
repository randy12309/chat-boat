CREATE EXTENSION IF NOT EXISTS vector;

-- Plan-wide numbers that used to be constants in code (eligibility threshold, quarterly premium, ...).
CREATE TABLE IF NOT EXISTS plan_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Policy rules, embedded so get_policy_info can match a topic by meaning. content may hold {setting_key} placeholders.
CREATE TABLE IF NOT EXISTS policy_topics (
    topic        TEXT PRIMARY KEY,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding    vector(384) NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_policy_topics_embedding ON policy_topics USING hnsw (embedding vector_cosine_ops);

-- FAQ questions. answer IS NULL marks a member-specific question: it is indexed so a close match is routed to the
-- agent (live data) instead of a similar public answer. hit_count drives which answers are kept hot in the cache.
CREATE TABLE IF NOT EXISTS faq_entries (
    id           TEXT PRIMARY KEY,
    question     TEXT NOT NULL,
    question_key TEXT NOT NULL,
    answer       TEXT,
    source       TEXT NOT NULL DEFAULT 'faq',
    content_hash TEXT NOT NULL,
    hit_count    INTEGER NOT NULL DEFAULT 0,
    last_hit_at  TIMESTAMPTZ,
    embedding    vector(384) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_faq_entries_key ON faq_entries (question_key);
CREATE INDEX IF NOT EXISTS idx_faq_entries_hits ON faq_entries (hit_count DESC);
CREATE INDEX IF NOT EXISTS idx_faq_entries_embedding ON faq_entries USING hnsw (embedding vector_cosine_ops);

-- Phrasings that were answered through vector search, with how often they were asked. Frequent ones are promoted
-- into the in-memory cache. Only public (non-personal) answers ever get a row here.
CREATE TABLE IF NOT EXISTS faq_learned (
    question_key TEXT PRIMARY KEY,
    question     TEXT NOT NULL,
    entry_id     TEXT NOT NULL REFERENCES faq_entries (id) ON DELETE CASCADE,
    hit_count    INTEGER NOT NULL DEFAULT 1,
    last_hit_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_faq_learned_hits ON faq_learned (hit_count DESC);
