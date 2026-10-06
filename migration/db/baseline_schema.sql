--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: detect_stale_chunks(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.detect_stale_chunks(heartbeat_threshold_seconds integer DEFAULT 120) RETURNS TABLE(chunk_id uuid, job_id uuid, table_name character varying)
    LANGUAGE plpgsql
    AS $$

BEGIN

    RETURN QUERY

    SELECT 

        id,

        job_id,

        table_name::VARCHAR

    FROM migration_chunks

    WHERE status = 'running'

    AND last_heartbeat < NOW() - (heartbeat_threshold_seconds || ' seconds')::INTERVAL;

END;

$$;


--
-- Name: update_job_counters(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_job_counters() RETURNS trigger
    LANGUAGE plpgsql
    AS $$

BEGIN

    -- Update job totals when chunk status changes

    IF TG_OP = 'UPDATE' AND OLD.status != NEW.status THEN

        UPDATE migration_jobs

        SET 

            completed_chunks = (

                SELECT COUNT(*) FROM migration_chunks 

                WHERE job_id = NEW.job_id AND status = 'completed'

            ),

            failed_chunks = (

                SELECT COUNT(*) FROM migration_chunks 

                WHERE job_id = NEW.job_id AND status = 'failed'

            )

        WHERE id = NEW.job_id;

        

        -- Update table counters

        UPDATE migration_tables

        SET 

            completed_chunks = (

                SELECT COUNT(*) FROM migration_chunks 

                WHERE table_id = NEW.table_id AND status = 'completed'

            ),

            failed_chunks = (

                SELECT COUNT(*) FROM migration_chunks 

                WHERE table_id = NEW.table_id AND status = 'failed'

            ),

            updated_at = NOW()

        WHERE id = NEW.table_id;

    END IF;

    

    RETURN NEW;

END;

$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: adaptive_chunk_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adaptive_chunk_configs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    table_name character varying(255) NOT NULL,
    row_count bigint,
    avg_row_size_bytes integer,
    pk_min bigint,
    pk_max bigint,
    pk_distribution character varying(50) DEFAULT 'sequential'::character varying,
    computed_chunk_size bigint NOT NULL,
    computed_chunk_count integer NOT NULL,
    strategy_used character varying(100),
    estimated_duration_sec integer,
    memory_estimate_mb integer,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    user_id uuid,
    key_name character varying(200) NOT NULL,
    key_hash text NOT NULL,
    key_prefix character varying(20) NOT NULL,
    scopes text[] DEFAULT ARRAY['read'::text],
    last_used_at timestamp with time zone,
    expires_at timestamp with time zone,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    revoked_at timestamp with time zone,
    role character varying(100) DEFAULT 'api_client'::character varying,
    is_active boolean DEFAULT true
);


--
-- Name: TABLE api_keys; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.api_keys IS 'API keys for programmatic access to the platform';


--
-- Name: COLUMN api_keys.key_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.key_hash IS 'SHA-256 hash of the actual API key (never store plaintext)';


--
-- Name: assessment_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assessment_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    connection_id uuid,
    schema_version_id uuid,
    complexity character varying(20) NOT NULL,
    risk_level character varying(20) NOT NULL,
    total_tables integer DEFAULT 0,
    total_rows bigint DEFAULT 0,
    total_size_gb numeric(12,3) DEFAULT 0,
    estimated_duration character varying(100),
    recommended_workers integer DEFAULT 4,
    recommended_chunk_strategy character varying(100),
    blocking_issues jsonb DEFAULT '[]'::jsonb,
    warnings jsonb DEFAULT '[]'::jsonb,
    recommendations jsonb DEFAULT '[]'::jsonb,
    table_breakdown jsonb DEFAULT '[]'::jsonb,
    full_report jsonb DEFAULT '{}'::jsonb,
    generated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    user_id uuid,
    action character varying(100) NOT NULL,
    resource_type character varying(100),
    resource_id uuid,
    details jsonb,
    ip_address inet,
    user_agent text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    status character varying(20) DEFAULT 'success'::character varying,
    error_message text
);


--
-- Name: TABLE audit_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.audit_logs IS 'Comprehensive audit trail for compliance and security';


--
-- Name: batch_size_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.batch_size_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    worker_id character varying(100) NOT NULL,
    chunk_id uuid,
    old_batch_size integer NOT NULL,
    new_batch_size integer NOT NULL,
    avg_latency_ms integer NOT NULL,
    target_latency_ms integer NOT NULL,
    adjustment_reason character varying(255),
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: TABLE batch_size_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.batch_size_history IS 'Adaptive batch sizing history for performance tuning';


--
-- Name: benchmark_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.benchmark_records (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid,
    tenant_id uuid,
    source_engine character varying(50),
    target_engine character varying(50),
    worker_count integer,
    chunk_strategy character varying(100),
    total_rows bigint,
    total_size_gb numeric(12,3),
    duration_sec integer,
    avg_rows_per_sec bigint,
    avg_mb_per_sec numeric(10,2),
    peak_rows_per_sec bigint,
    min_rows_per_sec bigint,
    avg_chunk_duration_ms integer,
    failed_chunks integer DEFAULT 0,
    retried_chunks integer DEFAULT 0,
    table_benchmarks jsonb DEFAULT '[]'::jsonb,
    environment_info jsonb DEFAULT '{}'::jsonb,
    recorded_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: cdc_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cdc_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    event_type character varying(20) NOT NULL,
    table_name character varying(255) NOT NULL,
    event_position character varying(255),
    before_image jsonb,
    after_image jsonb,
    pk_values jsonb,
    replayed boolean DEFAULT false,
    replayed_at timestamp without time zone,
    replay_error text,
    captured_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: cdc_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cdc_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    source_db_type character varying(50) NOT NULL,
    status character varying(50) DEFAULT 'initializing'::character varying,
    capture_method character varying(50),
    initial_load_done boolean DEFAULT false,
    initial_load_completed_at timestamp without time zone,
    capture_started_at timestamp without time zone,
    last_captured_at timestamp without time zone,
    last_replayed_at timestamp without time zone,
    binlog_file character varying(255),
    binlog_position bigint,
    wal_lsn character varying(100),
    last_event_id character varying(255),
    events_captured bigint DEFAULT 0,
    events_replayed bigint DEFAULT 0,
    events_pending bigint DEFAULT 0,
    lag_seconds integer DEFAULT 0,
    error_message text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: chunk_execution_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunk_execution_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    chunk_id uuid NOT NULL,
    worker_id character varying(100),
    attempt_number integer NOT NULL,
    status character varying(30) NOT NULL,
    rows_processed bigint DEFAULT 0,
    source_row_count bigint,
    target_row_count bigint,
    duration_ms bigint,
    error_message text,
    started_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: TABLE chunk_execution_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.chunk_execution_log IS 'Audit trail for all chunk execution attempts (Phase 2)';


--
-- Name: connection_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connection_registry (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    name character varying(255) NOT NULL,
    db_type character varying(50) NOT NULL,
    host character varying(500) NOT NULL,
    port integer NOT NULL,
    database_name character varying(255) NOT NULL,
    username character varying(255) NOT NULL,
    encrypted_password text NOT NULL,
    encryption_key_id character varying(100) DEFAULT 'local'::character varying,
    ssl_enabled boolean DEFAULT false,
    ssl_ca_cert text,
    ssl_client_cert text,
    ssl_client_key text,
    connection_pool_size integer DEFAULT 5,
    connect_timeout integer DEFAULT 30,
    query_timeout integer DEFAULT 300,
    extra_params jsonb DEFAULT '{}'::jsonb,
    last_tested_at timestamp without time zone,
    last_test_status character varying(50),
    last_test_error text,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: connector_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connector_registry (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    display_name character varying(255) NOT NULL,
    version character varying(50),
    db_type character varying(50) NOT NULL,
    capabilities jsonb DEFAULT '[]'::jsonb NOT NULL,
    supported_versions jsonb DEFAULT '[]'::jsonb,
    config_schema jsonb DEFAULT '{}'::jsonb,
    is_active boolean DEFAULT true,
    is_builtin boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: constraint_mapping_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.constraint_mapping_plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id uuid NOT NULL,
    source_table character varying(255) NOT NULL,
    target_table character varying(255) NOT NULL,
    create_ddl text,
    index_ddl jsonb,
    fk_ddl jsonb,
    unique_ddl jsonb,
    conflicts jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: cutover_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cutover_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    step character varying(100) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying,
    details text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: data_quality_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_quality_results (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    connection_id uuid,
    table_name character varying(255) NOT NULL,
    check_type character varying(100) NOT NULL,
    severity character varying(20) NOT NULL,
    affected_count bigint DEFAULT 0,
    affected_pct numeric(6,3) DEFAULT 0,
    sample_values jsonb DEFAULT '[]'::jsonb,
    details text,
    recommendation text,
    scanned_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: datatype_conversion_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.datatype_conversion_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'global'::character varying,
    source_db character varying(50) DEFAULT 'any'::character varying,
    target_db character varying(50) DEFAULT 'any'::character varying,
    source_type character varying(100) NOT NULL,
    target_type character varying(100) NOT NULL,
    safety character varying(20) NOT NULL,
    cast_template character varying(500),
    notes text,
    is_system boolean DEFAULT true
);


--
-- Name: event_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying,
    event_type character varying(150) NOT NULL,
    source_service character varying(100),
    resource_type character varying(100),
    resource_id character varying(255),
    payload jsonb DEFAULT '{}'::jsonb,
    correlation_id character varying(255),
    published_at timestamp without time zone DEFAULT now() NOT NULL,
    delivered_count integer DEFAULT 0
);


--
-- Name: event_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_subscriptions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subscriber_name character varying(150) NOT NULL,
    event_pattern character varying(150) NOT NULL,
    handler_path character varying(500),
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: generated_scripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.generated_scripts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id uuid NOT NULL,
    script_type character varying(50),
    target_table character varying(255),
    content text NOT NULL,
    filename character varying(500),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: intelligence_scan_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.intelligence_scan_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    connection_id uuid,
    schema_version_id uuid,
    status character varying(50) DEFAULT 'pending'::character varying,
    tables_total integer DEFAULT 0,
    tables_scanned integer DEFAULT 0,
    tables_failed integer DEFAULT 0,
    catalog_types jsonb DEFAULT '["statistics", "relationship", "distribution", "lob_detection", "compression", "hot_cold", "growth_rate"]'::jsonb,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    error_summary text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    table_results jsonb DEFAULT '[]'::jsonb
);


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    invoice_number character varying(50) NOT NULL,
    billing_period_start date NOT NULL,
    billing_period_end date NOT NULL,
    subtotal numeric(10,2) DEFAULT 0.00,
    tax numeric(10,2) DEFAULT 0.00,
    total numeric(10,2) NOT NULL,
    currency character varying(3) DEFAULT 'USD'::character varying,
    status character varying(20) DEFAULT 'pending'::character varying,
    due_date date NOT NULL,
    paid_at timestamp with time zone,
    line_items jsonb DEFAULT '[]'::jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE invoices; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.invoices IS 'Monthly invoices with line items for billing';


--
-- Name: migration_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_jobs (
    id uuid NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    status character varying(30) NOT NULL,
    source_config jsonb NOT NULL,
    target_config jsonb NOT NULL,
    total_tables integer DEFAULT 0,
    total_chunks integer DEFAULT 0,
    completed_chunks integer DEFAULT 0,
    failed_chunks integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    last_error text,
    failure_threshold_percent integer DEFAULT 5,
    auto_failed_at timestamp without time zone,
    avg_throughput_rows_per_sec numeric(15,2),
    peak_memory_mb integer,
    total_bytes_migrated bigint,
    optimization_method character varying(100) DEFAULT 'standard'::character varying,
    lifecycle_status character varying(20) DEFAULT 'active'::character varying,
    paused_at timestamp with time zone,
    paused_by uuid,
    cancelled_at timestamp with time zone,
    cancelled_by uuid,
    cancellation_reason text,
    estimated_cost numeric(10,2),
    actual_cost numeric(10,2),
    source_connection_id uuid,
    target_connection_id uuid,
    dependency_graph_built boolean DEFAULT false,
    rollback_plan_id uuid,
    throttle_active boolean DEFAULT false,
    max_workers integer DEFAULT 4,
    approved_by character varying(255),
    approved_at timestamp without time zone,
    name character varying(255),
    updated_at timestamp without time zone DEFAULT now(),
    error_message text,
    CONSTRAINT migration_jobs_status_check CHECK (((status)::text = ANY (ARRAY['pending'::text, 'planning'::text, 'queued'::text, 'awaiting_approval'::text, 'running'::text, 'paused'::text, 'validating'::text, 'completed'::text, 'failed'::text, 'cancelled'::text, 'rejected'::text, 'rolled_back'::text])))
);


--
-- Name: COLUMN migration_jobs.name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.migration_jobs.name IS 'Human-readable job name set at creation time via the New Migration wizard. Nullable for jobs created before this column existed.';


--
-- Name: job_health_view; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.job_health_view AS
 SELECT id AS job_id,
    status,
    total_chunks,
    completed_chunks,
    failed_chunks,
        CASE
            WHEN (total_chunks > 0) THEN round((((failed_chunks)::numeric / (total_chunks)::numeric) * (100)::numeric), 2)
            ELSE (0)::numeric
        END AS failure_rate_percent,
        CASE
            WHEN (total_chunks > 0) THEN round((((completed_chunks)::numeric / (total_chunks)::numeric) * (100)::numeric), 2)
            ELSE (0)::numeric
        END AS completion_percent,
    created_at,
    started_at,
    completed_at
   FROM public.migration_jobs j;


--
-- Name: knowledge_base; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_base (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    job_id uuid,
    source_engine character varying(50),
    target_engine character varying(50),
    entry_type character varying(100) NOT NULL,
    title character varying(500) NOT NULL,
    content jsonb DEFAULT '{}'::jsonb NOT NULL,
    tags text[] DEFAULT '{}'::text[],
    usefulness_score double precision DEFAULT 0.0,
    reference_count integer DEFAULT 0,
    is_public boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: maintenance_mode; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.maintenance_mode (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    is_active boolean DEFAULT false,
    reason text,
    activated_by uuid,
    activated_at timestamp without time zone,
    deactivated_at timestamp without time zone,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: mapping_projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mapping_projects (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    source_schema_id uuid,
    target_schema_id uuid,
    status character varying(50) DEFAULT 'draft'::character varying,
    dry_run_result jsonb,
    migration_plan jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    current_phase integer DEFAULT 0,
    execution_started_at timestamp without time zone,
    execution_completed_at timestamp without time zone
);


--
-- Name: masking_job_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.masking_job_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid,
    rule_set_id uuid,
    table_name character varying(255),
    column_name character varying(255),
    strategy character varying(100),
    rows_masked bigint DEFAULT 0,
    rows_skipped bigint DEFAULT 0,
    applied_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: masking_rule_sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.masking_rule_sets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: masking_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.masking_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    rule_set_id uuid,
    table_name character varying(255) NOT NULL,
    column_name character varying(255) NOT NULL,
    strategy character varying(100) NOT NULL,
    strategy_config jsonb DEFAULT '{}'::jsonb,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: metadata_catalog; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.metadata_catalog (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying,
    connection_id uuid,
    schema_version_id uuid,
    table_name character varying(255) NOT NULL,
    catalog_type character varying(100) NOT NULL,
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    computed_at timestamp without time zone DEFAULT now() NOT NULL,
    expires_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: migration_approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_approvals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    tenant_id uuid,
    requested_by_id uuid,
    reviewed_by_id uuid,
    status character varying(50) DEFAULT 'pending'::character varying,
    notes text,
    requested_at timestamp without time zone DEFAULT now() NOT NULL,
    reviewed_at timestamp without time zone,
    auto_approved boolean DEFAULT false
);


--
-- Name: migration_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_chunks (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    table_id uuid NOT NULL,
    table_name character varying(255) NOT NULL,
    pk_start bigint NOT NULL,
    pk_end bigint NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    retry_count integer DEFAULT 0,
    max_retries integer DEFAULT 3,
    rows_processed bigint DEFAULT 0,
    checksum character varying(128),
    duration_ms bigint,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    last_heartbeat timestamp without time zone,
    last_error text,
    created_at timestamp without time zone DEFAULT now(),
    worker_id character varying(100),
    next_retry_at timestamp without time zone,
    source_row_count bigint,
    target_row_count bigint,
    validation_status character varying(30) DEFAULT 'pending'::character varying,
    throughput_rows_per_sec numeric(15,2),
    throughput_mb_per_sec numeric(10,2),
    memory_peak_mb integer,
    insert_latency_ms integer,
    batch_size_used integer DEFAULT 5000,
    bulk_insert_method character varying(50) DEFAULT 'standard'::character varying,
    CONSTRAINT migration_chunks_status_check CHECK (((status)::text = ANY (ARRAY['pending'::text, 'assigned'::text, 'running'::text, 'retrying'::text, 'completed'::text, 'failed'::text, 'skipped'::text, 'cancelled'::text]))),
    CONSTRAINT migration_chunks_validation_status_check CHECK (((validation_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('validated'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: migration_execution_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_execution_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id uuid NOT NULL,
    phase integer NOT NULL,
    phase_name character varying(100),
    status character varying(50) DEFAULT 'pending'::character varying,
    ddl_executed text,
    error_message text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: migration_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    job_id uuid,
    report_type character varying(100) NOT NULL,
    format character varying(20) DEFAULT 'json'::character varying,
    title character varying(255) NOT NULL,
    content jsonb DEFAULT '{}'::jsonb,
    file_path text,
    generated_by character varying(255),
    generated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: migration_tables; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_tables (
    id uuid NOT NULL,
    job_id uuid NOT NULL,
    table_name character varying(255) NOT NULL,
    primary_key_column character varying(255),
    total_rows bigint,
    total_chunks integer DEFAULT 0,
    completed_chunks integer DEFAULT 0,
    failed_chunks integer DEFAULT 0,
    status character varying(30) DEFAULT 'pending'::character varying,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    execution_order integer DEFAULT 0,
    depth_level integer DEFAULT 0,
    depends_on jsonb DEFAULT '[]'::jsonb,
    computed_chunk_size bigint DEFAULT 100000,
    avg_row_size_bytes integer,
    CONSTRAINT migration_tables_status_check CHECK (((status)::text = ANY (ARRAY['pending'::text, 'running'::text, 'completed'::text, 'failed'::text, 'skipped'::text])))
);


--
-- Name: migration_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.migration_templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    source_db_type character varying(50),
    target_db_type character varying(50),
    table_mappings jsonb DEFAULT '{}'::jsonb,
    chunk_config jsonb DEFAULT '{}'::jsonb,
    validation_rules jsonb DEFAULT '[]'::jsonb,
    execution_config jsonb DEFAULT '{}'::jsonb,
    tags jsonb DEFAULT '[]'::jsonb,
    is_public boolean DEFAULT false,
    usage_count integer DEFAULT 0,
    created_by_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: operations_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operations_actions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    operator_id uuid,
    action_type character varying(100) NOT NULL,
    resource_type character varying(50),
    resource_id character varying(255),
    before_state jsonb DEFAULT '{}'::jsonb,
    after_state jsonb DEFAULT '{}'::jsonb,
    reason text,
    status character varying(50) DEFAULT 'completed'::character varying,
    error_message text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: password_reset_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_reset_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash character varying(255) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    used_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: performance_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.performance_metrics (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    metric_timestamp timestamp without time zone DEFAULT now() NOT NULL,
    rows_per_second numeric(15,2),
    mb_per_second numeric(10,2),
    chunks_per_minute integer,
    active_workers integer,
    queue_depth integer,
    memory_usage_mb integer,
    cpu_usage_percent numeric(5,2),
    source_db_latency_ms integer,
    target_db_latency_ms integer,
    insert_latency_ms integer,
    worker_id character varying(100),
    current_batch_size integer,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: TABLE performance_metrics; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.performance_metrics IS 'Time-series performance tracking for observability';


--
-- Name: plugin_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plugin_registry (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'global'::character varying,
    plugin_type character varying(50) NOT NULL,
    name character varying(100) NOT NULL,
    display_name character varying(255) NOT NULL,
    version character varying(50) DEFAULT '1.0.0'::character varying,
    capabilities jsonb DEFAULT '[]'::jsonb,
    config_schema jsonb DEFAULT '{}'::jsonb,
    module_path character varying(500),
    is_active boolean DEFAULT true,
    is_builtin boolean DEFAULT true,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: policy_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.policy_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    name character varying(255) NOT NULL,
    policy_type character varying(100) NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: rate_limit_tracking; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rate_limit_tracking (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    endpoint character varying(200) NOT NULL,
    window_start timestamp with time zone NOT NULL,
    window_duration_seconds integer DEFAULT 60,
    request_count integer DEFAULT 1,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE rate_limit_tracking; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.rate_limit_tracking IS 'Per-tenant API rate limiting enforcement';


--
-- Name: realtime_performance; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.realtime_performance AS
 SELECT j.id AS job_id,
    j.status,
    round(avg(c.throughput_rows_per_sec), 2) AS current_avg_throughput,
    max(c.throughput_rows_per_sec) AS peak_throughput,
    j.completed_chunks,
    j.total_chunks,
    round((((j.completed_chunks)::numeric / (NULLIF(j.total_chunks, 0))::numeric) * (100)::numeric), 2) AS completion_percent,
    max(c.memory_peak_mb) AS peak_memory_mb,
    avg(c.insert_latency_ms) AS avg_insert_latency_ms,
    count(
        CASE
            WHEN ((c.status)::text = 'running'::text) THEN 1
            ELSE NULL::integer
        END) AS currently_running,
        CASE
            WHEN ((j.completed_chunks > 0) AND (j.started_at IS NOT NULL)) THEN round((EXTRACT(epoch FROM (now() - (j.started_at)::timestamp with time zone)) / (60)::numeric), 1)
            ELSE (0)::numeric
        END AS elapsed_minutes,
        CASE
            WHEN ((j.completed_chunks > 0) AND (j.started_at IS NOT NULL)) THEN round((((EXTRACT(epoch FROM (now() - (j.started_at)::timestamp with time zone)) / (j.completed_chunks)::numeric) * ((j.total_chunks - j.completed_chunks))::numeric) / (60)::numeric), 1)
            ELSE NULL::numeric
        END AS eta_minutes
   FROM (public.migration_jobs j
     LEFT JOIN public.migration_chunks c ON ((c.job_id = j.id)))
  WHERE ((j.status)::text = ANY (ARRAY[('running'::character varying)::text, ('planning'::character varying)::text]))
  GROUP BY j.id, j.status, j.completed_chunks, j.total_chunks, j.started_at;


--
-- Name: resource_governor_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.resource_governor_state (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    recorded_at timestamp without time zone DEFAULT now() NOT NULL,
    source_db_cpu_pct numeric(5,2),
    source_db_conn_count integer,
    target_db_cpu_pct numeric(5,2),
    target_db_conn_count integer,
    worker_count_active integer,
    redis_queue_depth integer,
    rows_per_sec bigint,
    throttle_applied boolean DEFAULT false,
    throttle_reason character varying(255)
);


--
-- Name: role_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_definitions (
    role_name character varying(50) NOT NULL,
    display_name character varying(100) NOT NULL,
    description text,
    permissions text[] DEFAULT '{}'::text[] NOT NULL,
    rank integer NOT NULL
);


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    permissions jsonb DEFAULT '[]'::jsonb NOT NULL,
    is_system boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: rollback_execution_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rollback_execution_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    rollback_plan_id uuid NOT NULL,
    step_number integer NOT NULL,
    step_type character varying(100),
    table_name character varying(255),
    status character varying(50) DEFAULT 'pending'::character varying,
    sql_executed text,
    rows_affected bigint,
    error_message text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: rollback_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rollback_plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    status character varying(50) DEFAULT 'ready'::character varying,
    rollback_steps jsonb NOT NULL,
    checkpoint_data jsonb DEFAULT '{}'::jsonb,
    tables_affected jsonb DEFAULT '[]'::jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    executed_at timestamp without time zone,
    completed_at timestamp without time zone,
    error_message text
);


--
-- Name: schedule_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scheduled_job_id uuid NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    triggered_by character varying(100) DEFAULT 'scheduler'::character varying,
    status character varying(50) DEFAULT 'running'::character varying,
    migration_job_id uuid,
    started_at timestamp without time zone DEFAULT now() NOT NULL,
    completed_at timestamp without time zone,
    error_message text,
    result_summary jsonb DEFAULT '{}'::jsonb
);


--
-- Name: scheduled_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scheduled_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    job_type character varying(100) NOT NULL,
    cron_expression character varying(100),
    timezone character varying(100) DEFAULT 'UTC'::character varying,
    job_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    require_approval boolean DEFAULT false,
    is_active boolean DEFAULT true,
    last_run_at timestamp without time zone,
    next_run_at timestamp without time zone,
    last_status character varying(50),
    run_count integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: schema_column_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_column_mappings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    table_mapping_id uuid NOT NULL,
    source_table character varying(255),
    source_column character varying(255),
    source_type character varying(100),
    target_table character varying(255),
    target_column character varying(255),
    target_type character varying(100),
    mapping_kind character varying(50) DEFAULT 'direct'::character varying,
    mapping_config jsonb,
    conversion_safety character varying(20),
    requires_cast boolean DEFAULT false,
    cast_expression character varying(500),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: schema_diff_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_diff_cache (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_version_id uuid NOT NULL,
    target_version_id uuid NOT NULL,
    diff_result jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: schema_drift_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_drift_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    table_name character varying(255) NOT NULL,
    drift_type character varying(100) NOT NULL,
    column_name character varying(255),
    old_definition jsonb,
    new_definition jsonb,
    severity character varying(20) DEFAULT 'warning'::character varying,
    detected_at timestamp without time zone DEFAULT now() NOT NULL,
    action_taken character varying(100) DEFAULT 'paused'::character varying,
    resolved_at timestamp without time zone,
    notes text
);


--
-- Name: schema_recommendations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_recommendations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id uuid NOT NULL,
    rec_type character varying(50),
    source_ref character varying(500),
    target_ref character varying(500),
    confidence double precision,
    reason character varying(200),
    accepted boolean,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: schema_table_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_table_mappings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id uuid NOT NULL,
    mapping_type character varying(20) DEFAULT 'single'::character varying NOT NULL,
    source_tables jsonb NOT NULL,
    target_tables jsonb NOT NULL,
    join_condition text,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    source_db character varying(50) DEFAULT 'mysql'::character varying,
    target_db character varying(50) DEFAULT 'mysql'::character varying
);


--
-- Name: schema_validation_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_validation_results (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id uuid NOT NULL,
    validation_type character varying(100),
    source_table character varying(255),
    target_table character varying(255),
    source_value text,
    target_value text,
    passed boolean,
    details text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    sample_rows jsonb,
    business_rule_name character varying(255)
);


--
-- Name: schema_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    name character varying(255) NOT NULL,
    db_type character varying(50) NOT NULL,
    version_label character varying(100),
    schema_data jsonb NOT NULL,
    source_type character varying(50) DEFAULT 'live_db'::character varying,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: secrets_vault; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.secrets_vault (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    secret_type character varying(50) NOT NULL,
    secret_name character varying(200) NOT NULL,
    encrypted_value text NOT NULL,
    encryption_key_id character varying(100) NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_by uuid,
    last_accessed_at timestamp with time zone,
    access_count integer DEFAULT 0,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    key_name character varying(255) NOT NULL,
    description text,
    created_by_id uuid
);


--
-- Name: TABLE secrets_vault; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.secrets_vault IS 'Encrypted storage for database credentials and API keys';


--
-- Name: COLUMN secrets_vault.encrypted_value; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.secrets_vault.encrypted_value IS 'AES-256-GCM encrypted credential value';


--
-- Name: self_tuning_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.self_tuning_actions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    action_type character varying(100) NOT NULL,
    before_value jsonb DEFAULT '{}'::jsonb NOT NULL,
    after_value jsonb DEFAULT '{}'::jsonb NOT NULL,
    reason text,
    triggered_by character varying(100),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: service_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_registry (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    service_name character varying(100) NOT NULL,
    display_name character varying(255) NOT NULL,
    base_url character varying(500) NOT NULL,
    health_endpoint character varying(255) DEFAULT '/health'::character varying,
    version character varying(50),
    status character varying(50) DEFAULT 'unknown'::character varying,
    last_heartbeat timestamp without time zone,
    metadata jsonb DEFAULT '{}'::jsonb,
    registered_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: simulation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.simulation_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'local'::character varying NOT NULL,
    connection_id uuid,
    name character varying(255),
    worker_count integer NOT NULL,
    chunk_size_strategy character varying(100) DEFAULT 'size_based'::character varying,
    chunk_size_override integer,
    source_engine character varying(50) DEFAULT 'mysql'::character varying,
    target_engine character varying(50) DEFAULT 'mysql'::character varying,
    estimated_duration_sec bigint,
    estimated_duration_str character varying(100),
    estimated_rows_per_sec bigint,
    estimated_mb_per_sec numeric(10,2),
    estimated_cpu_pct numeric(5,2),
    estimated_network_gb numeric(10,3),
    estimated_target_storage_gb numeric(12,3),
    failure_probability_pct numeric(5,2),
    bottleneck character varying(100),
    table_breakdown jsonb DEFAULT '[]'::jsonb,
    recommendations jsonb DEFAULT '[]'::jsonb,
    data_source character varying(50) DEFAULT 'metadata_catalog'::character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: table_constraints_backup; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.table_constraints_backup (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    table_name character varying(255) NOT NULL,
    constraint_type character varying(50) NOT NULL,
    constraint_name character varying(255) NOT NULL,
    constraint_definition text NOT NULL,
    dropped_at timestamp without time zone,
    restored_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: TABLE table_constraints_backup; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.table_constraints_backup IS 'Backup of dropped constraints for bulk insert optimization';


--
-- Name: table_dependency_graph; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.table_dependency_graph (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    table_name character varying(255) NOT NULL,
    depends_on jsonb DEFAULT '[]'::jsonb,
    depth_level integer DEFAULT 0,
    execution_order integer,
    can_parallel boolean DEFAULT true,
    status character varying(50) DEFAULT 'pending'::character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: table_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.table_mappings (
    id uuid NOT NULL,
    table_id uuid,
    mapping_type character varying,
    source character varying,
    target character varying,
    config json
);


--
-- Name: tenant_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    display_name character varying(200) NOT NULL,
    description text,
    price_monthly numeric(10,2) DEFAULT 0.00,
    price_per_gb numeric(10,4) DEFAULT 0.00,
    max_concurrent_jobs integer DEFAULT 1,
    max_workers_per_job integer DEFAULT 4,
    max_gb_per_month integer DEFAULT 10,
    max_tables_per_job integer DEFAULT 50,
    api_rate_limit_per_minute integer DEFAULT 60,
    support_level character varying(50) DEFAULT 'community'::character varying,
    features jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE tenant_plans; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tenant_plans IS 'Subscription plans with resource limits and pricing';


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenants (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    plan character varying(50) DEFAULT 'free'::character varying NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    subscription_end_date timestamp without time zone,
    stripe_customer_id character varying(255),
    stripe_subscription_id character varying(255),
    max_migrations_per_month integer DEFAULT 10,
    max_concurrent_migrations integer DEFAULT 1,
    total_migrations integer DEFAULT 0,
    total_rows_migrated bigint DEFAULT 0,
    plan_id uuid,
    subscription_status character varying(50) DEFAULT 'active'::character varying,
    subscription_start_date timestamp with time zone DEFAULT now(),
    billing_cycle character varying(20) DEFAULT 'monthly'::character varying,
    slug character varying(100) NOT NULL,
    status character varying(50) DEFAULT 'active'::character varying,
    plan_name character varying(100) DEFAULT 'free'::character varying,
    max_users integer DEFAULT 3,
    max_jobs integer DEFAULT 10,
    max_connections integer DEFAULT 5,
    max_workers integer DEFAULT 2,
    storage_gb_limit integer DEFAULT 10,
    billing_email character varying(255),
    metadata jsonb DEFAULT '{}'::jsonb
);


--
-- Name: usage_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usage_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    job_id uuid,
    event_type character varying(50) NOT NULL,
    metric_name character varying(100) NOT NULL,
    metric_value numeric(20,4) NOT NULL,
    unit character varying(20),
    metadata jsonb DEFAULT '{}'::jsonb,
    "timestamp" timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE usage_events; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.usage_events IS 'Time-series usage tracking for billing and analytics';


--
-- Name: tenant_current_usage; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.tenant_current_usage AS
 SELECT t.id AS tenant_id,
    t.name AS tenant_name,
    tp.name AS plan_name,
    tp.max_concurrent_jobs,
    tp.max_gb_per_month,
    count(DISTINCT mj.id) FILTER (WHERE ((mj.status)::text = 'running'::text)) AS active_jobs,
    count(DISTINCT mj.id) FILTER (WHERE (mj.created_at >= date_trunc('month'::text, now()))) AS jobs_this_month,
    COALESCE(sum(ue.metric_value) FILTER (WHERE (((ue.metric_name)::text = 'gb_migrated'::text) AND (ue."timestamp" >= date_trunc('month'::text, now())))), (0)::numeric) AS gb_used_this_month,
    ((tp.max_gb_per_month)::numeric - COALESCE(sum(ue.metric_value) FILTER (WHERE (((ue.metric_name)::text = 'gb_migrated'::text) AND (ue."timestamp" >= date_trunc('month'::text, now())))), (0)::numeric)) AS gb_remaining_this_month
   FROM (((public.tenants t
     JOIN public.tenant_plans tp ON ((t.plan_id = tp.id)))
     LEFT JOIN public.migration_jobs mj ON ((((t.id)::text = (mj.tenant_id)::text) AND (((mj.lifecycle_status)::text = 'active'::text) OR (mj.lifecycle_status IS NULL)))))
     LEFT JOIN public.usage_events ue ON ((t.id = ue.tenant_id)))
  GROUP BY t.id, t.name, tp.name, tp.max_concurrent_jobs, tp.max_gb_per_month;


--
-- Name: VIEW tenant_current_usage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.tenant_current_usage IS 'Real-time resource usage and limits per tenant';


--
-- Name: tenant_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_usage (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    period_month character varying(7) NOT NULL,
    jobs_created integer DEFAULT 0,
    jobs_completed integer DEFAULT 0,
    rows_migrated bigint DEFAULT 0,
    gb_transferred numeric(10,3) DEFAULT 0,
    worker_hours numeric(10,2) DEFAULT 0,
    api_calls integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: tenant_usage_current_month; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.tenant_usage_current_month AS
 SELECT ue.tenant_id,
    t.name AS tenant_name,
    tp.name AS plan_name,
    date_trunc('month'::text, now()) AS month,
    sum(
        CASE
            WHEN ((ue.metric_name)::text = 'gb_migrated'::text) THEN ue.metric_value
            ELSE (0)::numeric
        END) AS total_gb_migrated,
    sum(
        CASE
            WHEN ((ue.metric_name)::text = 'rows_processed'::text) THEN ue.metric_value
            ELSE (0)::numeric
        END) AS total_rows_processed,
    sum(
        CASE
            WHEN ((ue.event_type)::text = 'job_created'::text) THEN 1
            ELSE 0
        END) AS total_jobs_created,
    sum(
        CASE
            WHEN ((ue.metric_name)::text = 'compute_hours'::text) THEN ue.metric_value
            ELSE (0)::numeric
        END) AS total_compute_hours,
    tp.max_gb_per_month AS plan_limit_gb,
    ((sum(
        CASE
            WHEN ((ue.metric_name)::text = 'gb_migrated'::text) THEN ue.metric_value
            ELSE (0)::numeric
        END) / (NULLIF(tp.max_gb_per_month, 0))::numeric) * (100)::numeric) AS usage_percentage
   FROM ((public.usage_events ue
     JOIN public.tenants t ON ((ue.tenant_id = t.id)))
     JOIN public.tenant_plans tp ON ((t.plan_id = tp.id)))
  WHERE (ue."timestamp" >= date_trunc('month'::text, now()))
  GROUP BY ue.tenant_id, t.name, tp.name, tp.max_gb_per_month;


--
-- Name: VIEW tenant_usage_current_month; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.tenant_usage_current_month IS 'Aggregated usage metrics for billing period';


--
-- Name: tenant_webhooks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenant_webhooks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    name character varying(200) NOT NULL,
    url text NOT NULL,
    secret character varying(100) NOT NULL,
    events text[] NOT NULL,
    enabled boolean DEFAULT true,
    retry_count integer DEFAULT 3,
    timeout_seconds integer DEFAULT 30,
    last_triggered_at timestamp with time zone,
    last_status character varying(20),
    failure_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: user_invitations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_invitations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    invited_by_id uuid,
    email character varying(255) NOT NULL,
    role character varying(100) DEFAULT 'migration_operator'::character varying NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    accepted_at timestamp without time zone,
    status character varying(50) DEFAULT 'pending'::character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: user_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    ip_address character varying(50),
    user_agent text,
    is_revoked boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    token_jti character varying(255),
    issued_at timestamp without time zone DEFAULT now(),
    revoked_at timestamp without time zone,
    revoked_reason character varying(100)
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    role character varying(50) DEFAULT 'user'::character varying NOT NULL,
    is_active boolean DEFAULT true,
    email_verified boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    last_login timestamp without time zone,
    last_activity timestamp without time zone,
    full_name character varying(255),
    avatar_url text,
    mfa_enabled boolean DEFAULT false,
    mfa_secret text,
    last_login_at timestamp without time zone,
    force_password_change boolean DEFAULT false,
    failed_login_attempts integer DEFAULT 0,
    locked_until timestamp without time zone,
    created_by uuid,
    updated_at timestamp without time zone DEFAULT now(),
    phone character varying(30),
    CONSTRAINT users_role_check CHECK (((role)::text = ANY (ARRAY['platform_admin'::text, 'tenant_admin'::text, 'migration_admin'::text, 'migration_operator'::text, 'read_only'::text, 'auditor'::text, 'api_client'::text])))
);


--
-- Name: v_job_progress; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_job_progress AS
 SELECT id,
    tenant_id,
    status,
    total_tables,
    total_chunks,
    completed_chunks,
    failed_chunks,
        CASE
            WHEN (total_chunks > 0) THEN round((((completed_chunks)::numeric / (total_chunks)::numeric) * (100)::numeric), 2)
            ELSE (0)::numeric
        END AS progress_percentage,
    created_at,
    started_at,
    completed_at,
    EXTRACT(epoch FROM (COALESCE((completed_at)::timestamp with time zone, now()) - (started_at)::timestamp with time zone)) AS duration_seconds
   FROM public.migration_jobs j;


--
-- Name: v_table_progress; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_table_progress AS
 SELECT t.id,
    t.job_id,
    t.table_name,
    t.total_rows,
    t.total_chunks,
    t.completed_chunks,
    t.failed_chunks,
    t.status,
        CASE
            WHEN (t.total_chunks > 0) THEN round((((t.completed_chunks)::numeric / (t.total_chunks)::numeric) * (100)::numeric), 2)
            ELSE (0)::numeric
        END AS progress_percentage,
    sum(c.rows_processed) AS total_rows_processed
   FROM (public.migration_tables t
     LEFT JOIN public.migration_chunks c ON (((c.table_id = t.id) AND ((c.status)::text = 'completed'::text))))
  GROUP BY t.id, t.job_id, t.table_name, t.total_rows, t.total_chunks, t.completed_chunks, t.failed_chunks, t.status;


--
-- Name: worker_heartbeats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.worker_heartbeats (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    worker_name character varying(100) NOT NULL,
    worker_status character varying(30) NOT NULL,
    current_chunk_id uuid,
    hostname character varying(255),
    cpu_usage double precision,
    memory_usage double precision,
    last_heartbeat timestamp without time zone NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: workflow_definitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_definitions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(100) DEFAULT 'global'::character varying NOT NULL,
    name character varying(255) NOT NULL,
    version character varying(50) DEFAULT '1.0.0'::character varying NOT NULL,
    description text,
    nodes jsonb DEFAULT '[]'::jsonb NOT NULL,
    edges jsonb DEFAULT '[]'::jsonb NOT NULL,
    is_default boolean DEFAULT false,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: workflow_executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_executions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_def_id uuid,
    job_id uuid,
    chunk_id uuid,
    worker_id character varying(100),
    status character varying(50) DEFAULT 'pending'::character varying,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    duration_ms integer,
    rows_read bigint DEFAULT 0,
    rows_written bigint DEFAULT 0,
    rows_skipped bigint DEFAULT 0,
    current_node character varying(100),
    context_snapshot jsonb DEFAULT '{}'::jsonb,
    error_message text,
    error_node character varying(100),
    retry_count integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: workflow_node_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_node_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    execution_id uuid NOT NULL,
    node_id character varying(100) NOT NULL,
    node_type character varying(100) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    duration_ms integer,
    input_summary jsonb DEFAULT '{}'::jsonb,
    output_summary jsonb DEFAULT '{}'::jsonb,
    error_message text,
    retry_count integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: adaptive_chunk_configs adaptive_chunk_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adaptive_chunk_configs
    ADD CONSTRAINT adaptive_chunk_configs_pkey PRIMARY KEY (id);


--
-- Name: api_keys api_keys_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_key_hash_key UNIQUE (key_hash);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: assessment_reports assessment_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assessment_reports
    ADD CONSTRAINT assessment_reports_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: batch_size_history batch_size_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_size_history
    ADD CONSTRAINT batch_size_history_pkey PRIMARY KEY (id);


--
-- Name: benchmark_records benchmark_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.benchmark_records
    ADD CONSTRAINT benchmark_records_pkey PRIMARY KEY (id);


--
-- Name: cdc_events cdc_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdc_events
    ADD CONSTRAINT cdc_events_pkey PRIMARY KEY (id);


--
-- Name: cdc_sessions cdc_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdc_sessions
    ADD CONSTRAINT cdc_sessions_pkey PRIMARY KEY (id);


--
-- Name: chunk_execution_log chunk_execution_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunk_execution_log
    ADD CONSTRAINT chunk_execution_log_pkey PRIMARY KEY (id);


--
-- Name: connection_registry connection_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connection_registry
    ADD CONSTRAINT connection_registry_pkey PRIMARY KEY (id);


--
-- Name: connector_registry connector_registry_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_registry
    ADD CONSTRAINT connector_registry_name_key UNIQUE (name);


--
-- Name: connector_registry connector_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_registry
    ADD CONSTRAINT connector_registry_pkey PRIMARY KEY (id);


--
-- Name: constraint_mapping_plans constraint_mapping_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.constraint_mapping_plans
    ADD CONSTRAINT constraint_mapping_plans_pkey PRIMARY KEY (id);


--
-- Name: cutover_log cutover_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cutover_log
    ADD CONSTRAINT cutover_log_pkey PRIMARY KEY (id);


--
-- Name: data_quality_results data_quality_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_quality_results
    ADD CONSTRAINT data_quality_results_pkey PRIMARY KEY (id);


--
-- Name: datatype_conversion_rules datatype_conversion_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.datatype_conversion_rules
    ADD CONSTRAINT datatype_conversion_rules_pkey PRIMARY KEY (id);


--
-- Name: event_log event_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_log
    ADD CONSTRAINT event_log_pkey PRIMARY KEY (id);


--
-- Name: event_subscriptions event_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_subscriptions
    ADD CONSTRAINT event_subscriptions_pkey PRIMARY KEY (id);


--
-- Name: generated_scripts generated_scripts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generated_scripts
    ADD CONSTRAINT generated_scripts_pkey PRIMARY KEY (id);


--
-- Name: secrets_vault idx_secrets_tenant_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secrets_vault
    ADD CONSTRAINT idx_secrets_tenant_key UNIQUE (tenant_id, key_name);


--
-- Name: intelligence_scan_jobs intelligence_scan_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.intelligence_scan_jobs
    ADD CONSTRAINT intelligence_scan_jobs_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_invoice_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_invoice_number_key UNIQUE (invoice_number);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: knowledge_base knowledge_base_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_base
    ADD CONSTRAINT knowledge_base_pkey PRIMARY KEY (id);


--
-- Name: maintenance_mode maintenance_mode_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_mode
    ADD CONSTRAINT maintenance_mode_pkey PRIMARY KEY (id);


--
-- Name: mapping_projects mapping_projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mapping_projects
    ADD CONSTRAINT mapping_projects_pkey PRIMARY KEY (id);


--
-- Name: masking_job_log masking_job_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_job_log
    ADD CONSTRAINT masking_job_log_pkey PRIMARY KEY (id);


--
-- Name: masking_rule_sets masking_rule_sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_rule_sets
    ADD CONSTRAINT masking_rule_sets_pkey PRIMARY KEY (id);


--
-- Name: masking_rules masking_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_rules
    ADD CONSTRAINT masking_rules_pkey PRIMARY KEY (id);


--
-- Name: metadata_catalog metadata_catalog_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metadata_catalog
    ADD CONSTRAINT metadata_catalog_pkey PRIMARY KEY (id);


--
-- Name: migration_approvals migration_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_approvals
    ADD CONSTRAINT migration_approvals_pkey PRIMARY KEY (id);


--
-- Name: migration_chunks migration_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_chunks
    ADD CONSTRAINT migration_chunks_pkey PRIMARY KEY (id);


--
-- Name: migration_execution_log migration_execution_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_execution_log
    ADD CONSTRAINT migration_execution_log_pkey PRIMARY KEY (id);


--
-- Name: migration_jobs migration_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_jobs
    ADD CONSTRAINT migration_jobs_pkey PRIMARY KEY (id);


--
-- Name: migration_reports migration_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_reports
    ADD CONSTRAINT migration_reports_pkey PRIMARY KEY (id);


--
-- Name: migration_tables migration_tables_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_tables
    ADD CONSTRAINT migration_tables_pkey PRIMARY KEY (id);


--
-- Name: migration_templates migration_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_templates
    ADD CONSTRAINT migration_templates_pkey PRIMARY KEY (id);


--
-- Name: operations_actions operations_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operations_actions
    ADD CONSTRAINT operations_actions_pkey PRIMARY KEY (id);


--
-- Name: password_reset_tokens password_reset_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (id);


--
-- Name: performance_metrics performance_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.performance_metrics
    ADD CONSTRAINT performance_metrics_pkey PRIMARY KEY (id);


--
-- Name: plugin_registry plugin_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plugin_registry
    ADD CONSTRAINT plugin_registry_pkey PRIMARY KEY (id);


--
-- Name: policy_rules policy_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rules
    ADD CONSTRAINT policy_rules_pkey PRIMARY KEY (id);


--
-- Name: rate_limit_tracking rate_limit_tracking_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rate_limit_tracking
    ADD CONSTRAINT rate_limit_tracking_pkey PRIMARY KEY (id);


--
-- Name: rate_limit_tracking rate_limit_tracking_tenant_id_endpoint_window_start_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rate_limit_tracking
    ADD CONSTRAINT rate_limit_tracking_tenant_id_endpoint_window_start_key UNIQUE (tenant_id, endpoint, window_start);


--
-- Name: resource_governor_state resource_governor_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_governor_state
    ADD CONSTRAINT resource_governor_state_pkey PRIMARY KEY (id);


--
-- Name: role_definitions role_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_definitions
    ADD CONSTRAINT role_definitions_pkey PRIMARY KEY (role_name);


--
-- Name: roles roles_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_name_key UNIQUE (name);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: rollback_execution_log rollback_execution_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rollback_execution_log
    ADD CONSTRAINT rollback_execution_log_pkey PRIMARY KEY (id);


--
-- Name: rollback_plans rollback_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rollback_plans
    ADD CONSTRAINT rollback_plans_pkey PRIMARY KEY (id);


--
-- Name: schedule_runs schedule_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_runs
    ADD CONSTRAINT schedule_runs_pkey PRIMARY KEY (id);


--
-- Name: scheduled_jobs scheduled_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scheduled_jobs
    ADD CONSTRAINT scheduled_jobs_pkey PRIMARY KEY (id);


--
-- Name: schema_column_mappings schema_column_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_column_mappings
    ADD CONSTRAINT schema_column_mappings_pkey PRIMARY KEY (id);


--
-- Name: schema_diff_cache schema_diff_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_diff_cache
    ADD CONSTRAINT schema_diff_cache_pkey PRIMARY KEY (id);


--
-- Name: schema_drift_events schema_drift_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_drift_events
    ADD CONSTRAINT schema_drift_events_pkey PRIMARY KEY (id);


--
-- Name: schema_recommendations schema_recommendations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_recommendations
    ADD CONSTRAINT schema_recommendations_pkey PRIMARY KEY (id);


--
-- Name: schema_table_mappings schema_table_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_table_mappings
    ADD CONSTRAINT schema_table_mappings_pkey PRIMARY KEY (id);


--
-- Name: schema_validation_results schema_validation_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_validation_results
    ADD CONSTRAINT schema_validation_results_pkey PRIMARY KEY (id);


--
-- Name: schema_versions schema_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_versions
    ADD CONSTRAINT schema_versions_pkey PRIMARY KEY (id);


--
-- Name: secrets_vault secrets_vault_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secrets_vault
    ADD CONSTRAINT secrets_vault_pkey PRIMARY KEY (id);


--
-- Name: secrets_vault secrets_vault_tenant_id_secret_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secrets_vault
    ADD CONSTRAINT secrets_vault_tenant_id_secret_name_key UNIQUE (tenant_id, secret_name);


--
-- Name: self_tuning_actions self_tuning_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.self_tuning_actions
    ADD CONSTRAINT self_tuning_actions_pkey PRIMARY KEY (id);


--
-- Name: service_registry service_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_registry
    ADD CONSTRAINT service_registry_pkey PRIMARY KEY (id);


--
-- Name: service_registry service_registry_service_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_registry
    ADD CONSTRAINT service_registry_service_name_key UNIQUE (service_name);


--
-- Name: simulation_runs simulation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.simulation_runs
    ADD CONSTRAINT simulation_runs_pkey PRIMARY KEY (id);


--
-- Name: table_constraints_backup table_constraints_backup_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.table_constraints_backup
    ADD CONSTRAINT table_constraints_backup_pkey PRIMARY KEY (id);


--
-- Name: table_dependency_graph table_dependency_graph_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.table_dependency_graph
    ADD CONSTRAINT table_dependency_graph_pkey PRIMARY KEY (id);


--
-- Name: table_mappings table_mappings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.table_mappings
    ADD CONSTRAINT table_mappings_pkey PRIMARY KEY (id);


--
-- Name: tenant_plans tenant_plans_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_plans
    ADD CONSTRAINT tenant_plans_name_key UNIQUE (name);


--
-- Name: tenant_plans tenant_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_plans
    ADD CONSTRAINT tenant_plans_pkey PRIMARY KEY (id);


--
-- Name: tenant_usage tenant_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_usage
    ADD CONSTRAINT tenant_usage_pkey PRIMARY KEY (id);


--
-- Name: tenant_webhooks tenant_webhooks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_webhooks
    ADD CONSTRAINT tenant_webhooks_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_slug_key UNIQUE (slug);


--
-- Name: usage_events usage_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_events
    ADD CONSTRAINT usage_events_pkey PRIMARY KEY (id);


--
-- Name: user_invitations user_invitations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_invitations
    ADD CONSTRAINT user_invitations_pkey PRIMARY KEY (id);


--
-- Name: user_invitations user_invitations_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_invitations
    ADD CONSTRAINT user_invitations_token_hash_key UNIQUE (token_hash);


--
-- Name: user_sessions user_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (id);


--
-- Name: user_sessions user_sessions_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_token_hash_key UNIQUE (token_hash);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: worker_heartbeats worker_heartbeats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_heartbeats
    ADD CONSTRAINT worker_heartbeats_pkey PRIMARY KEY (id);


--
-- Name: worker_heartbeats worker_heartbeats_worker_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_heartbeats
    ADD CONSTRAINT worker_heartbeats_worker_name_key UNIQUE (worker_name);


--
-- Name: workflow_definitions workflow_definitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_definitions
    ADD CONSTRAINT workflow_definitions_pkey PRIMARY KEY (id);


--
-- Name: workflow_executions workflow_executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_executions
    ADD CONSTRAINT workflow_executions_pkey PRIMARY KEY (id);


--
-- Name: workflow_node_log workflow_node_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_node_log
    ADD CONSTRAINT workflow_node_log_pkey PRIMARY KEY (id);


--
-- Name: idx_adaptive_chunk_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_adaptive_chunk_job ON public.adaptive_chunk_configs USING btree (job_id);


--
-- Name: idx_api_keys_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_hash ON public.api_keys USING btree (key_hash);


--
-- Name: idx_api_keys_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_tenant ON public.api_keys USING btree (tenant_id);


--
-- Name: idx_approvals_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approvals_job ON public.migration_approvals USING btree (job_id);


--
-- Name: idx_approvals_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approvals_tenant ON public.migration_approvals USING btree (tenant_id, status);


--
-- Name: idx_assessment_reports_connection; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assessment_reports_connection ON public.assessment_reports USING btree (connection_id);


--
-- Name: idx_assessment_reports_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assessment_reports_tenant ON public.assessment_reports USING btree (tenant_id, generated_at DESC);


--
-- Name: idx_audit_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_action ON public.audit_logs USING btree (action);


--
-- Name: idx_audit_logs_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_action ON public.audit_logs USING btree (action);


--
-- Name: idx_audit_logs_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_resource ON public.audit_logs USING btree (resource_type, resource_id);


--
-- Name: idx_audit_logs_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_tenant ON public.audit_logs USING btree (tenant_id, created_at DESC);


--
-- Name: idx_audit_logs_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_user ON public.audit_logs USING btree (user_id, created_at DESC);


--
-- Name: idx_audit_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_resource ON public.audit_logs USING btree (resource_type, resource_id);


--
-- Name: idx_audit_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_tenant ON public.audit_logs USING btree (tenant_id, created_at DESC);


--
-- Name: idx_audit_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_user ON public.audit_logs USING btree (user_id, created_at DESC);


--
-- Name: idx_batch_history_chunk; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batch_history_chunk ON public.batch_size_history USING btree (chunk_id);


--
-- Name: idx_batch_history_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batch_history_worker ON public.batch_size_history USING btree (worker_id, created_at DESC);


--
-- Name: idx_benchmark_engines; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_benchmark_engines ON public.benchmark_records USING btree (source_engine, target_engine, recorded_at DESC);


--
-- Name: idx_benchmark_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_benchmark_tenant ON public.benchmark_records USING btree (tenant_id, recorded_at DESC);


--
-- Name: idx_cdc_events_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdc_events_session ON public.cdc_events USING btree (session_id, replayed, captured_at);


--
-- Name: idx_cdc_events_table; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdc_events_table ON public.cdc_events USING btree (session_id, table_name);


--
-- Name: idx_cdc_events_unreplayed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdc_events_unreplayed ON public.cdc_events USING btree (session_id) WHERE (replayed = false);


--
-- Name: idx_cdc_sessions_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdc_sessions_job ON public.cdc_sessions USING btree (job_id);


--
-- Name: idx_chunks_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_heartbeat ON public.migration_chunks USING btree (last_heartbeat);


--
-- Name: idx_chunks_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_job_id ON public.migration_chunks USING btree (job_id);


--
-- Name: idx_chunks_job_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_job_status ON public.migration_chunks USING btree (job_id, status);


--
-- Name: idx_chunks_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_pending ON public.migration_chunks USING btree (status) WHERE ((status)::text = 'pending'::text);


--
-- Name: idx_chunks_retry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_retry ON public.migration_chunks USING btree (next_retry_at) WHERE (((status)::text = 'failed'::text) AND (retry_count < max_retries));


--
-- Name: idx_chunks_running; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_running ON public.migration_chunks USING btree (status, last_heartbeat) WHERE ((status)::text = 'running'::text);


--
-- Name: idx_chunks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_status ON public.migration_chunks USING btree (status);


--
-- Name: idx_chunks_status_hb; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_status_hb ON public.migration_chunks USING btree (status, last_heartbeat);


--
-- Name: idx_chunks_table_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_table_id ON public.migration_chunks USING btree (table_id);


--
-- Name: idx_chunks_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_worker ON public.migration_chunks USING btree (worker_id);


--
-- Name: idx_col_mappings_table_mapping; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_col_mappings_table_mapping ON public.schema_column_mappings USING btree (table_mapping_id);


--
-- Name: idx_conn_registry_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_conn_registry_name ON public.connection_registry USING btree (tenant_id, name);


--
-- Name: idx_conn_registry_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conn_registry_tenant ON public.connection_registry USING btree (tenant_id);


--
-- Name: idx_constraint_plans_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_constraint_plans_project ON public.constraint_mapping_plans USING btree (project_id);


--
-- Name: idx_constraints_backup_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_constraints_backup_job ON public.table_constraints_backup USING btree (job_id, table_name);


--
-- Name: idx_cutover_log_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cutover_log_session ON public.cutover_log USING btree (session_id);


--
-- Name: idx_dep_graph_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dep_graph_job ON public.table_dependency_graph USING btree (job_id, execution_order);


--
-- Name: idx_diff_cache_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_diff_cache_pair ON public.schema_diff_cache USING btree (source_version_id, target_version_id);


--
-- Name: idx_dq_results_connection; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dq_results_connection ON public.data_quality_results USING btree (connection_id, table_name);


--
-- Name: idx_dq_results_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dq_results_severity ON public.data_quality_results USING btree (severity, check_type);


--
-- Name: idx_drift_events_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_drift_events_job ON public.schema_drift_events USING btree (job_id, detected_at DESC);


--
-- Name: idx_dtype_rules_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_dtype_rules_unique ON public.datatype_conversion_rules USING btree (tenant_id, source_db, target_db, source_type, target_type);


--
-- Name: idx_event_log_correlation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_log_correlation ON public.event_log USING btree (correlation_id);


--
-- Name: idx_event_log_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_log_resource ON public.event_log USING btree (resource_type, resource_id);


--
-- Name: idx_event_log_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_log_tenant ON public.event_log USING btree (tenant_id, published_at DESC);


--
-- Name: idx_event_log_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_log_type ON public.event_log USING btree (event_type, published_at DESC);


--
-- Name: idx_event_subs_pattern; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_event_subs_pattern ON public.event_subscriptions USING btree (event_pattern, is_active);


--
-- Name: idx_exec_log_chunk; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exec_log_chunk ON public.chunk_execution_log USING btree (chunk_id, created_at DESC);


--
-- Name: idx_exec_log_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exec_log_project ON public.migration_execution_log USING btree (project_id);


--
-- Name: idx_exec_log_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exec_log_worker ON public.chunk_execution_log USING btree (worker_id, created_at DESC);


--
-- Name: idx_generated_scripts_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generated_scripts_project ON public.generated_scripts USING btree (project_id);


--
-- Name: idx_invitations_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invitations_email ON public.user_invitations USING btree (email);


--
-- Name: idx_invitations_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invitations_tenant ON public.user_invitations USING btree (tenant_id);


--
-- Name: idx_invoices_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invoices_due_date ON public.invoices USING btree (due_date);


--
-- Name: idx_invoices_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invoices_status ON public.invoices USING btree (status);


--
-- Name: idx_invoices_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_invoices_tenant ON public.invoices USING btree (tenant_id);


--
-- Name: idx_jobs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_created_at ON public.migration_jobs USING btree (created_at DESC);


--
-- Name: idx_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_status ON public.migration_jobs USING btree (status);


--
-- Name: idx_jobs_tenant_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_tenant_created ON public.migration_jobs USING btree (tenant_id, created_at DESC);


--
-- Name: idx_jobs_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_tenant_id ON public.migration_jobs USING btree (tenant_id);


--
-- Name: idx_kb_engine_pair; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_engine_pair ON public.knowledge_base USING btree (source_engine, target_engine, entry_type);


--
-- Name: idx_kb_tags; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_tags ON public.knowledge_base USING gin (tags);


--
-- Name: idx_kb_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kb_tenant ON public.knowledge_base USING btree (tenant_id, entry_type, usefulness_score DESC);


--
-- Name: idx_maintenance_mode_global; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_maintenance_mode_global ON public.maintenance_mode USING btree (((tenant_id IS NULL))) WHERE (tenant_id IS NULL);


--
-- Name: idx_maintenance_mode_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_maintenance_mode_tenant ON public.maintenance_mode USING btree (tenant_id);


--
-- Name: idx_mapping_projects_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mapping_projects_tenant ON public.mapping_projects USING btree (tenant_id);


--
-- Name: idx_masking_log_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masking_log_job ON public.masking_job_log USING btree (job_id);


--
-- Name: idx_masking_rule_sets_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_masking_rule_sets_name ON public.masking_rule_sets USING btree (tenant_id, name);


--
-- Name: idx_masking_rules_set; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masking_rules_set ON public.masking_rules USING btree (rule_set_id);


--
-- Name: idx_masking_rules_table; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_masking_rules_table ON public.masking_rules USING btree (table_name, column_name);


--
-- Name: idx_metadata_catalog_connection_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_metadata_catalog_connection_type ON public.metadata_catalog USING btree (connection_id, catalog_type, computed_at DESC);


--
-- Name: idx_metadata_catalog_fresh; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_metadata_catalog_fresh ON public.metadata_catalog USING btree (table_name, computed_at DESC);


--
-- Name: idx_metadata_catalog_table; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_metadata_catalog_table ON public.metadata_catalog USING btree (connection_id, table_name, catalog_type);


--
-- Name: idx_metadata_catalog_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_metadata_catalog_tenant ON public.metadata_catalog USING btree (tenant_id, catalog_type);


--
-- Name: idx_metadata_catalog_type_table; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_metadata_catalog_type_table ON public.metadata_catalog USING btree (catalog_type, table_name);


--
-- Name: idx_migration_reports_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_migration_reports_job ON public.migration_reports USING btree (job_id, report_type);


--
-- Name: idx_migration_reports_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_migration_reports_tenant ON public.migration_reports USING btree (tenant_id, generated_at DESC);


--
-- Name: idx_ops_actions_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ops_actions_resource ON public.operations_actions USING btree (resource_type, resource_id, created_at DESC);


--
-- Name: idx_ops_actions_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ops_actions_tenant ON public.operations_actions USING btree (tenant_id, created_at DESC);


--
-- Name: idx_password_reset_tokens_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_password_reset_tokens_hash ON public.password_reset_tokens USING btree (token_hash);


--
-- Name: idx_password_reset_tokens_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_password_reset_tokens_user ON public.password_reset_tokens USING btree (user_id, created_at DESC);


--
-- Name: idx_perf_metrics_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_perf_metrics_job ON public.performance_metrics USING btree (job_id, metric_timestamp DESC);


--
-- Name: idx_perf_metrics_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_perf_metrics_timestamp ON public.performance_metrics USING btree (metric_timestamp DESC);


--
-- Name: idx_perf_metrics_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_perf_metrics_worker ON public.performance_metrics USING btree (worker_id, metric_timestamp DESC);


--
-- Name: idx_plugin_registry_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_plugin_registry_type ON public.plugin_registry USING btree (plugin_type, is_active);


--
-- Name: idx_plugin_registry_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_plugin_registry_unique ON public.plugin_registry USING btree (tenant_id, plugin_type, name);


--
-- Name: idx_policy_rules_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_policy_rules_tenant ON public.policy_rules USING btree (tenant_id, policy_type);


--
-- Name: idx_rate_limit_tenant_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rate_limit_tenant_time ON public.rate_limit_tracking USING btree (tenant_id, window_start);


--
-- Name: idx_recommendations_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_recommendations_project ON public.schema_recommendations USING btree (project_id);


--
-- Name: idx_reset_tokens_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reset_tokens_expiry ON public.password_reset_tokens USING btree (expires_at) WHERE (used_at IS NULL);


--
-- Name: idx_reset_tokens_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reset_tokens_user ON public.password_reset_tokens USING btree (user_id);


--
-- Name: idx_resource_governor_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_resource_governor_job ON public.resource_governor_state USING btree (job_id, recorded_at DESC);


--
-- Name: idx_rollback_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rollback_job ON public.rollback_plans USING btree (job_id);


--
-- Name: idx_scan_jobs_connection; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_scan_jobs_connection ON public.intelligence_scan_jobs USING btree (connection_id);


--
-- Name: idx_scan_jobs_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_scan_jobs_tenant ON public.intelligence_scan_jobs USING btree (tenant_id, status);


--
-- Name: idx_schedule_runs_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schedule_runs_job ON public.schedule_runs USING btree (scheduled_job_id, started_at DESC);


--
-- Name: idx_scheduled_jobs_next_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_scheduled_jobs_next_run ON public.scheduled_jobs USING btree (next_run_at) WHERE (is_active = true);


--
-- Name: idx_scheduled_jobs_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_scheduled_jobs_tenant ON public.scheduled_jobs USING btree (tenant_id, is_active);


--
-- Name: idx_schema_val_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schema_val_project ON public.schema_validation_results USING btree (project_id);


--
-- Name: idx_schema_versions_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schema_versions_name ON public.schema_versions USING btree (tenant_id, name);


--
-- Name: idx_schema_versions_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schema_versions_tenant ON public.schema_versions USING btree (tenant_id);


--
-- Name: idx_secrets_vault_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_secrets_vault_tenant ON public.secrets_vault USING btree (tenant_id);


--
-- Name: idx_secrets_vault_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_secrets_vault_type ON public.secrets_vault USING btree (secret_type);


--
-- Name: idx_service_registry_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_registry_status ON public.service_registry USING btree (status);


--
-- Name: idx_sessions_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expires ON public.user_sessions USING btree (expires_at);


--
-- Name: idx_sessions_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expiry ON public.user_sessions USING btree (expires_at) WHERE (revoked_at IS NULL);


--
-- Name: idx_sessions_jti; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_sessions_jti ON public.user_sessions USING btree (token_jti) WHERE (token_jti IS NOT NULL);


--
-- Name: idx_sessions_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_token ON public.user_sessions USING btree (token_hash);


--
-- Name: idx_sessions_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_user ON public.user_sessions USING btree (user_id);


--
-- Name: idx_simulation_runs_connection; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_simulation_runs_connection ON public.simulation_runs USING btree (connection_id);


--
-- Name: idx_simulation_runs_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_simulation_runs_tenant ON public.simulation_runs USING btree (tenant_id, created_at DESC);


--
-- Name: idx_table_mappings_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_table_mappings_project ON public.schema_table_mappings USING btree (project_id);


--
-- Name: idx_tables_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tables_job ON public.migration_tables USING btree (job_id);


--
-- Name: idx_tables_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tables_job_id ON public.migration_tables USING btree (job_id);


--
-- Name: idx_tables_job_table; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tables_job_table ON public.migration_tables USING btree (job_id, table_name);


--
-- Name: idx_tables_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tables_status ON public.migration_tables USING btree (status);


--
-- Name: idx_templates_public; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_templates_public ON public.migration_templates USING btree (is_public) WHERE (is_public = true);


--
-- Name: idx_templates_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_templates_tenant ON public.migration_templates USING btree (tenant_id);


--
-- Name: idx_tenants_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_active ON public.tenants USING btree (is_active);


--
-- Name: idx_tenants_plan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_plan ON public.tenants USING btree (plan);


--
-- Name: idx_tenants_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_slug ON public.tenants USING btree (slug);


--
-- Name: idx_tenants_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tenants_status ON public.tenants USING btree (status);


--
-- Name: idx_tuning_actions_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tuning_actions_job ON public.self_tuning_actions USING btree (job_id, created_at DESC);


--
-- Name: idx_usage_events_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usage_events_job ON public.usage_events USING btree (job_id);


--
-- Name: idx_usage_events_tenant_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usage_events_tenant_time ON public.usage_events USING btree (tenant_id, "timestamp" DESC);


--
-- Name: idx_usage_events_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usage_events_type ON public.usage_events USING btree (event_type);


--
-- Name: idx_usage_tenant_month; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_usage_tenant_month ON public.tenant_usage USING btree (tenant_id, period_month);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: idx_users_email_lower_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_users_email_lower_unique ON public.users USING btree (lower((email)::text));


--
-- Name: idx_users_email_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_users_email_tenant ON public.users USING btree (tenant_id, lower((email)::text));


--
-- Name: idx_users_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_role ON public.users USING btree (role);


--
-- Name: idx_users_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_tenant ON public.users USING btree (tenant_id);


--
-- Name: idx_users_tenant_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_tenant_active ON public.users USING btree (tenant_id, is_active);


--
-- Name: idx_users_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_tenant_id ON public.users USING btree (tenant_id);


--
-- Name: idx_webhooks_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_webhooks_enabled ON public.tenant_webhooks USING btree (enabled);


--
-- Name: idx_webhooks_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_webhooks_tenant ON public.tenant_webhooks USING btree (tenant_id);


--
-- Name: idx_wf_exec_chunk; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wf_exec_chunk ON public.workflow_executions USING btree (chunk_id);


--
-- Name: idx_wf_exec_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wf_exec_job ON public.workflow_executions USING btree (job_id, status);


--
-- Name: idx_wf_exec_worker; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wf_exec_worker ON public.workflow_executions USING btree (worker_id, status);


--
-- Name: idx_wf_node_log_exec; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wf_node_log_exec ON public.workflow_node_log USING btree (execution_id);


--
-- Name: idx_workflow_def_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_def_active ON public.workflow_definitions USING btree (tenant_id, is_active);


--
-- Name: idx_workflow_def_name_version; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_workflow_def_name_version ON public.workflow_definitions USING btree (tenant_id, name, version);


--
-- Name: migration_chunks trigger_update_counters; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_update_counters AFTER UPDATE ON public.migration_chunks FOR EACH ROW EXECUTE FUNCTION public.update_job_counters();


--
-- Name: adaptive_chunk_configs adaptive_chunk_configs_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adaptive_chunk_configs
    ADD CONSTRAINT adaptive_chunk_configs_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: api_keys api_keys_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: api_keys api_keys_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: audit_logs audit_logs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: batch_size_history batch_size_history_chunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_size_history
    ADD CONSTRAINT batch_size_history_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.migration_chunks(id) ON DELETE CASCADE;


--
-- Name: benchmark_records benchmark_records_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.benchmark_records
    ADD CONSTRAINT benchmark_records_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE SET NULL;


--
-- Name: benchmark_records benchmark_records_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.benchmark_records
    ADD CONSTRAINT benchmark_records_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: cdc_events cdc_events_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdc_events
    ADD CONSTRAINT cdc_events_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.cdc_sessions(id) ON DELETE CASCADE;


--
-- Name: cdc_sessions cdc_sessions_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdc_sessions
    ADD CONSTRAINT cdc_sessions_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: chunk_execution_log chunk_execution_log_chunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunk_execution_log
    ADD CONSTRAINT chunk_execution_log_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.migration_chunks(id) ON DELETE CASCADE;


--
-- Name: constraint_mapping_plans constraint_mapping_plans_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.constraint_mapping_plans
    ADD CONSTRAINT constraint_mapping_plans_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.mapping_projects(id) ON DELETE CASCADE;


--
-- Name: cutover_log cutover_log_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cutover_log
    ADD CONSTRAINT cutover_log_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.cdc_sessions(id) ON DELETE CASCADE;


--
-- Name: generated_scripts generated_scripts_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generated_scripts
    ADD CONSTRAINT generated_scripts_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.mapping_projects(id) ON DELETE CASCADE;


--
-- Name: invoices invoices_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: knowledge_base knowledge_base_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_base
    ADD CONSTRAINT knowledge_base_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id);


--
-- Name: maintenance_mode maintenance_mode_activated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_mode
    ADD CONSTRAINT maintenance_mode_activated_by_fkey FOREIGN KEY (activated_by) REFERENCES public.users(id);


--
-- Name: maintenance_mode maintenance_mode_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.maintenance_mode
    ADD CONSTRAINT maintenance_mode_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: mapping_projects mapping_projects_source_schema_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mapping_projects
    ADD CONSTRAINT mapping_projects_source_schema_id_fkey FOREIGN KEY (source_schema_id) REFERENCES public.schema_versions(id);


--
-- Name: mapping_projects mapping_projects_target_schema_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mapping_projects
    ADD CONSTRAINT mapping_projects_target_schema_id_fkey FOREIGN KEY (target_schema_id) REFERENCES public.schema_versions(id);


--
-- Name: masking_job_log masking_job_log_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_job_log
    ADD CONSTRAINT masking_job_log_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: masking_job_log masking_job_log_rule_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_job_log
    ADD CONSTRAINT masking_job_log_rule_set_id_fkey FOREIGN KEY (rule_set_id) REFERENCES public.masking_rule_sets(id);


--
-- Name: masking_rules masking_rules_rule_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.masking_rules
    ADD CONSTRAINT masking_rules_rule_set_id_fkey FOREIGN KEY (rule_set_id) REFERENCES public.masking_rule_sets(id) ON DELETE CASCADE;


--
-- Name: migration_approvals migration_approvals_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_approvals
    ADD CONSTRAINT migration_approvals_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: migration_approvals migration_approvals_requested_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_approvals
    ADD CONSTRAINT migration_approvals_requested_by_id_fkey FOREIGN KEY (requested_by_id) REFERENCES public.users(id);


--
-- Name: migration_approvals migration_approvals_reviewed_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_approvals
    ADD CONSTRAINT migration_approvals_reviewed_by_id_fkey FOREIGN KEY (reviewed_by_id) REFERENCES public.users(id);


--
-- Name: migration_approvals migration_approvals_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_approvals
    ADD CONSTRAINT migration_approvals_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: migration_chunks migration_chunks_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_chunks
    ADD CONSTRAINT migration_chunks_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: migration_chunks migration_chunks_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_chunks
    ADD CONSTRAINT migration_chunks_table_id_fkey FOREIGN KEY (table_id) REFERENCES public.migration_tables(id) ON DELETE CASCADE;


--
-- Name: migration_execution_log migration_execution_log_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_execution_log
    ADD CONSTRAINT migration_execution_log_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.mapping_projects(id) ON DELETE CASCADE;


--
-- Name: migration_jobs migration_jobs_cancelled_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_jobs
    ADD CONSTRAINT migration_jobs_cancelled_by_fkey FOREIGN KEY (cancelled_by) REFERENCES public.users(id);


--
-- Name: migration_jobs migration_jobs_paused_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_jobs
    ADD CONSTRAINT migration_jobs_paused_by_fkey FOREIGN KEY (paused_by) REFERENCES public.users(id);


--
-- Name: migration_jobs migration_jobs_source_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_jobs
    ADD CONSTRAINT migration_jobs_source_connection_id_fkey FOREIGN KEY (source_connection_id) REFERENCES public.connection_registry(id);


--
-- Name: migration_jobs migration_jobs_target_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_jobs
    ADD CONSTRAINT migration_jobs_target_connection_id_fkey FOREIGN KEY (target_connection_id) REFERENCES public.connection_registry(id);


--
-- Name: migration_reports migration_reports_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_reports
    ADD CONSTRAINT migration_reports_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id);


--
-- Name: migration_tables migration_tables_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_tables
    ADD CONSTRAINT migration_tables_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: migration_templates migration_templates_created_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_templates
    ADD CONSTRAINT migration_templates_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.users(id);


--
-- Name: migration_templates migration_templates_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.migration_templates
    ADD CONSTRAINT migration_templates_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: operations_actions operations_actions_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operations_actions
    ADD CONSTRAINT operations_actions_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.users(id);


--
-- Name: operations_actions operations_actions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operations_actions
    ADD CONSTRAINT operations_actions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: password_reset_tokens password_reset_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: performance_metrics performance_metrics_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.performance_metrics
    ADD CONSTRAINT performance_metrics_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: rate_limit_tracking rate_limit_tracking_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rate_limit_tracking
    ADD CONSTRAINT rate_limit_tracking_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: resource_governor_state resource_governor_state_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.resource_governor_state
    ADD CONSTRAINT resource_governor_state_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: rollback_execution_log rollback_execution_log_rollback_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rollback_execution_log
    ADD CONSTRAINT rollback_execution_log_rollback_plan_id_fkey FOREIGN KEY (rollback_plan_id) REFERENCES public.rollback_plans(id) ON DELETE CASCADE;


--
-- Name: rollback_plans rollback_plans_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rollback_plans
    ADD CONSTRAINT rollback_plans_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: schedule_runs schedule_runs_scheduled_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_runs
    ADD CONSTRAINT schedule_runs_scheduled_job_id_fkey FOREIGN KEY (scheduled_job_id) REFERENCES public.scheduled_jobs(id) ON DELETE CASCADE;


--
-- Name: schema_column_mappings schema_column_mappings_table_mapping_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_column_mappings
    ADD CONSTRAINT schema_column_mappings_table_mapping_id_fkey FOREIGN KEY (table_mapping_id) REFERENCES public.schema_table_mappings(id) ON DELETE CASCADE;


--
-- Name: schema_diff_cache schema_diff_cache_source_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_diff_cache
    ADD CONSTRAINT schema_diff_cache_source_version_id_fkey FOREIGN KEY (source_version_id) REFERENCES public.schema_versions(id) ON DELETE CASCADE;


--
-- Name: schema_diff_cache schema_diff_cache_target_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_diff_cache
    ADD CONSTRAINT schema_diff_cache_target_version_id_fkey FOREIGN KEY (target_version_id) REFERENCES public.schema_versions(id) ON DELETE CASCADE;


--
-- Name: schema_drift_events schema_drift_events_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_drift_events
    ADD CONSTRAINT schema_drift_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: schema_recommendations schema_recommendations_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_recommendations
    ADD CONSTRAINT schema_recommendations_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.mapping_projects(id) ON DELETE CASCADE;


--
-- Name: schema_table_mappings schema_table_mappings_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_table_mappings
    ADD CONSTRAINT schema_table_mappings_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.mapping_projects(id) ON DELETE CASCADE;


--
-- Name: schema_validation_results schema_validation_results_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_validation_results
    ADD CONSTRAINT schema_validation_results_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.mapping_projects(id) ON DELETE CASCADE;


--
-- Name: secrets_vault secrets_vault_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secrets_vault
    ADD CONSTRAINT secrets_vault_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: secrets_vault secrets_vault_created_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secrets_vault
    ADD CONSTRAINT secrets_vault_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.users(id);


--
-- Name: secrets_vault secrets_vault_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.secrets_vault
    ADD CONSTRAINT secrets_vault_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: self_tuning_actions self_tuning_actions_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.self_tuning_actions
    ADD CONSTRAINT self_tuning_actions_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: table_constraints_backup table_constraints_backup_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.table_constraints_backup
    ADD CONSTRAINT table_constraints_backup_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: table_dependency_graph table_dependency_graph_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.table_dependency_graph
    ADD CONSTRAINT table_dependency_graph_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: table_mappings table_mappings_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.table_mappings
    ADD CONSTRAINT table_mappings_table_id_fkey FOREIGN KEY (table_id) REFERENCES public.migration_tables(id);


--
-- Name: tenant_usage tenant_usage_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_usage
    ADD CONSTRAINT tenant_usage_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenant_webhooks tenant_webhooks_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenant_webhooks
    ADD CONSTRAINT tenant_webhooks_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: tenants tenants_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.tenant_plans(id);


--
-- Name: usage_events usage_events_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_events
    ADD CONSTRAINT usage_events_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE SET NULL;


--
-- Name: usage_events usage_events_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usage_events
    ADD CONSTRAINT usage_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: user_invitations user_invitations_invited_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_invitations
    ADD CONSTRAINT user_invitations_invited_by_id_fkey FOREIGN KEY (invited_by_id) REFERENCES public.users(id);


--
-- Name: user_invitations user_invitations_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_invitations
    ADD CONSTRAINT user_invitations_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: user_sessions user_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: workflow_executions workflow_executions_chunk_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_executions
    ADD CONSTRAINT workflow_executions_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.migration_chunks(id) ON DELETE CASCADE;


--
-- Name: workflow_executions workflow_executions_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_executions
    ADD CONSTRAINT workflow_executions_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.migration_jobs(id) ON DELETE CASCADE;


--
-- Name: workflow_executions workflow_executions_workflow_def_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_executions
    ADD CONSTRAINT workflow_executions_workflow_def_id_fkey FOREIGN KEY (workflow_def_id) REFERENCES public.workflow_definitions(id);


--
-- Name: workflow_node_log workflow_node_log_execution_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_node_log
    ADD CONSTRAINT workflow_node_log_execution_id_fkey FOREIGN KEY (execution_id) REFERENCES public.workflow_executions(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: role_definitions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.role_definitions (role_name, display_name, description, permissions, rank) FROM stdin;
platform_admin	Platform Admin	Full system access. Can manage all settings, users, and tenants.	{*}	1
tenant_admin	Tenant Admin	Manage users and settings within their tenant. Cannot change platform-level configuration.	{manage:users,manage:tenant_settings,configure:policies,configure:notifiers,maintenance:mode,emergency:stop,view:audit,create:connection,create:job,start:job,pause:job,resume:job,cancel:job,kill:worker,create:masking,create:schedule,view:knowledge}	2
migration_admin	Migration Admin	Create and manage migrations. Cannot manage users or platform settings.	{create:connection,create:job,start:job,pause:job,resume:job,cancel:job,kill:worker,create:masking,create:schedule,view:knowledge,view:audit_own}	3
migration_operator	Migration Operator	Run and monitor existing migrations. Cannot create new connections or jobs.	{start:job,pause:job,resume:job,view:knowledge}	4
read_only	Read Only	View all data across the platform with no modification rights.	{view:jobs,view:connections,view:schema,view:reports,view:knowledge}	5
auditor	Auditor	View audit logs and reports only. No access to operational pages.	{view:audit,view:reports}	6
api_client	API Client	Programmatic API access only. No UI access.	{api:access}	7
\.


--
-- Data for Name: roles; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.roles (id, name, description, permissions, is_system, created_at) FROM stdin;
1403b889-f230-4a54-80be-273e4fb020fe	platform_admin	Full system access. Can manage all settings, users, and tenants.	["*:*"]	t	2026-06-28 22:02:27.931974
4b1a4891-5841-47e0-9f42-0bf010b0ca39	tenant_admin	Manage users and settings within their tenant. Cannot change platform-level configuration.	["users:*", "connections:*", "jobs:*", "jobs:approve", "schema:*", "operations:*", "masking:*", "scheduler:*", "reports:*", "knowledge:*", "audit:read", "tenant:*"]	t	2026-06-28 22:02:27.931974
0dcf8396-ea08-4259-bdc3-1f3d3167d767	migration_admin	Create and manage migrations. Cannot manage users, platform settings, or approve migrations they created.	["connections:*", "jobs:create", "jobs:read", "jobs:write", "jobs:start", "jobs:pause", "jobs:resume", "jobs:cancel", "schema:*", "operations:read", "operations:write", "masking:*", "scheduler:*", "reports:*", "knowledge:*"]	t	2026-06-28 22:02:27.931974
0e4512fa-4763-4d5a-baf8-eb86d28d658b	migration_operator	Run and monitor existing migrations. Cannot create new connections or jobs.	["connections:read", "jobs:read", "jobs:start", "jobs:pause", "jobs:resume", "schema:read", "operations:read", "operations:write", "scheduler:read", "reports:read", "knowledge:read"]	t	2026-06-28 22:02:27.931974
590ea40b-9374-4c93-9306-8ca44624bf12	read_only	View all data across the platform with no modification rights.	["connections:read", "jobs:read", "schema:read", "operations:read", "masking:read", "scheduler:read", "reports:read", "knowledge:read"]	t	2026-06-28 22:02:27.931974
85b38f2f-286c-48ed-9824-39ae4d5146c0	auditor	View audit logs and reports only. No access to operational pages.	["audit:read", "reports:read"]	t	2026-06-28 22:02:27.931974
22aaba19-8a11-4ea0-9669-2fad2b4967c4	api_client	Programmatic API access only. No UI access.	["api:access"]	t	2026-06-28 22:02:27.931974
\.


--
-- Data for Name: tenant_plans; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.tenant_plans (id, name, display_name, description, price_monthly, price_per_gb, max_concurrent_jobs, max_workers_per_job, max_gb_per_month, max_tables_per_job, api_rate_limit_per_minute, support_level, features, created_at) FROM stdin;
6c86319b-fe10-42d1-8fb5-73038e5b4c61	free	Free Tier	Perfect for testing and small projects	0.00	0.0000	1	2	10	50	30	community	{}	2026-03-01 14:32:39.611292+00
c458a4c8-cbc7-4a38-b74b-fc8ade8dc607	starter	Starter	For small teams and growing businesses	49.00	0.1000	3	4	100	50	120	email	{}	2026-03-01 14:32:39.611292+00
9a358b5e-bd75-48b0-9c07-48264026c932	professional	Professional	For professional teams with high volume	199.00	0.0500	10	8	500	50	300	priority	{}	2026-03-01 14:32:39.611292+00
06741677-ef82-4707-8ea8-1e49b395a9a3	enterprise	Enterprise	Custom solutions for large organizations	999.00	0.0200	50	16	5000	50	1000	24/7	{}	2026-03-01 14:32:39.611292+00
\.


--
-- PostgreSQL database dump complete
--


