#!/usr/bin/env python3
"""SQLite-backed durable state for Rick v6."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

_SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def data_root() -> Path:
    return Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))


def runtime_db_path() -> Path:
    return Path(
        os.path.expanduser(
            os.getenv("RICK_RUNTIME_DB_FILE", str(data_root() / "runtime" / "rick-runtime.db"))
        )
    )


def connect() -> sqlite3.Connection:
    db_path = runtime_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Set restrictive umask before creating the DB file to avoid permission race
    old_umask = os.umask(0o077)
    try:
        connection = sqlite3.connect(str(db_path))
    finally:
        os.umask(old_umask)
    # Ensure DB file permissions are owner-only
    try:
        db_path.chmod(0o600)
        wal_path = db_path.parent / (db_path.name + "-wal")
        shm_path = db_path.parent / (db_path.name + "-shm")
        if wal_path.exists():
            wal_path.chmod(0o600)
        if shm_path.exists():
            shm_path.chmod(0o600)
    except OSError as exc:
        import logging
        logging.getLogger("rick.db").warning("Could not restrict DB permissions: %s", exc)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _assert_safe_identifier(value: str, label: str) -> None:
    if not _SAFE_IDENTIFIER.match(value):
        raise ValueError(f"Unsafe SQL identifier for {label}: {value!r}")


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    _assert_safe_identifier(table, "table")
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def ensure_column(connection: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    _assert_safe_identifier(table, "table")
    _assert_safe_identifier(column, "column")
    if column in table_columns(connection, table):
        return
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def migrate_db(connection: sqlite3.Connection) -> None:
    ensure_column(connection, "workflows", "lane", "TEXT NOT NULL DEFAULT 'ceo-lane'")
    ensure_column(connection, "workflows", "telegram_target", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "workflows", "openclaw_session_key", "TEXT NOT NULL DEFAULT ''")
    ensure_column(connection, "jobs", "lane", "TEXT NOT NULL DEFAULT 'ceo-lane'")
    connection.execute(
        "UPDATE workflows SET finished_at=updated_at"
        " WHERE finished_at IS NULL"
        " AND status IN ('done','published','fulfilled','denied','failed','cancelled')"
    )

    # Ensure newer tables exist (safe to re-run due to IF NOT EXISTS)
    for table_check in [
        ("conversation_messages", "chat_id TEXT NOT NULL"),
        ("notification_log", "target_chat_id TEXT NOT NULL DEFAULT ''"),
        ("scheduled_messages", "schedule_key TEXT NOT NULL UNIQUE"),
        ("prospect_pipeline", "platform TEXT NOT NULL DEFAULT ''"),
        ("tenants", "customer_id TEXT NOT NULL"),
        ("tenant_health_history", "tenant_id TEXT NOT NULL"),
        ("email_subscribers", "email TEXT NOT NULL UNIQUE"),
        ("affiliates", "name TEXT NOT NULL DEFAULT ''"),
        ("referrals", "affiliate_id TEXT NOT NULL"),
        ("fleet_benchmarks", "industry TEXT NOT NULL DEFAULT ''"),
        ("tenant_insights", "tenant_id TEXT NOT NULL"),
        ("subagent_heartbeat", "run_id TEXT PRIMARY KEY"),
        ("skill_variants", "id TEXT PRIMARY KEY"),
        ("effective_patterns", "id TEXT PRIMARY KEY"),
        ("outbound_jobs", "id TEXT PRIMARY KEY"),
        ("channel_state", "channel TEXT PRIMARY KEY"),
        ("analytics_snapshots", "id INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("lead_aliases", "id INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("prospect_graph_edges", "id INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("cost_attribution", "id INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("experiments", "id TEXT PRIMARY KEY"),
        ("email_threads", "id INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("follow_up_queue", "id INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("ledger_entries", "id INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("notification_dedupe", "dedup_hash TEXT PRIMARY KEY"),
    ]:
        table_name, _ = table_check
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not existing:
            # Re-run init to create missing tables
            init_db(connection)
            break

    # Ensure indexes exist even if tables were created before indexes were added.
    # All CREATE INDEX statements use IF NOT EXISTS so this is safe to re-run.
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_events_workflow ON events(workflow_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id);
        CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_workflow ON jobs(workflow_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        """
    )


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            slug TEXT NOT NULL,
            project TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 50,
            owner TEXT NOT NULL DEFAULT 'rick',
            lane TEXT NOT NULL DEFAULT 'ceo-lane',
            telegram_target TEXT NOT NULL DEFAULT '',
            openclaw_session_key TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            step_name TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            route TEXT NOT NULL,
            lane TEXT NOT NULL DEFAULT 'ceo-lane',
            payload_json TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            last_error TEXT,
            blocked_reason TEXT,
            approval_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            run_after TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_status_run_after
        ON jobs(status, run_after, step_index);

        CREATE INDEX IF NOT EXISTS idx_jobs_lane_status_run_after
        ON jobs(lane, status, run_after, step_index);

        CREATE INDEX IF NOT EXISTS idx_workflows_lane_status
        ON workflows(lane, status, priority);

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            area TEXT NOT NULL,
            request_text TEXT NOT NULL,
            impact_text TEXT NOT NULL,
            policy_basis TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            resolved_by TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            resolution_note TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_approvals_status
        ON approvals(status, created_at);

        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_artifacts_workflow
        ON artifacts(workflow_id, created_at);

        CREATE TABLE IF NOT EXISTS customers (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            latest_workflow_id TEXT REFERENCES workflows(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'active',
            tags_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_customers_email
        ON customers(email);

        CREATE TABLE IF NOT EXISTS customer_events (
            id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            workflow_id TEXT REFERENCES workflows(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_customer_events_customer
        ON customer_events(customer_id, created_at);

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT REFERENCES workflows(id) ON DELETE CASCADE,
            job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_workflow
        ON events(workflow_id, created_at);

        CREATE TABLE IF NOT EXISTS telegram_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            thread_id INTEGER NOT NULL,
            topic_key TEXT NOT NULL,
            slug TEXT NOT NULL,
            title TEXT NOT NULL,
            purpose TEXT NOT NULL,
            lane TEXT NOT NULL DEFAULT '',
            workflow_id TEXT REFERENCES workflows(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'active',
            icon_custom_emoji_id TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            seed_message_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_topics_chat_thread
        ON telegram_topics(chat_id, thread_id);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_topics_chat_key
        ON telegram_topics(chat_id, topic_key);

        CREATE INDEX IF NOT EXISTS idx_telegram_topics_workflow
        ON telegram_topics(workflow_id, updated_at);

        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL DEFAULT 1,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT REFERENCES workflows(id) ON DELETE CASCADE,
            job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            step_name TEXT NOT NULL,
            route TEXT NOT NULL DEFAULT '',
            model_used TEXT NOT NULL DEFAULT '',
            cost_usd REAL NOT NULL DEFAULT 0.0,
            duration_seconds REAL NOT NULL DEFAULT 0.0,
            quality_score REAL,
            outcome_type TEXT NOT NULL DEFAULT 'success',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_outcomes_workflow
        ON outcomes(workflow_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_outcomes_type
        ON outcomes(outcome_type, created_at);

        -- Cost attribution table (TIER-0 #1, 2026-04-23) — links every
        -- outcome row to its eventual workflow terminal status + revenue
        -- attribution. Lets daily ROI digest answer: "$ spent on workflows
        -- that produced revenue vs $ spent on workflows that died."
        -- Pure additive table; no FK cascade so reverting is safe.
        CREATE TABLE IF NOT EXISTS cost_attribution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id INTEGER NOT NULL,
            workflow_id TEXT NOT NULL DEFAULT '',
            workflow_kind TEXT NOT NULL DEFAULT '',
            terminal_status TEXT NOT NULL DEFAULT 'pending',
            converted_to_revenue INTEGER NOT NULL DEFAULT 0,
            revenue_usd REAL NOT NULL DEFAULT 0.0,
            attributed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(outcome_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cost_attribution_workflow
            ON cost_attribution(workflow_id);
        CREATE INDEX IF NOT EXISTS idx_cost_attribution_kind_status
            ON cost_attribution(workflow_kind, terminal_status, attributed_at);
        CREATE INDEX IF NOT EXISTS idx_cost_attribution_revenue
            ON cost_attribution(converted_to_revenue, attributed_at);

        -- Experiments table (TIER-1 #3, 2026-04-23) — hypothesis-generation
        -- + outcome loop. experiment-engine.py reads/writes this. Without
        -- the table, the script silently fails to dispatch.
        CREATE TABLE IF NOT EXISTS experiments (
            id TEXT PRIMARY KEY,
            skill_name TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            measure_at TEXT,
            outcome_json TEXT NOT NULL DEFAULT '{}',
            cost_usd REAL NOT NULL DEFAULT 0.0,
            quality_score REAL,
            created_at TEXT NOT NULL,
            launched_at TEXT,
            measured_at TEXT,
            promoted_pattern_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_experiments_status
            ON experiments(status, measure_at);
        CREATE INDEX IF NOT EXISTS idx_experiments_skill
            ON experiments(skill_name, created_at);

        -- Email threads (TIER-3.5 #A1, 2026-04-23) — preserves Gmail thread
        -- context across inbound/outbound so Rick replies hit the right
        -- conversation. Without this, every Resend send is orphaned in Gmail.
        CREATE TABLE IF NOT EXISTS email_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            gmail_thread_id TEXT,
            prospect_id TEXT,
            root_message_id TEXT,
            subject TEXT NOT NULL DEFAULT '',
            participants_json TEXT NOT NULL DEFAULT '[]',
            last_inbound_at TEXT,
            last_outbound_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            intent_class TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(thread_id)
        );
        CREATE INDEX IF NOT EXISTS idx_email_threads_gmail
            ON email_threads(gmail_thread_id);
        CREATE INDEX IF NOT EXISTS idx_email_threads_prospect
            ON email_threads(prospect_id);
        CREATE INDEX IF NOT EXISTS idx_email_threads_status
            ON email_threads(status, last_inbound_at DESC);

        -- Follow-up queue (TIER-3.5 #A2, 2026-04-23) — adaptive cadence by
        -- intent (cold=7d, warm=4d, hot=2d, engaged-then-silent=24h). Hard
        -- cap 4 follow-ups per thread before status='closed_lost'.
        CREATE TABLE IF NOT EXISTS follow_up_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id TEXT,
            thread_id TEXT NOT NULL,
            follow_up_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 4,
            last_intent TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            draft_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_follow_up_queue_due
            ON follow_up_queue(status, follow_up_at);
        CREATE INDEX IF NOT EXISTS idx_follow_up_queue_thread
            ON follow_up_queue(thread_id);

        CREATE INDEX IF NOT EXISTS idx_approvals_workflow
        ON approvals(workflow_id);

        CREATE INDEX IF NOT EXISTS idx_approvals_job
        ON approvals(job_id);

        CREATE INDEX IF NOT EXISTS idx_artifacts_job
        ON artifacts(job_id);

        CREATE INDEX IF NOT EXISTS idx_customer_events_workflow
        ON customer_events(workflow_id);

        CREATE INDEX IF NOT EXISTS idx_events_type
        ON events(event_type);

        CREATE INDEX IF NOT EXISTS idx_events_job
        ON events(job_id);

        CREATE INDEX IF NOT EXISTS idx_workflows_status
        ON workflows(status, created_at);

        CREATE INDEX IF NOT EXISTS idx_jobs_workflow
        ON jobs(workflow_id);

        CREATE INDEX IF NOT EXISTS idx_jobs_status
        ON jobs(status);

        CREATE INDEX IF NOT EXISTS idx_outcomes_job
        ON outcomes(job_id);

        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            thread_id INTEGER,
            topic_key TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL,
            sender TEXT NOT NULL DEFAULT 'unknown',
            message_text TEXT NOT NULL,
            message_id INTEGER,
            workflow_id TEXT REFERENCES workflows(id) ON DELETE SET NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_conversation_messages_chat_thread
        ON conversation_messages(chat_id, thread_id, created_at);

        CREATE INDEX IF NOT EXISTS idx_conversation_messages_topic
        ON conversation_messages(topic_key, created_at);

        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_chat_id TEXT NOT NULL DEFAULT '',
            target_thread_id INTEGER,
            topic_key TEXT NOT NULL DEFAULT '',
            message_text TEXT NOT NULL,
            telegram_message_id INTEGER,
            status TEXT NOT NULL DEFAULT 'sent',
            error TEXT NOT NULL DEFAULT '',
            workflow_id TEXT REFERENCES workflows(id) ON DELETE SET NULL,
            purpose TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_notification_log_created
        ON notification_log(created_at);

        CREATE TABLE IF NOT EXISTS notification_dedupe (
            dedup_hash TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_alerted_at TEXT NOT NULL,
            count_since_alert INTEGER NOT NULL DEFAULT 0,
            last_text TEXT NOT NULL DEFAULT '',
            total_seen INTEGER NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_notification_dedupe_kind_alerted
        ON notification_dedupe(kind, last_alerted_at);

        CREATE TABLE IF NOT EXISTS scheduled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_key TEXT NOT NULL UNIQUE,
            topic_key TEXT NOT NULL DEFAULT '',
            cron_expression TEXT NOT NULL,
            template_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_sent_at TEXT
        );

        CREATE TABLE IF NOT EXISTS prospect_pipeline (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            profile_url TEXT NOT NULL DEFAULT '',
            score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'intake',
            notes TEXT NOT NULL DEFAULT '',
            last_contact_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_prospect_pipeline_status
        ON prospect_pipeline(status, score DESC);

        CREATE INDEX IF NOT EXISTS idx_prospect_pipeline_username
        ON prospect_pipeline(username);

        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            stripe_customer_id TEXT NOT NULL DEFAULT '',
            subscription_id TEXT NOT NULL DEFAULT '',
            business_name TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            industry TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'provisioning',
            config_json TEXT NOT NULL DEFAULT '{}',
            health_score REAL NOT NULL DEFAULT 100,
            monthly_value_usd REAL NOT NULL DEFAULT 0,
            last_serviced_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tenants_status
        ON tenants(status);

        CREATE TABLE IF NOT EXISTS tenant_health_history (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            score REAL NOT NULL DEFAULT 0,
            signals_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tenant_health_history_tenant
        ON tenant_health_history(tenant_id, created_at);

        CREATE TABLE IF NOT EXISTS email_subscribers (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT '',
            subscribed_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_email_subscribers_status
        ON email_subscribers(status);

        CREATE TABLE IF NOT EXISTS affiliates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            profile_url TEXT NOT NULL DEFAULT '',
            referral_code TEXT NOT NULL DEFAULT '',
            commission_rate REAL NOT NULL DEFAULT 0.30,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_affiliates_status
        ON affiliates(status);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_affiliates_referral_code
        ON affiliates(referral_code);

        CREATE TABLE IF NOT EXISTS referrals (
            id TEXT PRIMARY KEY,
            affiliate_id TEXT NOT NULL REFERENCES affiliates(id) ON DELETE CASCADE,
            customer_id TEXT REFERENCES customers(id) ON DELETE SET NULL,
            commission_cents INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_referrals_affiliate
        ON referrals(affiliate_id);

        CREATE TABLE IF NOT EXISTS fleet_benchmarks (
            id TEXT PRIMARY KEY,
            industry TEXT NOT NULL DEFAULT '',
            metric_name TEXT NOT NULL DEFAULT '',
            value REAL NOT NULL DEFAULT 0,
            sample_size INTEGER NOT NULL DEFAULT 0,
            computed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_fleet_benchmarks_industry
        ON fleet_benchmarks(industry, computed_at);

        CREATE TABLE IF NOT EXISTS tenant_insights (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            insight_type TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tenant_insights_tenant
        ON tenant_insights(tenant_id, created_at);

        -- Subagent heartbeat — tracks every delegation from dispatch through
        -- merge. Before this table, `status='dispatched'` was terminal in
        -- subagents.py and nothing ever advanced it. The 2026-04-21 audit
        -- found 136 of 136 subagent runs in 30 days ghosted. Schema lets
        -- the reaper (in engine.py heartbeat) re-queue stuck dispatches and
        -- the merger fold subagent outputs back into parent workflow context.
        CREATE TABLE IF NOT EXISTS subagent_heartbeat (
            run_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            parent_workflow_id TEXT REFERENCES workflows(id) ON DELETE CASCADE,
            parent_job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            parent_fanout_id TEXT,
            status TEXT NOT NULL,
            pid INTEGER,
            output_path TEXT NOT NULL DEFAULT '',
            task TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL DEFAULT '{}',
            output_json TEXT,
            error TEXT NOT NULL DEFAULT '',
            cost_estimate_usd REAL NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            cost_cap_usd REAL NOT NULL DEFAULT 5.0,
            usage_tokens_in INTEGER NOT NULL DEFAULT 0,
            usage_tokens_out INTEGER NOT NULL DEFAULT 0,
            usage_tokens_cache_read INTEGER NOT NULL DEFAULT 0,
            usage_tokens_cache_write INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            thinking_level TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            last_beat_at TEXT NOT NULL,
            lease_until TEXT NOT NULL,
            finished_at TEXT,
            merged_at TEXT,
            attempt INTEGER NOT NULL DEFAULT 1,
            ghosted INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_subagent_hb_status_lease
            ON subagent_heartbeat(status, lease_until);
        CREATE INDEX IF NOT EXISTS idx_subagent_hb_parent
            ON subagent_heartbeat(parent_workflow_id, status);
        CREATE INDEX IF NOT EXISTS idx_subagent_hb_fanout
            ON subagent_heartbeat(parent_fanout_id, status);
        CREATE INDEX IF NOT EXISTS idx_subagent_hb_kind_started
            ON subagent_heartbeat(kind, started_at);

        -- Skill variants — the A/B test ledger for every Rick skill/step.
        -- Thompson sampling picker in runtime/variants.py uses n_runs + wins
        -- + losses to roll a variant per invocation. Retired losers get
        -- status='retired' so picker skips them; new mutations (from
        -- prompt-evolution) land as status='active' with parent_variant_id
        -- set for lineage tracking.
        CREATE TABLE IF NOT EXISTS skill_variants (
            id TEXT PRIMARY KEY,
            skill_name TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            parent_variant_id TEXT,
            n_runs INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            sum_quality REAL NOT NULL DEFAULT 0,
            sum_cost REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            retired_at TEXT,
            UNIQUE(skill_name, variant_id)
        );
        CREATE INDEX IF NOT EXISTS idx_skill_variants_skill_status
            ON skill_variants(skill_name, status);

        -- Effective patterns — cross-skill knowledge transfer store.
        -- When one skill discovers a winning copy pattern (e.g. subject-line
        -- CTA), pattern-miner extracts it here and variants.py injects the
        -- top-3 matching patterns as few-shot examples in future mutations
        -- across any skill listed in applicable_skills. This is how Rick's
        -- learning compounds across domains instead of staying siloed.
        CREATE TABLE IF NOT EXISTS effective_patterns (
            id TEXT PRIMARY KEY,
            pattern_kind TEXT NOT NULL,
            snippet TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            applicable_skills TEXT NOT NULL DEFAULT '[]',
            sum_wins INTEGER NOT NULL DEFAULT 0,
            sum_runs INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_effective_patterns_kind
            ON effective_patterns(pattern_kind, sum_wins DESC);

        -- Outbound jobs — unified queue for every outbound touch across
        -- email / moltbook / reddit / linkedin / threads / instagram / etc.
        -- fan_out(lead, template, channels) writes one row per channel here;
        -- outbound_dispatcher drain picks queued rows, enforces
        -- kill_switches.assert_channel_active, enforces per-channel
        -- rate-limits from config/channel-limits.json, and calls the matching
        -- formatter. Keeps every channel behind one queue with uniform safety.
        CREATE TABLE IF NOT EXISTS outbound_jobs (
            id TEXT PRIMARY KEY,
            lead_id TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL,
            template_id TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'queued',
            scheduled_at TEXT NOT NULL,
            last_error TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            result_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_outbound_status_sched
            ON outbound_jobs(status, scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_outbound_channel_status
            ON outbound_jobs(channel, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_outbound_lead
            ON outbound_jobs(lead_id, created_at);

        -- Lead aliases — cross-channel attribution (2026-04-22).
        -- Same human shows up as info@acme.co (email), @acmeco (twitter),
        -- linkedin.com/in/founder (linkedin), reddit.com/u/acme_founder (reddit).
        -- All these aliases map back to ONE prospect_pipeline.id so when a
        -- reply lands via email, Rick knows the original lead came from Reddit.
        CREATE TABLE IF NOT EXISTS lead_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id TEXT NOT NULL,
            alias_value TEXT NOT NULL,
            alias_type TEXT NOT NULL,
            source_channel TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            UNIQUE(alias_value, alias_type)
        );
        CREATE INDEX IF NOT EXISTS idx_lead_aliases_prospect
            ON lead_aliases(prospect_id);
        CREATE INDEX IF NOT EXISTS idx_lead_aliases_lookup
            ON lead_aliases(alias_value, alias_type);

        -- Founder graph edges — who-discovered-whom + cross-source signals
        -- (2026-04-22 cornerstone-2). Each edge says "prospect A and prospect B
        -- co-occur in the same context" (HN front-page same day, mutual GitHub
        -- follows, IndieHackers product collab). Compounds: every new edge
        -- raises the confidence that A and B are real founders worth pitching.
        CREATE TABLE IF NOT EXISTS prospect_graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_prospect_id TEXT NOT NULL,
            dst_prospect_id TEXT NOT NULL,
            edge_kind TEXT NOT NULL,        -- 'cooccur_hn' | 'cooccur_ih' | 'github_follow' | 'mention' | 'comment'
            evidence_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL,           -- 'hn' | 'ih' | 'github' | 'whois'
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            UNIQUE(src_prospect_id, dst_prospect_id, edge_kind)
        );
        CREATE INDEX IF NOT EXISTS idx_prospect_graph_edges_src
            ON prospect_graph_edges(src_prospect_id, edge_kind);
        CREATE INDEX IF NOT EXISTS idx_prospect_graph_edges_dst
            ON prospect_graph_edges(dst_prospect_id, edge_kind);

        -- Analytics snapshots — daily ingest from GA4 / GSC / Ahrefs / Lighthouse.
        -- Source is one of ga4|gsc|ahrefs|ahrefs_audit|lighthouse. metric_value
        -- is for numerics; metric_str for URLs/keywords/etc. dim_json holds
        -- per-row dimensions (page path, query text, country, etc).
        CREATE TABLE IF NOT EXISTS analytics_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL,
            metric_str TEXT NOT NULL DEFAULT '',
            dim_json TEXT NOT NULL DEFAULT '{}',
            snapshot_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_analytics_source_date
            ON analytics_snapshots(source, snapshot_date);
        CREATE INDEX IF NOT EXISTS idx_analytics_metric_date
            ON analytics_snapshots(metric_name, snapshot_date);

        -- Channel state — kill-switch + rate-limit-counter per outbound channel.
        -- outbound_dispatcher reads this before every send; kill_switches
        -- writes status='paused' when thresholds breach (bounce > 5%,
        -- shadowban detected, 401 auth failure × 2, DNC hit, etc).
        -- paused_until is a soft pause (auto-resume); hard pause uses
        -- status='disabled' with no auto-resume.
        CREATE TABLE IF NOT EXISTS channel_state (
            channel TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'active',
            sends_today INTEGER NOT NULL DEFAULT 0,
            sends_this_minute INTEGER NOT NULL DEFAULT 0,
            last_send_at TEXT,
            paused_until TEXT,
            pause_reason TEXT NOT NULL DEFAULT '',
            bounce_count_7d INTEGER NOT NULL DEFAULT 0,
            complaint_count_7d INTEGER NOT NULL DEFAULT 0,
            auth_failure_streak INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        -- Ledger entries (TIER-0 #5, 2026-04-23) — SQL-side mirror of the
        -- JSONL execution ledger. escalate_stuck_workflows() in engine.py
        -- queries this table to dedupe per-workflow escalations within 7 days.
        -- Without the table, the SELECT throws OperationalError (caught) and
        -- the same stuck workflow gets re-alerted on every heartbeat tick.
        -- Columns chosen to match engine.py:5573 SELECT (entry_kind, entry_notes,
        -- created_at) plus the kwargs passed to append_execution_ledger() so
        -- a future engine.py change can dual-write here without another migration.
        CREATE TABLE IF NOT EXISTS ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_kind TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            entry_status TEXT NOT NULL DEFAULT '',
            area TEXT NOT NULL DEFAULT '',
            project TEXT NOT NULL DEFAULT '',
            route TEXT NOT NULL DEFAULT '',
            entry_notes TEXT NOT NULL DEFAULT '',
            impact TEXT NOT NULL DEFAULT '',
            artifacts_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ledger_entries_kind_created
            ON ledger_entries(entry_kind, created_at);
        CREATE INDEX IF NOT EXISTS idx_ledger_entries_notes
            ON ledger_entries(entry_notes);
        """
    )
    migrate_db(connection)
    from datetime import datetime

    connection.execute(
        "INSERT OR IGNORE INTO schema_version (id, version, applied_at) VALUES (1, 1, ?)",
        (datetime.now().isoformat(timespec="seconds"),),
    )
    connection.commit()
