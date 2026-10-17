"""
Email Processing — Core worker logic for processing a single email.
No global config imports. Everything is received via function parameters.
"""

import logging
import hashlib
import os
import shutil
import tempfile
from datetime import datetime
from typing import List, Dict

from config.database import DBClient
from connectors.outlook import OutlookConnector
from connectors.s3_upload import S3Connector
from processors.downloader import FileDownloader, extract_and_collect_files
from processors.excel_splitter import split_excel_into_sheets
from processors.path_resolver import PathResolver

from utils.decorators import monitor_and_log
from utils.pipeline_tracking import RunMetrics, log_audit
from utils.content_processing import extract_download_url, sanitize_filename, normalize_for_matching as normalize_text
from utils.enums import PolicyReason

logger = logging.getLogger(__name__)


def match_email_rule(subject: str, resolver: PathResolver, sender: str = None) -> bool:
    """Checks if the email subject or sender matches any configured Ingestion Rule."""
    if resolver.is_subject_relevant(subject):
        return True
    if sender and resolver.is_sender_relevant(sender):
        return True
    return False


@monitor_and_log(log_message="Error processing file item", default_return="ERROR")
def process_file_item(
    file_item: Dict,
    original_filename: str,
    subject: str,
    s3: S3Connector,
    db: DBClient,
    resolver: PathResolver,
    metrics: RunMetrics = None,
    is_split: bool = False,
    sender: str = None,
) -> str:
    """
    Core logic to Process a Single File:
    1. Calculate SHA256 Hash.
    2. Check Database for Duplicates (Idempotency).
    3. Upload to S3 if unique.
    4. Mark as processed in Database.

    Returns: "SUCCESS", "DUPLICATE", "NO_RULE", "ERROR"
    """
    file_path = file_item["path"]
    job_name = normalize_text(subject)

    safe_original_name = sanitize_filename(original_filename)
    name, ext = os.path.splitext(safe_original_name)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    safe_original_name = f"{name}_{timestamp}{ext}"

    if metrics:
        metrics.attachments_evaluated += 1

    # Resolve S3 Target Path
    s3_key, rule_name, match_type = resolver.resolve_s3_key(subject, safe_original_name, is_split=is_split, sender=sender)

    if not s3_key:
        logger.debug(f"[Policy] File '{safe_original_name}' rejected: {PolicyReason.FILENAME_MISMATCH.value}")
        if metrics:
            metrics.rejected_by_filename_mismatch += 1
        log_audit(subject, safe_original_name, None, None, None, "STRICT_REJECT_NO_RULE")
        return "NO_RULE"

    # Calculate SHA256 Hash
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    file_hash = hasher.hexdigest()

    # Duplicate Check (Idempotency)
    if db.is_file_processed(file_hash, job_name):
        logger.warning(f"[Idempotency] Skipping duplicate: '{safe_original_name}'")
        log_audit(subject, safe_original_name, rule_name, match_type, s3_key, "DUPLICATE")
        return "DUPLICATE"

    # Upload to S3
    s3.upload_file(file_path, s3_key, original_filename=safe_original_name)

    # Mark Complete in DB
    db.mark_file_processed(safe_original_name, file_hash, s3_key, job_name)
    log_audit(subject, safe_original_name, rule_name, match_type, s3_key, "SUCCESS")

    return "SUCCESS"


def finalize_email_state(
    email_id: str,
    results: List[str],
    outlook: OutlookConnector,
    metrics: RunMetrics,
    duplicate_policy: str = "MARK_READ_AND_SKIP",
):
    """
    Decides what to do with the Email after processing its files.
    - SUCCESS => Mark Read.
    - ALL DUPLICATE => Mark Read (per policy).
    - Error => Leave Unread.
    """
    has_success = "SUCCESS" in results
    has_duplicate = "DUPLICATE" in results

    if has_success:
        outlook.mark_email_processed(email_id)
        metrics.emails_processed_successfully += 1
        metrics.matched_rules += 1
        return

    if has_duplicate and not has_success:
        if duplicate_policy == "MARK_READ_AND_SKIP":
            outlook.mark_email_processed(email_id)
            logger.info("Email fully skipped as duplicate (Marked Read per policy).")
        else:
            logger.info("Email fully skipped as duplicate (Left UNREAD per policy).")
        metrics.skipped_duplicate += 1
        metrics.matched_rules += 1
        return

    if not results or all(r == "NO_RULE" for r in results):
        if results and all(r == "NO_RULE" for r in results):
            logger.info("[Strict Mode] Email skipped. Subject matched, but no valid payloads found.")
        return

    metrics.emails_failed += 1
    logger.error("Email failed to process (Left Unread).")


def process_file_list(
    files: List[Dict[str, str]],
    subject: str,
    s3: S3Connector,
    db: DBClient,
    resolver: PathResolver,
    metrics: RunMetrics,
    results: List[str],
    sender: str = None,
):
    """Iterates through downloaded files, handles ZIP/GZIP, calls process_file_item."""
    for item in files:
        original_filename = item["name"]
        expanded_files = extract_and_collect_files(item["path"], os.path.dirname(item["path"]))

        if not expanded_files:
            logger.warning(f"No valid files found in {original_filename}")
            metrics.failed_downloads += 1
            results.append("ERROR")
            continue

        # Check for Excel files that need splitting
        final_files = []
        for f_info in expanded_files:
            _, ext = os.path.splitext(f_info["name"])
            if ext.lower() in [".xlsx", ".xls"]:
                split_results = split_excel_into_sheets(f_info["path"], os.path.dirname(f_info["path"]))
                if split_results:
                    logger.info(f"Split {f_info['name']} into {len(split_results)} sheets.")
                    for res in split_results:
                        res["is_split"] = True
                    final_files.extend(split_results)
                else:
                    final_files.append(f_info)
            else:
                final_files.append(f_info)

        for f_info in final_files:
            effective_original = f_info["name"]
            is_split = f_info.get("is_split", False)
            res = process_file_item(f_info, effective_original, subject, s3, db, resolver, metrics, is_split=is_split, sender=sender)
            results.append(res)
            if res == "SUCCESS":
                metrics.files_uploaded += 1


def process_single_email(
    email: dict,
    db_config: dict,
    run_id: str,
    outlook: OutlookConnector,
    s3: S3Connector,
    downloader: FileDownloader,
    resolver: PathResolver,
    pipeline_config=None,
) -> RunMetrics:
    """
    Worker Function: Processes ONE single email completely.
    Thread-Safe: Creates its own DB connection.
    """
    files_metrics = RunMetrics()
    files_metrics.emails_scanned = 1

    db = DBClient(db_config, run_id=run_id)

    # Get config values with safe defaults
    junk_extensions = pipeline_config.junk_extensions if pipeline_config else [".png", ".jpg", ".jpeg", ".gif", ".bmp"]
    duplicate_policy = pipeline_config.duplicate_handle_policy if pipeline_config else "MARK_READ_AND_SKIP"
    download_url_pattern = pipeline_config.download_url_pattern if pipeline_config else None

    try:
        db.connect()
        msg_id = email["id"]
        subject = email["subject"]
        sender = email.get("from", {}).get("emailAddress", {}).get("address")
        body_content = email.get("body", {}).get("content", "")

        safe_subject = subject.encode("ascii", "replace").decode("ascii")
        logger.info(f"[Ingest] Processing: '{safe_subject}'")
        db.log_step("INFO", f"Processing: {safe_subject}")

        results = []

        try:
            has_attachment = email.get("hasAttachments", False)
            download_link = extract_download_url(body_content, download_url_pattern) if download_url_pattern else None

            if has_attachment:
                files_metrics.emails_found_with_attachment = 1
            if download_link:
                files_metrics.emails_found_with_link = 1

            # Subject/Sender match check
            if not match_email_rule(subject, resolver, sender=sender):
                logger.debug(f"Skipping '{subject}' - Subject pattern mismatch (Worker).")
                files_metrics.rejected_by_subject_mismatch += 1
                return files_metrics

            # Handle Download Link
            if download_link:
                try:
                    f_name_link, link_stream = downloader.download_stream(download_link)
                    with tempfile.TemporaryDirectory() as temp_dir:
                        file_path = os.path.join(temp_dir, f_name_link)
                        with open(file_path, "wb") as f:
                            shutil.copyfileobj(link_stream, f)
                        if hasattr(link_stream, "close"):
                            link_stream.close()
                        process_file_list(
                            [{"name": f_name_link, "path": file_path}],
                            subject, s3, db, resolver, files_metrics, results, sender=sender,
                        )
                except Exception as e:
                    logger.error(f"Link download failed: {e}")
                    db.log_step("ERROR", f"Link download failed: {e}")
                    files_metrics.failed_downloads += 1
                    results.append("ERROR")

            # Handle Attachments
            if has_attachment:
                try:
                    with tempfile.TemporaryDirectory() as temp_dir:
                        att_metas = outlook.list_attachments_metadata(msg_id)
                        downloaded_items = []

                        for meta in att_metas:
                            att_id = meta["id"]
                            att_name = meta["name"]
                            
                            target_key, rule_name, match_type = resolver.resolve_s3_key(subject, att_name, sender=sender)
                            if not target_key:
                                files_metrics.attachments_evaluated += 1
                                if any(att_name.lower().endswith(ext) for ext in junk_extensions):
                                    logger.debug(f"[Policy] Auto-Skipping: '{att_name}' ({PolicyReason.EXTENSION_POLICY.value})")
                                    files_metrics.rejected_by_extension_policy += 1
                                else:
                                    logger.info(f"[Policy] Rejected: '{att_name}'. {PolicyReason.NO_RULE_MATCH.value}")
                                    files_metrics.rejected_by_filename_mismatch += 1
                                continue

                            logger.info(f"[Ingest] Rule Match for '{att_name}'. Downloading...")
                            save_path = os.path.join(temp_dir, os.path.basename(att_name))
                            success = outlook.download_attachment(msg_id, att_id, save_path)
                            if success:
                                downloaded_items.append({"name": os.path.basename(att_name), "path": save_path})
                            else:
                                logger.error(f"Failed to download '{att_name}'")
                                files_metrics.failed_downloads += 1
                                results.append("ERROR")

                        if downloaded_items:
                            process_file_list(downloaded_items, subject, s3, db, resolver, files_metrics, results, sender=sender)

                except Exception as e:
                    logger.error(f"Attachment processing failed: {e}")
                    db.log_step("ERROR", f"Attachment processing failed: {e}")
                    results.append("ERROR")

            # Finalize Email
            finalize_email_state(msg_id, results, outlook, files_metrics, duplicate_policy)

        except Exception as e:
            raise e

    except Exception as e:
        logger.error(f"Critical error processing email: {e}")
        if db:
            db.log_step("ERROR", f"Critical error: {e}")
        files_metrics.emails_failed = 1

    finally:
        if db:
            db.close()

    return files_metrics
