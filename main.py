"""
Email-to-S3 Pipeline — Meta-Driven Orchestrator.

Reads all configuration from Postgres (email_to_s3_config schema),
builds a typed PipelineManifest, and drives the entire pipeline
without any hardcoded config files.

Usage:
    python main.py
    python main.py --batch_id 1
"""

import logging
import sys
import os
import signal
import argparse
import concurrent.futures

from config.config import db_config, LOG_DIR
from config.database import DBClient
from config.metadata_loader import MetadataLoader

from connectors.outlook import OutlookConnector
from connectors.s3_upload import S3Connector
from processors.downloader import FileDownloader
from processors.path_resolver import PathResolver
from processors.email_processing import process_single_email, match_email_rule

from utils.pipeline_tracking import RunMetrics, merge_metrics, init_tracking
from utils.reporting import PipelineReporter

# ─── Logging Setup ────────────────────────────────────────────────────────

log_file_path = os.path.join(LOG_DIR, "pipeline.log")
init_tracking(LOG_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file_path),
    ],
)
logger = logging.getLogger(__name__)

DEFAULT_BATCH_ID = 1


def shutdown_handler(sig, frame):
    """Gracefully handle termination signals."""
    logger.warning("Received termination signal")
    sys.exit(1)


signal.signal(signal.SIGTERM, shutdown_handler)


def main(batch_id: int = DEFAULT_BATCH_ID):
    """
    Main Orchestration Loop:
    1. Load PipelineManifest from DB.
    2. Initialize Connectors with manifest config.
    3. Fetch Unread Emails.
    4. Spin up Worker Threads to process emails.
    5. Aggregate Results, Report, and Update Run Status.
    """
    logger.info("=" * 70)
    logger.info("Email-to-S3 Pipeline — Meta-Driven Engine")
    logger.info(f"Batch ID: {batch_id}")
    logger.info("=" * 70)

    # ── Step 1: Load Manifest from Database ───────────────────────────────
    logger.info("Loading pipeline manifest from database...")
    loader_db = DBClient(db_config)
    loader = MetadataLoader(loader_db)
    manifest = loader.load(batch_id)
    loader_db.close()

    if not manifest:
        logger.critical(f"Cannot start: Manifest for '{batch_id}' not found or invalid.")
        sys.exit(1)

    if not manifest.mappings:
        logger.critical("Cannot start: No active metadata mappings found.")
        sys.exit(1)

    pc = manifest.pipeline_config
    logger.info(f"Manifest loaded: {len(manifest.mappings)} mapping(s), workers={pc.max_workers}")

    # ── Step 2: Initialize DB Run ─────────────────────────────────────────
    main_metrics = RunMetrics()
    db = DBClient(db_config)

    try:
        run_id = db.start_run(batch_id=manifest.batch_id)
        db.log_step("INFO", f"Pipeline initialized. Batch ID: {batch_id}, Run: {run_id}")

        # ── Step 3: Process Each Mapping ──────────────────────────────────
        # Each mapping = one Source → Target → RuleSet combination
        for mapping in manifest.mappings:
            src = mapping.source
            tgt = mapping.target
            rs = mapping.rule_set

            logger.info(f"Processing mapping: {src.mailbox_address} -> s3://{tgt.bucket_name} ({rs.rule_set_name})")

            # Initialize Connectors with this mapping's config
            outlook = OutlookConnector(source_config=src, pipeline_config=pc)
            s3 = S3Connector(target_config=tgt)
            downloader = FileDownloader(pipeline_config=pc)
            resolver = PathResolver(rules=rs.rules, match_threshold=pc.match_threshold)

            # Fetch Emails
            emails = outlook.fetch_unread_emails()
            count = len(emails)
            db.log_step("INFO", f"Fetched {count} unread emails from {src.mailbox_address}/{src.target_folder}")

            if not emails:
                logger.info("No unread emails found for this mapping.")
                continue

            # Pre-Filtering
            eligible_emails = []
            seen_message_ids = set()

            for idx, email in enumerate(emails, 1):
                if idx % 5 == 0 or idx == 1 or idx == count:
                    logger.info(f"Scanning email {idx}/{count}...")

                subject = email["subject"]
                sender = email.get("from", {}).get("emailAddress", {}).get("address")
                msg_id = email.get("internetMessageId")

                # Dedup within batch
                if msg_id and msg_id in seen_message_ids:
                    logger.warning(f"[Dedupe] Skipping duplicate: '{subject}'")
                    main_metrics.skipped_duplicate += 1
                    continue
                if msg_id:
                    seen_message_ids.add(msg_id)

                # Subject/Sender Match
                if match_email_rule(subject, resolver, sender=sender):
                    eligible_emails.append(email)
                else:
                    # Deep Pre-Scan: Check attachments metadata
                    if pc.deep_scan:
                        try:
                            att_metas = outlook.list_attachments_metadata(email["id"])
                            is_file_match = False
                            for meta in att_metas:
                                s3_key, _, _ = resolver.resolve_s3_key(subject, meta["name"], sender=sender)
                                if s3_key:
                                    is_file_match = True
                                    logger.info(f"Deep Scan: Match via '{meta['name']}' in '{subject}'")
                                    break

                            if is_file_match:
                                eligible_emails.append(email)
                            else:
                                main_metrics.emails_scanned += 1
                                main_metrics.rejected_by_subject_mismatch += 1
                        except Exception as e:
                            logger.warning(f"Deep Scan failed for '{subject}': {e}")
                            main_metrics.emails_scanned += 1
                            main_metrics.emails_failed += 1
                    else:
                        main_metrics.emails_scanned += 1
                        main_metrics.rejected_by_subject_mismatch += 1

            match_count = len(eligible_emails)
            logger.info(f"Pre-Flight: {match_count}/{count} emails eligible for processing.")

            if not eligible_emails:
                logger.info("No emails matched ingestion rules after pre-filter.")
                continue

            # ── Step 4: Parallel Processing ───────────────────────────────
            logger.info(f"Starting parallel execution. Workers: {pc.max_workers}, Queue: {match_count}")

            with concurrent.futures.ThreadPoolExecutor(max_workers=pc.max_workers) as executor:
                futures = [
                    executor.submit(
                        process_single_email, email, db_config, run_id,
                        outlook, s3, downloader, resolver, pc,
                    )
                    for email in eligible_emails
                ]

                for future in concurrent.futures.as_completed(futures):
                    try:
                        result_metrics = future.result()
                        merge_metrics(main_metrics, result_metrics)
                    except Exception as exc:
                        logger.error(f"Thread exception: {exc}")
                        main_metrics.emails_failed += 1

        # ── Step 5: Finalize Run ──────────────────────────────────────────
        db.update_run_status("SUCCESS", main_metrics.to_dict())

    except Exception as e:
        logger.critical(f"Pipeline crashed: {e}")

        # Send Critical Alert
        try:
            if manifest.notification_config:
                tgt = manifest.mappings[0].target if manifest.mappings else None
                reporter = PipelineReporter(db_config, manifest.notification_config, tgt)
                reporter.send_failure_alert("Uncaught Exception in Main Loop", str(e))
        except Exception as alert_err:
            logger.error(f"Failed to send alert: {alert_err}")

        if "db" in locals():
            db.update_run_status("FAILED", main_metrics.to_dict() if main_metrics else None, str(e))
        sys.exit(1)

    finally:
        if "db" in locals():
            db.close()

        # ── Verdict ───────────────────────────────────────────────────────
        metrics = main_metrics

        if metrics.emails_failed > 0:
            verdict = "ERROR - Data Loss / Processing Failures"
        elif metrics.files_uploaded > 0:
            verdict = "SUCCESS - New Data Ingested"
        elif metrics.skipped_duplicate > 0:
            verdict = "SUCCESS - Idempotent Skip"
        elif metrics.attachments_evaluated > 0 and metrics.files_uploaded == 0:
            verdict = "COMPLETED - Zero Ingestion (Payloads rejected by Policy)"
        elif metrics.emails_scanned == 0:
            verdict = "COMPLETED - No New Data (Target Empty)"
        else:
            verdict = "COMPLETED - Zero Ingestion"

        logger.info("=" * 50)
        logger.info("ETL EXECUTION SUMMARY")
        logger.info(f"  Emails Scanned:         {metrics.emails_scanned}")
        logger.info(f"  Files Uploaded:          {metrics.files_uploaded}")
        logger.info(f"  Duplicates Skipped:      {metrics.skipped_duplicate}")
        logger.info(f"  Subject Mismatches:      {metrics.rejected_by_subject_mismatch}")
        logger.info(f"  Attachments Evaluated:   {metrics.attachments_evaluated}")
        logger.info(f"  Filename Mismatches:     {metrics.rejected_by_filename_mismatch}")
        logger.info(f"  Extension Blocked:       {metrics.rejected_by_extension_policy}")
        logger.info(f"  VERDICT: {verdict}")
        logger.info("=" * 50)

        # ── Inline Reporting ──────────────────────────────────────────────
        if manifest.notification_config:
            try:
                tgt = manifest.mappings[0].target if manifest.mappings else None
                reporter = PipelineReporter(db_config, manifest.notification_config, tgt)
                reporter.generate_daily_report()
            except Exception as e:
                logger.error(f"Failed to trigger inline report: {e}")

        # ── Exit Code ─────────────────────────────────────────────────────
        if metrics.emails_failed > 0 or metrics.failed_downloads > 0:
            logger.error(f"Pipeline completed with failures. Failed={metrics.emails_failed}")

            if manifest.notification_config:
                try:
                    tgt = manifest.mappings[0].target if manifest.mappings else None
                    reporter = PipelineReporter(db_config, manifest.notification_config, tgt)
                    reporter.send_failure_alert(
                        f"Partial Failure - {metrics.emails_failed} emails failed",
                        f"Scanned: {metrics.emails_scanned}, Uploaded: {metrics.files_uploaded}, "
                        f"Failed: {metrics.emails_failed}, Downloads Failed: {metrics.failed_downloads}",
                    )
                except Exception as alert_err:
                    logger.error(f"Failed to send partial failure alert: {alert_err}")

            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email-to-S3 Pipeline")
    parser.add_argument("--batch_id", type=int, default=DEFAULT_BATCH_ID, help="Batch ID to execute")
    args = parser.parse_args()
    main(batch_id=args.batch_id)
