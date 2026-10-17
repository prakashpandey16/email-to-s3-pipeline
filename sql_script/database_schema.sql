-- =============================================================================
-- META-DRIVEN ETL FRAMEWORK — POSTGRES SCHEMA
-- Schema: email_to_s3_config
--
-- Table Creation Order (respecting FK dependencies):
--   1. source_config_table
--   2. target_config_table
--   3. batch_config_table
--   4. rule_set_config_table
--   5. email_rule_config_table
--   6. pipeline_config_table
--   7. metadata_config_table
--   8. pipeline_run_table
--   9. processed_files_log_table
--  10. error_log_table
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS email_to_s3_config;


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. SOURCE CONFIGURATION
--    Defines WHERE data comes from (e.g., Outlook mailbox details)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.source_config_table (
    id                  SERIAL          PRIMARY KEY,
    source_type         VARCHAR(50)     NOT NULL DEFAULT 'outlook',
    mailbox_address     VARCHAR(255)    NOT NULL,
    target_folder       VARCHAR(255)    NOT NULL DEFAULT 'LMD Managers',
    
    -- Azure AD / Authentication details (stored encrypted)
    azure_tenant_id     VARCHAR(500),
    azure_client_id     VARCHAR(500),
    azure_client_secret TEXT,
    
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_source_type
        CHECK (source_type IN ('outlook', 'gmail', 'imap')),

    CONSTRAINT chk_email_format
        CHECK (mailbox_address ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
);



-- ─────────────────────────────────────────────────────────────────────────────
-- 2. TARGET CONFIGURATION
--    Defines WHERE data goes (e.g., S3 bucket details)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS email_to_s3_config.target_config_table_id_seq;

CREATE TABLE IF NOT EXISTS email_to_s3_config.target_config_table (
    id                  INT             NOT NULL DEFAULT nextval('email_to_s3_config.target_config_table_id_seq'::regclass),
    target_type         VARCHAR(50)     NOT NULL DEFAULT 's3',
    bucket_name         VARCHAR(255)    NOT NULL,
    region              VARCHAR(50)     NOT NULL,
    
    -- AWS Authentication details (stored encrypted)
    aws_access_key_id     VARCHAR(500),
    aws_secret_access_key TEXT,
    
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. BATCH CONFIGURATION
--    Defines an executable batch (grouping of multiple metadata entries)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.batch_config_table (
    batch_id            SERIAL          PRIMARY KEY,
    batch_name          VARCHAR(100)    NOT NULL UNIQUE,
    start_time          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),  -- batch creation/registration time
    description         TEXT,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. RULE SET CONFIGURATION
--    Groups a collection of rules under one set_id.
--    A rule_set can be reused across multiple metadata entries.
--    e.g., rule_set_id=1 contains 100 Flipkart rules,
--          rule_set_id=2 could contain Myntra-specific rules.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.rule_set_config_table (
    rule_set_id         SERIAL          PRIMARY KEY,
    rule_set_name       VARCHAR(100)    NOT NULL UNIQUE,
    description         TEXT,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. EMAIL RULE CONFIGURATION
--    Individual matching rules belonging to a rule_set.
--    Determines how an email subject/filename maps to a target S3 path.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.email_rule_config_table (
    rule_id             SERIAL          PRIMARY KEY,
    rule_set_id         INT             NOT NULL
        REFERENCES email_to_s3_config.rule_set_config_table(rule_set_id) ON DELETE CASCADE,
    rule_type           VARCHAR(50)     NOT NULL
        CHECK (rule_type IN ('subject_filename', 'multi_sheet', 'regex', 'subject_only', 'filename_keyword')),
    subject_keyword     VARCHAR(255),
    filename_keyword    VARCHAR(255),
    regex_pattern       VARCHAR(500),
    target_path_suffix  VARCHAR(500)    NOT NULL,
    priority            INT             NOT NULL DEFAULT 1,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_priority
        CHECK (priority BETWEEN 1 AND 10)
);

CREATE INDEX IF NOT EXISTS idx_email_rule_config_ruleset
    ON email_to_s3_config.email_rule_config_table(rule_set_id, is_active, priority);


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. PIPELINE CONFIGURATION
--    Operational settings for the pipeline engine.
--    Controls timeouts, retries, concurrency, and file-processing policies.
--    Linked to batch so each batch can have its own tuning.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.pipeline_config_table (
    id                      SERIAL          PRIMARY KEY,
    batch_id                INT             NOT NULL UNIQUE
        REFERENCES email_to_s3_config.batch_config_table(batch_id) ON DELETE CASCADE,

    -- Concurrency
    max_workers             INT             NOT NULL DEFAULT 5,

    -- Connection / Fetch
    page_size               INT             NOT NULL DEFAULT 100,
    batch_size              INT             NOT NULL DEFAULT 25,
    max_folder_depth        INT             NOT NULL DEFAULT 10,

    -- Timeouts
    connection_timeout_sec  INT             NOT NULL DEFAULT 10,
    read_timeout_sec        INT             NOT NULL DEFAULT 30,

    -- Retry Policy
    max_retries             INT             NOT NULL DEFAULT 3,
    retry_delay_sec         NUMERIC(5,2)    NOT NULL DEFAULT 1.0,

    -- File Processing Policy
    max_extracted_size_mb   INT             NOT NULL DEFAULT 500,
    max_zip_depth           INT             NOT NULL DEFAULT 2,
    match_threshold         NUMERIC(3,2)    NOT NULL DEFAULT 0.60,
    strict_mode             BOOLEAN         NOT NULL DEFAULT TRUE,
    deep_scan               BOOLEAN         NOT NULL DEFAULT TRUE,
    download_url_pattern    VARCHAR(500)    NOT NULL DEFAULT 'https://storage\.googleapis\.com[^\s"''<>]+',
    duplicate_handle_policy VARCHAR(30)     NOT NULL DEFAULT 'MARK_READ_AND_SKIP'
        CHECK (duplicate_handle_policy IN ('MARK_READ_AND_SKIP', 'LEAVE_UNREAD')),
    junk_extensions         TEXT[]          DEFAULT ARRAY[
        '.png','.jpg','.jpeg','.gif','.bmp',
        '.mp3','.wav','.css','.js','.ico','.svg'
    ],

    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);


-- ─────────────────────────────────────────────────────────────────────────────
-- 7. METADATA CONFIGURATION
--    The central hub that connects Batch → Source → Target → RuleSet.
--    One batch can have MULTIPLE metadata rows (multiple source-target pairs).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.metadata_config_table (
    id                  SERIAL          PRIMARY KEY,
    batch_id            INT             NOT NULL
        REFERENCES email_to_s3_config.batch_config_table(batch_id) ON DELETE CASCADE,
    source_id           INT             NOT NULL
        REFERENCES email_to_s3_config.source_config_table(id),
    target_id           INT             NOT NULL
        REFERENCES email_to_s3_config.target_config_table(id),
    rule_set_id         INT             NOT NULL
        REFERENCES email_to_s3_config.rule_set_config_table(rule_set_id),
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_batch_ruleset UNIQUE (batch_id, rule_set_id)
);

CREATE INDEX IF NOT EXISTS idx_metadata_batch    ON email_to_s3_config.metadata_config_table(batch_id);
CREATE INDEX IF NOT EXISTS idx_metadata_source   ON email_to_s3_config.metadata_config_table(source_id);
CREATE INDEX IF NOT EXISTS idx_metadata_target   ON email_to_s3_config.metadata_config_table(target_id);
CREATE INDEX IF NOT EXISTS idx_metadata_ruleset  ON email_to_s3_config.metadata_config_table(rule_set_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- 8. PIPELINE RUNS
--    Tracks the execution of a batch run.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.pipeline_run_table (
    run_id              UUID            PRIMARY KEY,
    batch_id            INT
        REFERENCES email_to_s3_config.batch_config_table(batch_id),
    start_time          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    end_time            TIMESTAMPTZ,
    status              VARCHAR(50)     NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED', 'PARTIAL_SUCCESS')),
    metrics             JSONB,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_run_batch  ON email_to_s3_config.pipeline_run_table(batch_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_status ON email_to_s3_config.pipeline_run_table(status);


-- ─────────────────────────────────────────────────────────────────────────────
-- 9. PROCESSED FILES LOG
--    Idempotency tracking via SHA256 hash checks.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.processed_files_log_table (
    file_id             SERIAL          PRIMARY KEY,
    run_id              UUID            NOT NULL
        REFERENCES email_to_s3_config.pipeline_run_table(run_id) ON DELETE CASCADE,
    file_hash_sha256    VARCHAR(64)     NOT NULL,
    file_name           VARCHAR(255)    NOT NULL,
    target_key          VARCHAR(500)    NOT NULL,
    job_name            VARCHAR(100)    NOT NULL,
    processed_at        TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processed_files_hash
    ON email_to_s3_config.processed_files_log_table(file_hash_sha256, job_name);


-- ─────────────────────────────────────────────────────────────────────────────
-- 10. NOTIFICATION CONFIG
--     Email notification settings (SES) and reporting schedule per batch.
-- ─────────────────────────────────────────────────────────────────────────────
-- Sequence and defined type
CREATE SEQUENCE IF NOT EXISTS email_to_s3_config.notification_config_table_id_seq;

-- Table Definition
CREATE TABLE "email_to_s3_config"."notification_config_table" (
    "id" int4 NOT NULL DEFAULT nextval('email_to_s3_config.notification_config_table_id_seq'::regclass),
    "batch_id" int4 NOT NULL,
    "sender_email" varchar(255) NOT NULL,
    "success_recipient_email" varchar(500) NOT NULL,
    "failure_recipient_email" varchar(500) NOT NULL,
    "schedule_times" _text NOT NULL DEFAULT ARRAY['20:00'::text],
    "ses_access_key_id" varchar(500),
    "ses_secret_access_key" varchar(500),
    "aws_region" varchar(50),
    "is_active" bool NOT NULL DEFAULT true,
    "created_at" timestamptz NOT NULL DEFAULT now(),
    "updated_at" timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT "notification_config_table_batch_id_fkey" FOREIGN KEY ("batch_id") REFERENCES "email_to_s3_config"."batch_config_table"("batch_id") ON DELETE CASCADE,
    PRIMARY KEY ("id")
);

-- Indices
CREATE UNIQUE INDEX notification_config_table_batch_id_key ON email_to_s3_config.notification_config_table USING btree (batch_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 11. ERROR LOG
--     Detailed execution/error logs tied to a run.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_to_s3_config.error_log_table (
    log_id              SERIAL          PRIMARY KEY,
    run_id              UUID
        REFERENCES email_to_s3_config.pipeline_run_table(run_id) ON DELETE CASCADE,
    log_level           VARCHAR(20)     NOT NULL,
    message             TEXT            NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_error_log_run ON email_to_s3_config.error_log_table(run_id);


-- =============================================================================
-- SEED DATA — Migrate from existing etl_config.email_s3_pipeline_config
-- Run this ONCE after creating the schema above.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 1: SOURCE — Insert Outlook Source with encrypted Azure creds + PLAINTEXT mailbox
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO email_to_s3_config.source_config_table
    (source_type, mailbox_address, target_folder, azure_tenant_id, azure_client_id, azure_client_secret)
VALUES
    (
        'outlook', 
        'your.email@example.com', 
        'Target Folder', 
        'dummy_encrypted_tenant_id', 
        'dummy_encrypted_client_id', 
        'dummy_encrypted_client_secret'
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 2: TARGET — Insert S3 Target with encrypted AWS creds + PLAINTEXT bucket
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO email_to_s3_config.target_config_table
    (target_type, bucket_name, region, aws_access_key_id, aws_secret_access_key)
VALUES
    (
        's3', 
        'demo-bucket-name', 
        'us-east-1',         
        'dummy_encrypted_aws_access_key', 
        'dummy_encrypted_aws_secret_key'
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 3: RULE SET — Create a named group for rules
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO email_to_s3_config.rule_set_config_table (rule_set_name, description)
VALUES ('example_rules', 'Example rule set for email ingestion');

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 4: BATCH — Define the executable batch
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO email_to_s3_config.batch_config_table (batch_name, description)
VALUES ('example_batch', 'Example email-to-S3 ingestion batch');

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 5: PIPELINE CONFIG — Operational tuning for this batch
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO email_to_s3_config.pipeline_config_table (
    batch_id, max_workers, page_size, batch_size, max_folder_depth,
    connection_timeout_sec, read_timeout_sec, max_retries, retry_delay_sec,
    max_extracted_size_mb, max_zip_depth, match_threshold, strict_mode,
    deep_scan, download_url_pattern, duplicate_handle_policy, junk_extensions
)
SELECT
    b.batch_id, 5, 100, 25, 10,
    10, 30, 3, 1.0,
    500, 2, 0.60, TRUE,
    TRUE, 'https://storage\.googleapis\.com[^\s"''<>]+', 'MARK_READ_AND_SKIP',
    ARRAY['.png','.jpg','.jpeg','.gif','.bmp','.mp3','.wav','.css','.js','.ico','.svg']
FROM email_to_s3_config.batch_config_table b
WHERE b.batch_name = 'example_batch';

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 6: METADATA — Connect batch → source → target → ruleset
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO email_to_s3_config.metadata_config_table (batch_id, source_id, target_id, rule_set_id)
SELECT
    b.batch_id, s.id, t.id, rs.rule_set_id
FROM email_to_s3_config.batch_config_table    b
CROSS JOIN email_to_s3_config.source_config_table  s
CROSS JOIN email_to_s3_config.target_config_table  t
CROSS JOIN email_to_s3_config.rule_set_config_table rs
WHERE b.batch_name    = 'example_batch'
  AND rs.rule_set_name = 'example_rules'
  AND s.is_active = TRUE
  AND t.is_active = TRUE;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 7: RULES — Sample generic rules
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE v_rsid INT;
BEGIN
    SELECT rule_set_id INTO v_rsid FROM email_to_s3_config.rule_set_config_table WHERE rule_set_name = 'example_rules';

    INSERT INTO email_to_s3_config.email_rule_config_table
        (rule_set_id, rule_type, subject_keyword, filename_keyword, target_path_suffix, priority)
    VALUES
        (v_rsid, 'subject_filename', 'Sales Report', 'Daily_Sales', 'data/sales/', 1),
        (v_rsid, 'multi_sheet', NULL, 'Monthly_Finance_Data', 'data/finance/', 1),
        (v_rsid, 'subject_only', 'Urgent Alerts', NULL, 'data/alerts/', 2);
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 8: NOTIFICATION CONFIG — Schedule an 8:00 PM email for this batch
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO email_to_s3_config.notification_config_table (
    batch_id, sender_email, success_recipient_email, failure_recipient_email, schedule_times, 
    ses_access_key_id, ses_secret_access_key, aws_region
)
SELECT 
    batch_id,
    'noreply@example.com',
    'reports@example.com',
    'alerts@example.com',
    ARRAY['20:00'::text],
    'dummy_encrypted_ses_key',
    'dummy_encrypted_ses_secret',
    'us-east-1'
FROM email_to_s3_config.batch_config_table 
WHERE batch_name = 'example_batch'
ON CONFLICT (batch_id) DO NOTHING;