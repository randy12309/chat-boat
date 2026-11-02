CREATE TABLE IF NOT EXISTS users (
    id            CHAR(36)     NOT NULL,
    username      VARCHAR(50)  NOT NULL,
    email         VARCHAR(255) NOT NULL,
    display_name  VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_demo       TINYINT(1)   NOT NULL DEFAULT 0,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username),
    UNIQUE KEY uq_users_email (email),
    CONSTRAINT ck_users_email_normalized CHECK (BINARY email = LOWER(TRIM(email)))
) ENGINE=InnoDB;

-- One row per premium billing record. Amounts are integer minor units (cents). No card data is stored.
CREATE TABLE IF NOT EXISTS payments (
    id                 CHAR(36)     NOT NULL,
    user_id            CHAR(36)     NOT NULL,
    amount_minor       INT UNSIGNED NOT NULL,
    currency           CHAR(3)      NOT NULL DEFAULT 'USD',
    billing_period     CHAR(7)      NOT NULL COMMENT 'ISO year-month, e.g. 2026-11',
    due_date           DATE         NOT NULL,
    status             ENUM('Due','Overdue','Paid','Failed') NOT NULL DEFAULT 'Due',
    idempotency_key    VARCHAR(64)  NULL,
    provider_reference VARCHAR(64)  NULL,
    simulated          TINYINT(1)   NOT NULL DEFAULT 1,
    paid_at            TIMESTAMP    NULL,
    created_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_payments_user_period (user_id, billing_period),
    UNIQUE KEY uq_payments_idempotency (idempotency_key),
    KEY idx_payments_user_status (user_id, status),
    CONSTRAINT fk_payments_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT ck_payments_amount CHECK (amount_minor > 0),
    CONSTRAINT ck_payments_currency CHECK (BINARY currency = UPPER(currency))
) ENGINE=InnoDB;

-- A chat session. session_id is the client-facing id, unique per user so one user can never read another's.
CREATE TABLE IF NOT EXISTS conversations (
    id         CHAR(36)     NOT NULL,
    user_id    CHAR(36)     NOT NULL,
    session_id VARCHAR(64)  NOT NULL,
    title      VARCHAR(120) NOT NULL,
    status     ENUM('active','archived') NOT NULL DEFAULT 'active',
    created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_conversations_user_session (user_id, session_id),
    KEY idx_conversations_user_updated (user_id, updated_at),
    CONSTRAINT fk_conversations_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Chat history, strictly ordered per conversation.
CREATE TABLE IF NOT EXISTS messages (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversation_id CHAR(36)     NOT NULL,
    sequence_number INT UNSIGNED NOT NULL,
    role            ENUM('user','assistant') NOT NULL,
    content         TEXT         NOT NULL,
    sources         JSON         NULL,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_messages_order (conversation_id, sequence_number),
    CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Agent memory for a conversation: the pending payment quote only. Never card data or credentials.
CREATE TABLE IF NOT EXISTS conversation_memory (
    conversation_id CHAR(36)  NOT NULL,
    state           JSON      NOT NULL,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (conversation_id),
    CONSTRAINT fk_memory_conversation FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
) ENGINE=InnoDB;

