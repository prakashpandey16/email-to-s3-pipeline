"""
Database Client — Handles all operational DB interactions.
Schema: email_to_s3_config
"""

import psycopg2
import uuid
import logging
from datetime import datetime
import hashlib
from psycopg2.extras import Json

logger = logging.getLogger(__name__)

SCHEMA = "email_to_s3_config"


class DBClient:
    """
    Handles all interactions with the PostgreSQL control plane.
    Manages Pipeline Runs, Logs, and Processed Files state.
    """

    def __init__(self, db_config, run_id=None):
        self.db_config = db_config
        self.conn = None
        self.run_id = run_id

    def connect(self):
        """Establishes a connection to the database."""
        if self.conn and not self.conn.closed:
            return  # Already connected
        try:
            self.conn = psycopg2.connect(
                user=self.db_config.get("user"),
                password=self.db_config.get("password"),
                host=self.db_config.get("host"),
                port=self.db_config.get("port"),
                dbname=self.db_config.get("dbname"),
            )
            self.conn.autocommit = True
        except Exception as e:
            logger.error(f"DB Connection Failed: {e}")
            raise

    # ─── Pipeline Run Lifecycle ───────────────────────────────────────────

    def start_run(self, batch_id: int = None):
        """
        Creates a new entry in pipeline_run_table.
        Returns the generated UUID for this run.
        """
        if not self.conn:
            self.connect()

        self.run_id = str(uuid.uuid4())
        try:
            with self.conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {SCHEMA}.pipeline_run_table
                    (run_id, batch_id, start_time, status)
                    VALUES (%s, %s, %s, 'RUNNING')
                """, (self.run_id, batch_id, datetime.utcnow()))

            logger.info(f"DB: Started Pipeline Run [{self.run_id}]")
            return self.run_id
        except Exception as e:
            logger.critical(f"DB: Failed to initialize run: {e}")
            raise

    def update_run_status(self, status, metrics=None, error_msg=None):
        """
        Updates the final status of the pipeline run.
        Args:
            status: SUCCESS, FAILED, PARTIAL_SUCCESS
            metrics: Run statistics dict
            error_msg: Root cause if failed
        """
        if not self.run_id:
            return
        if not self.conn or self.conn.closed:
            logger.warning("DB: Attempted to update run status without active connection.")
            return

        try:
            with self.conn.cursor() as cur:
                cur.execute(f"""
                    UPDATE {SCHEMA}.pipeline_run_table
                    SET status = %s, end_time = %s, metrics = %s, error_message = %s
                    WHERE run_id = %s
                """, (status, datetime.utcnow(), Json(metrics) if metrics else None, error_msg, self.run_id))
            logger.info(f"DB: Run [{self.run_id}] finished with status: {status}")
        except Exception as e:
            logger.exception(f"DB: Failed to update run status: {e}")

    # ─── Idempotency (Processed Files) ────────────────────────────────────

    def is_file_processed(self, file_hash: str, job_name: str) -> bool:
        """
        Idempotency Check using SHA256 hash.
        Returns True if already processed, False otherwise.
        """
        try:
            if not self.conn or self.conn.closed:
                logger.warning("DB: Check processed file skipped - No Connection")
                return False

            with self.conn.cursor() as cur:
                cur.execute(f"""
                    SELECT 1 FROM {SCHEMA}.processed_files_log_table
                    WHERE file_hash_sha256 = %s AND job_name = %s
                """, (file_hash, job_name))
                exists = cur.fetchone() is not None

                if exists:
                    logger.info(f"DB: Duplicate content detected (Hash: {file_hash[:8]}...) for Job '{job_name}'")

                return exists
        except Exception as e:
            logger.exception(f"DB: Idempotency check failed: {e}")
            return False

    def mark_file_processed(self, file_name, file_hash: str, target_key, job_name):
        """Records a successfully processed file."""
        if not self.run_id:
            return
        if not self.conn or self.conn.closed:
            logger.warning("DB: Mark file processed skipped - No Connection")
            return

        # Truncate to match DB column limits
        file_name = (file_name or "")[:255]
        target_key = (target_key or "")[:500]
        job_name = (job_name or "")[:100]

        try:
            with self.conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {SCHEMA}.processed_files_log_table
                    (file_hash_sha256, file_name, target_key, job_name, run_id)
                    VALUES (%s, %s, %s, %s, %s)
                """, (file_hash, file_name, target_key, job_name, self.run_id))
        except Exception as e:
            logger.exception(f"DB: Failed to mark file processed: {e}")
            raise

    # ─── Error Logging ────────────────────────────────────────────────────

    def log_step(self, log_level: str, message: str):
        """
        Logs a pipeline step or error to the error_log_table.
        Only logs WARNING, ERROR, CRITICAL levels to reduce noise.
        """
        if log_level not in ["ERROR", "CRITICAL", "WARNING"]:
            return
        if not self.conn or self.conn.closed:
            return

        try:
            with self.conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO {SCHEMA}.error_log_table
                    (run_id, log_level, message)
                    VALUES (%s, %s, %s)
                """, (self.run_id, log_level, message))
        except Exception as e:
            logger.error(f"DB: Failed to write log step: {e}")

    # ─── Generic Query ────────────────────────────────────────────────────

    def execute_query(self, query, params=None, fetch_one=False):
        """
        Executes a query and returns results as dictionaries.
        Args:
            query: SQL query to execute.
            params: Parameters for the query.
            fetch_one: Return only the first row as a dict.
        Returns:
            list[dict] or dict or None.
        """
        if not self.conn:
            self.connect()

        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params)
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    if fetch_one:
                        row = cur.fetchone()
                        return dict(zip(columns, row)) if row else None
                    else:
                        return [dict(zip(columns, row)) for row in cur.fetchall()]
                return None
        except Exception as e:
            logger.error(f"DB: Query execution failed: {e}")
            raise

    # ─── Connection Management ────────────────────────────────────────────

    def close(self):
        """Closes the database connection safely."""
        if self.conn:
            try:
                if not self.conn.closed:
                    self.conn.close()
                logger.debug("DB: Connection closed")
            except Exception as e:
                logger.error(f"DB: Error closing connection: {e}")
            finally:
                self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
