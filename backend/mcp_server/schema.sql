-- Plan Assistant schema.
--
-- Participant identity and payments come from the BMOWNER legacy tables of the IWPRD ERD:
--   USER_ACCOUNT, EPAYMENT_METHOD, EPAYMENT.
-- A member is identified by USER_NAME, which equals TE_SUID (the participant id from MDM).
-- The app-owned tables (chat history, admins, agent flow, display profile) key on ACCOUNT_ID / TE_SUID.
-- Names and addresses are not in USER_ACCOUNT: they come from MDM, so the app keeps only a display profile.

-- Participant login and registration. From BMOWNER.USER_ACCOUNT (same columns).
-- STATUS_CODE: 1 active, 2 disabled, 3 incomplete. TYPE_CODE: 1 participant.
-- ENC_PASSWORD holds the login hash. Nothing here is MDM master data.
CREATE TABLE IF NOT EXISTS user_account (
    account_id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
    type_code              SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    status_code            SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    user_name              VARCHAR(64)  NULL,
    email_id               VARCHAR(64)  NULL,
    mobile_number          VARCHAR(16)  NULL,
    enc_password           VARCHAR(64)  NULL,
    enc_otp                VARCHAR(64)  NULL,
    invalid_password_count SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    invalid_otp_count      SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    created_by             VARCHAR(64)  NULL,
    created_date           DATETIME     NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by             VARCHAR(64)  NULL,
    updated_date           DATETIME     NULL,
    mobile_verified        CHAR(1)      NOT NULL DEFAULT 'N',
    email_verified         CHAR(1)      NOT NULL DEFAULT 'N',
    te_suid                VARCHAR(64)  NOT NULL,
    status_updated_date    DATETIME     NULL,
    spouse_grid            DECIMAL(38,0) NULL,
    PRIMARY KEY (account_id),
    UNIQUE KEY cons_user_account_te_suid_unique (te_suid),
    UNIQUE KEY uq_user_account_user_name (user_name)
) ENGINE=InnoDB;

-- App-owned display profile, keyed by participant id. Stands in for the MDM name/address data that the
-- legacy system reads elsewhere, so USER_ACCOUNT keeps only login fields.
CREATE TABLE IF NOT EXISTS users_profile (
    te_suid      VARCHAR(64)  NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    email        VARCHAR(255) NULL,
    created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (te_suid),
    CONSTRAINT fk_users_profile_account FOREIGN KEY (te_suid) REFERENCES user_account (te_suid) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Saved payment method. From BMOWNER.EPAYMENT_METHOD (same columns).
-- For cards ENC_TOKEN is the gateway token; real card numbers are never stored.
-- EPAY_MODE: CREDIT_CARD or ECHECK (ACH). STATUS: ACTIVE or DELETED.
CREATE TABLE IF NOT EXISTS epayment_method (
    epay_method_id   INT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_account_id  INT UNSIGNED NOT NULL,
    nickname         VARCHAR(128) NULL,
    epay_mode        VARCHAR(32)  NOT NULL,
    status           VARCHAR(16)  NOT NULL,
    enc_token        VARCHAR(128) NOT NULL,
    card_type        VARCHAR(32)  NULL,
    enc_last_four    VARCHAR(32)  NULL,
    enc_exp_mm_yyyy  VARCHAR(32)  NULL,
    enc_username     VARCHAR(128) NULL,
    created_date     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_date     DATETIME     NULL,
    last_used_date   DATETIME     NULL,
    cardholder_name  VARCHAR(128) NULL,
    tc_accepted_date DATETIME     NULL,
    PRIMARY KEY (epay_method_id),
    KEY epayment_method_c01 (user_account_id),
    CONSTRAINT fk_epayment_method_account FOREIGN KEY (user_account_id) REFERENCES user_account (account_id)
) ENGINE=InnoDB;

-- Payment staging table. From BMOWNER.EPAYMENT (same columns).
-- SRC: W web. EPMT_STATUS: P pending sync, S synced to the backend, F failed.
-- RESULT: A approved, D declined. CRCRD_TYPE: V Visa, M MasterCard, A Amex, D Discover.
-- A row is created when the member pays: P while it is staged, S once the backend has accepted it.
-- Due date is optional; if not provided, it's derived from EPMT_TIMSTMP (quarter billing period).
CREATE TABLE IF NOT EXISTS epayment (
    epmt_id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    te_suid          VARCHAR(64)  NOT NULL,
    src              CHAR(1)      NOT NULL,
    amt              DECIMAL(10,2) NOT NULL,
    crcrd_rsp_suid   INT UNSIGNED NULL,
    crcrd_cnf_nbr    VARCHAR(20)  NULL,
    crcrd_txn_id     VARCHAR(20)  NULL,
    epmt_timstmp     DATETIME     NOT NULL,
    due_date         DATE         NULL,
    epmt_status      CHAR(1)      NOT NULL,
    bat_nbr          VARCHAR(12)  NULL,
    gateway_suid     INT UNSIGNED NULL,
    gateway_rank     SMALLINT UNSIGNED NULL,
    result           CHAR(1)      NULL,
    bank_account_id  INT UNSIGNED NULL,
    echeck_cnf_nbr   VARCHAR(20)  NULL,
    internal_user_id VARCHAR(50)  NULL,
    extract_timstmp  DATETIME     NULL,
    crcrd_type       CHAR(1)      NULL,
    ems_payment_id   INT UNSIGNED NULL,
    response_message VARCHAR(1024) NULL,
    epay_method_id   INT UNSIGNED NULL,
    PRIMARY KEY (epmt_id),
    KEY epayment_c05 (epay_method_id),
    KEY idx_epayment_te_suid (te_suid, epmt_timstmp),
    KEY idx_epayment_status (epmt_status, result),
    CONSTRAINT fk_epayment_method FOREIGN KEY (epay_method_id) REFERENCES epayment_method (epay_method_id),
    CONSTRAINT fk_epayment_te_suid FOREIGN KEY (te_suid) REFERENCES user_account (te_suid)
) ENGINE=InnoDB;

-- A chat session. session_id is the client-facing id, unique per member so one member can never read another's.
CREATE TABLE IF NOT EXISTS conversations (
    id         CHAR(36)     NOT NULL,
    account_id INT UNSIGNED NOT NULL,
    session_id VARCHAR(64)  NOT NULL,
    title      VARCHAR(120) NOT NULL,
    status     ENUM('active','archived') NOT NULL DEFAULT 'active',
    created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_conversations_user_session (account_id, session_id),
    KEY idx_conversations_user_updated (account_id, updated_at),
    CONSTRAINT fk_conversations_account FOREIGN KEY (account_id) REFERENCES user_account (account_id) ON DELETE CASCADE
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

-- Administrators. A username listed in ADMIN_USERS is added here at startup, and membership here also grants admin access.
CREATE TABLE IF NOT EXISTS admins (
    account_id INT UNSIGNED NOT NULL,
    granted_by VARCHAR(50)  NOT NULL DEFAULT 'ADMIN_USERS',
    created_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id),
    CONSTRAINT fk_admins_account FOREIGN KEY (account_id) REFERENCES user_account (account_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- One row per chat question: who asked, which agent finally answered. Kept for audit, so no foreign key.
CREATE TABLE IF NOT EXISTS agent_turns (
    id           CHAR(36)     NOT NULL,
    username     VARCHAR(50)  NULL COMMENT 'NULL for an anonymous visitor',
    session_id   VARCHAR(64)  NULL,
    question     VARCHAR(2000) NOT NULL,
    flow         JSON          NULL COMMENT 'Ordered agent, cache and tool activity for this question',
    final_agent  VARCHAR(30)  NULL,
    sources      VARCHAR(500) NULL,
    duration_ms  INT UNSIGNED NOT NULL DEFAULT 0,
    created_at   TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_turns_created (created_at),
    KEY idx_turns_user_created (username, created_at)
) ENGINE=InnoDB;

-- The ordered flow of a turn: user -> supervisor -> assigned agent -> retrieval / MCP tool calls -> answer.
-- from_actor and to_actor say who handed what to whom, and tool_name is the function called (route_request, get_balance, ...).
CREATE TABLE IF NOT EXISTS agent_events (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    turn_id     CHAR(36)     NOT NULL,
    step        INT UNSIGNED NOT NULL,
    event_type  VARCHAR(20)  NOT NULL COMMENT 'question | route | retrieval | llm | tool_call | tool_result | join | answer | sign_in',
    from_actor  VARCHAR(30)  NOT NULL,
    to_actor    VARCHAR(30)  NOT NULL,
    tool_name   VARCHAR(50)  NULL,
    status      VARCHAR(30)  NULL,
    storage_backend VARCHAR(20) NULL COMMENT 'valkey | qdrant | mysql | none',
    detail      VARCHAR(500) NOT NULL DEFAULT '',
    elapsed_ms  INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'since the turn started',
    duration_ms INT UNSIGNED NULL,
    created_at  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uq_events_order (turn_id, step),
    KEY idx_events_tool (tool_name, created_at),
    CONSTRAINT fk_events_turn FOREIGN KEY (turn_id) REFERENCES agent_turns (id) ON DELETE CASCADE
) ENGINE=InnoDB;
