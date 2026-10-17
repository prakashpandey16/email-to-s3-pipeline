"""
Pipeline Tracking — RunMetrics, audit logging utilities.
No global config imports.
"""

import os
import csv
import threading
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Audit Log Setup
audit_lock = threading.Lock()
_audit_log_path = None  # Will be set by init_tracking()


def init_tracking(log_dir: str):
    """Initialize the audit log path. Must be called before log_audit()."""
    global _audit_log_path
    _audit_log_path = os.path.join(log_dir, "audit_log.csv")
    setup_audit_log()


def setup_audit_log():
    """Ensure audit log header exists."""
    if not _audit_log_path:
        return
    try:
        if not os.path.exists(_audit_log_path):
            os.makedirs(os.path.dirname(_audit_log_path), exist_ok=True)
            with open(_audit_log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "email_subject", "filename", "matched_rule", "match_basis", "s3_path", "status"])
    except Exception as e:
        logger.error(f"Failed to initialize audit log: {e}")


def log_audit(subject, filename, rule, match_basis, s3_path, status):
    """Thread-safe append to audit log CSV."""
    if not _audit_log_path:
        return
    try:
        with audit_lock:
            with open(_audit_log_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().isoformat(),
                    subject,
                    filename,
                    rule or "N/A",
                    match_basis or "N/A",
                    s3_path or "N/A",
                    status,
                ])
    except Exception as e:
        logger.error(f"Failed to write to audit log: {e}")


@dataclass
class RunMetrics:
    emails_scanned: int = 0
    matched_rules: int = 0
    skipped_no_rule: int = 0
    skipped_duplicate: int = 0
    emails_found_with_attachment: int = 0
    emails_found_with_link: int = 0
    emails_processed_successfully: int = 0
    files_uploaded: int = 0
    emails_failed: int = 0
    failed_downloads: int = 0

    # Granular Rejection Metrics
    rejected_by_subject_mismatch: int = 0
    rejected_by_filename_mismatch: int = 0
    rejected_by_extension_policy: int = 0
    attachments_evaluated: int = 0

    def to_dict(self):
        return self.__dict__


def merge_metrics(target: RunMetrics, source: RunMetrics):
    """Aggregate metrics from worker threads."""
    target.emails_scanned += source.emails_scanned
    target.matched_rules += source.matched_rules
    target.skipped_no_rule += source.skipped_no_rule
    target.skipped_duplicate += source.skipped_duplicate
    target.emails_found_with_attachment += source.emails_found_with_attachment
    target.emails_found_with_link += source.emails_found_with_link
    target.emails_processed_successfully += source.emails_processed_successfully
    target.files_uploaded += source.files_uploaded
    target.emails_failed += source.emails_failed
    target.failed_downloads += source.failed_downloads
    target.rejected_by_subject_mismatch += source.rejected_by_subject_mismatch
    target.rejected_by_filename_mismatch += source.rejected_by_filename_mismatch
    target.rejected_by_extension_policy += source.rejected_by_extension_policy
    target.attachments_evaluated += source.attachments_evaluated
