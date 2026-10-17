"""
Metadata Loader — The single source of truth for pipeline configuration.

Reads all configuration from the email_to_s3_config PostgreSQL schema
and compiles it into strongly-typed Python dataclasses.

Usage:
    from config.metadata_loader import MetadataLoader
    from config.config import db_config
    from config.database import DBClient

    loader = MetadataLoader(DBClient(db_config))
    manifest = loader.load("flipkart_lmd_batch")
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional
from utils.encryption import SecretManager

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Domain Models (Typed Dataclasses)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SourceConfig:
    """Outlook / Email source connection details."""
    source_id: int
    source_type: str
    mailbox_address: str
    target_folder: str
    azure_tenant_id: Optional[str] = None
    azure_client_id: Optional[str] = None
    azure_client_secret: Optional[str] = None


@dataclass
class TargetConfig:
    """S3 / Storage target connection details."""
    target_id: int
    target_type: str
    bucket_name: str
    region: str
    base_path: str = ""
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None


@dataclass
class EmailRule:
    """A single matching rule for routing emails/files to S3 paths."""
    rule_id: int
    rule_type: str              # subject_filename | multi_sheet | regex | subject_only | filename_keyword | sender
    subject_keyword: Optional[str] = None
    filename_keyword: Optional[str] = None
    sender_keyword: Optional[str] = None
    regex_pattern: Optional[str] = None
    target_path_suffix: str = ""
    priority: int = 1


@dataclass
class RuleSet:
    """A named collection of EmailRules."""
    rule_set_id: int
    rule_set_name: str
    rules: List[EmailRule] = field(default_factory=list)


@dataclass
class PipelineConfig:
    """Operational tuning for the pipeline engine."""
    max_workers: int = 5
    page_size: int = 100
    batch_size: int = 25
    max_folder_depth: int = 10
    connection_timeout_sec: int = 10
    read_timeout_sec: int = 30
    max_retries: int = 3
    retry_delay_sec: float = 1.0
    max_extracted_size_mb: int = 500
    max_zip_depth: int = 2
    match_threshold: float = 0.60
    strict_mode: bool = True
    deep_scan: bool = True
    download_url_pattern: str = r'https://storage\.googleapis\.com[^\s"\'<>]+'
    duplicate_handle_policy: str = "MARK_READ_AND_SKIP"
    junk_extensions: List[str] = field(default_factory=lambda: [
        ".png", ".jpg", ".jpeg", ".gif", ".bmp",
        ".mp3", ".wav", ".css", ".js", ".ico", ".svg"
    ])


@dataclass
class NotificationConfig:
    """Email notification (SES) settings."""
    sender_email: str = ""
    success_recipient_email: str = ""
    failure_recipient_email: str = ""
    schedule_times: List[str] = field(default_factory=lambda: ["20:00"])
    ses_access_key_id: Optional[str] = None
    ses_secret_access_key: Optional[str] = None
    aws_region: Optional[str] = None


@dataclass
class MetadataMapping:
    """Links a Source → Target → RuleSet within a Batch."""
    metadata_id: int
    source: SourceConfig
    target: TargetConfig
    rule_set: RuleSet


@dataclass
class PipelineManifest:
    """
    The complete operational manifest for a single batch execution.
    Everything the pipeline needs to run is contained here.
    """
    batch_id: int
    batch_name: str
    pipeline_config: PipelineConfig
    notification_config: Optional[NotificationConfig]
    mappings: List[MetadataMapping] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Metadata Loader (Data Access Layer)
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = "email_to_s3_config"


class MetadataLoader:
    """
    Reads the entire pipeline configuration from the email_to_s3_config
    PostgreSQL schema and returns a typed PipelineManifest.
    """

    def __init__(self, db_client):
        self.db = db_client
        self.secrets = SecretManager()

    def load(self, batch_id: int) -> Optional[PipelineManifest]:
        """
        Build the complete PipelineManifest for a given batch.

        Args:
            batch_id: The unique batch ID (e.g. 1)

        Returns:
            PipelineManifest with all configs, or None if batch not found.
        """
        try:
            self.db.connect()

            # ── 1. Batch ─────────────────────────────────────────────────
            batch = self.db.execute_query(
                f"SELECT batch_id, batch_name FROM {SCHEMA}.batch_config_table "
                f"WHERE batch_id = %s AND is_active = TRUE",
                (batch_id,), fetch_one=True
            )
            if not batch:
                logger.error(f"Batch ID '{batch_id}' not found or inactive.")
                return None

            logger.info(f"Loading manifest for batch: {batch['batch_name']} (ID: {batch_id})")

            # ── 2. Pipeline Config ────────────────────────────────────────
            pipeline_config = self._load_pipeline_config(batch_id)

            # ── 3. Notification Config ────────────────────────────────────
            notification_config = self._load_notification_config(batch_id)

            # ── 4. Metadata Mappings (Source → Target → RuleSet) ──────────
            mappings = self._load_mappings(batch_id)

            manifest = PipelineManifest(
                batch_id=batch_id,
                batch_name=batch["batch_name"],
                pipeline_config=pipeline_config,
                notification_config=notification_config,
                mappings=mappings,
            )

            total_rules = sum(len(m.rule_set.rules) for m in mappings)
            logger.info(
                f"Manifest compiled: {len(mappings)} mapping(s), "
                f"{total_rules} rule(s), "
                f"notification={'ON' if notification_config else 'OFF'}"
            )
            return manifest

        except Exception as e:
            logger.exception(f"Failed to load manifest for batch_id '{batch_id}': {e}")
            return None

    # ─── Private Helpers ──────────────────────────────────────────────────

    def _load_pipeline_config(self, batch_id: int) -> PipelineConfig:
        row = self.db.execute_query(
            f"SELECT * FROM {SCHEMA}.pipeline_config_table WHERE batch_id = %s",
            (batch_id,), fetch_one=True
        )
        if not row:
            logger.warning(f"No pipeline_config for batch {batch_id}, using defaults.")
            return PipelineConfig()

        return PipelineConfig(
            max_workers=row["max_workers"],
            page_size=row["page_size"],
            batch_size=row["batch_size"],
            max_folder_depth=row["max_folder_depth"],
            connection_timeout_sec=row["connection_timeout_sec"],
            read_timeout_sec=row["read_timeout_sec"],
            max_retries=row["max_retries"],
            retry_delay_sec=float(row["retry_delay_sec"]),
            max_extracted_size_mb=row["max_extracted_size_mb"],
            max_zip_depth=row["max_zip_depth"],
            match_threshold=float(row["match_threshold"]),
            strict_mode=row["strict_mode"],
            deep_scan=row["deep_scan"],
            download_url_pattern=row["download_url_pattern"],
            duplicate_handle_policy=row["duplicate_handle_policy"],
            junk_extensions=row["junk_extensions"],
        )

    def _load_notification_config(self, batch_id: int) -> Optional[NotificationConfig]:
        row = self.db.execute_query(
            f"SELECT * FROM {SCHEMA}.notification_config_table "
            f"WHERE batch_id = %s AND is_active = TRUE",
            (batch_id,), fetch_one=True
        )
        if not row:
            return None

        return NotificationConfig(
            sender_email=row["sender_email"],
            success_recipient_email=row["success_recipient_email"],
            failure_recipient_email=row["failure_recipient_email"],
            schedule_times=row["schedule_times"],
            ses_access_key_id=self.secrets.decrypt(row.get("ses_access_key_id")),
            ses_secret_access_key=self.secrets.decrypt(row.get("ses_secret_access_key")),
            aws_region=row.get("aws_region"),
        )

    def _load_mappings(self, batch_id: int) -> List[MetadataMapping]:
        rows = self.db.execute_query(
            f"SELECT id, source_id, target_id, rule_set_id "
            f"FROM {SCHEMA}.metadata_config_table "
            f"WHERE batch_id = %s AND is_active = TRUE",
            (batch_id,)
        ) or []

        mappings = []
        for row in rows:
            source = self._load_source(row["source_id"])
            target = self._load_target(row["target_id"])
            rule_set = self._load_rule_set(row["rule_set_id"])

            if source and target and rule_set:
                mappings.append(MetadataMapping(
                    metadata_id=row["id"],
                    source=source,
                    target=target,
                    rule_set=rule_set,
                ))
            else:
                logger.warning(f"Skipping incomplete mapping ID={row['id']}")

        return mappings

    def _load_source(self, source_id: int) -> Optional[SourceConfig]:
        row = self.db.execute_query(
            f"SELECT * FROM {SCHEMA}.source_config_table WHERE id = %s AND is_active = TRUE",
            (source_id,), fetch_one=True
        )
        if not row:
            return None

        return SourceConfig(
            source_id=row["id"],
            source_type=row["source_type"],
            mailbox_address=row["mailbox_address"],           
            target_folder=row["target_folder"],               
            azure_tenant_id=self.secrets.decrypt(row.get("azure_tenant_id")),
            azure_client_id=self.secrets.decrypt(row.get("azure_client_id")),
            azure_client_secret=self.secrets.decrypt(row.get("azure_client_secret")),
        )

    def _load_target(self, target_id: int) -> Optional[TargetConfig]:
        row = self.db.execute_query(
            f"SELECT * FROM {SCHEMA}.target_config_table WHERE id = %s AND is_active = TRUE",
            (target_id,), fetch_one=True
        )
        if not row:
            return None

        return TargetConfig(
            target_id=row["id"],
            target_type=row["target_type"],
            bucket_name=row["bucket_name"],                   
            region=row["region"],                        
            base_path=row.get("base_path", ""),
            aws_access_key_id=self.secrets.decrypt(row.get("aws_access_key_id")),
            aws_secret_access_key=self.secrets.decrypt(row.get("aws_secret_access_key")),
        )

    def _load_rule_set(self, rule_set_id: int) -> Optional[RuleSet]:
        rs_row = self.db.execute_query(
            f"SELECT * FROM {SCHEMA}.rule_set_config_table "
            f"WHERE rule_set_id = %s AND is_active = TRUE",
            (rule_set_id,), fetch_one=True
        )
        if not rs_row:
            return None

        rule_rows = self.db.execute_query(
            f"SELECT * FROM {SCHEMA}.email_rule_config_table "
            f"WHERE rule_set_id = %s AND is_active = TRUE "
            f"ORDER BY priority ASC, rule_id ASC",
            (rule_set_id,)
        ) or []

        rules = [
            EmailRule(
                rule_id=r["rule_id"],
                rule_type=r["rule_type"],
                subject_keyword=r.get("subject_keyword"),
                filename_keyword=r.get("filename_keyword"),
                sender_keyword=r.get("sender_keyword"),
                regex_pattern=r.get("regex_pattern"),
                target_path_suffix=r["target_path_suffix"],
                priority=r["priority"],
            )
            for r in rule_rows
        ]

        return RuleSet(
            rule_set_id=rs_row["rule_set_id"],
            rule_set_name=rs_row["rule_set_name"],
            rules=rules,
        )
