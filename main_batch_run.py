"""
Safe Launcher — Runs main.py as a subprocess.
Catches catastrophic failures (SyntaxError, Import Error, Segfault)
and logs them to DB even if main.py itself could not start.
"""

import subprocess
import sys
import logging
from datetime import datetime

logging.basicConfig(level=logging.ERROR, format="%(asctime)s - [LAUNCHER] - %(message)s")
logger = logging.getLogger("Launcher")

SCHEMA = "email_to_s3_config"


def run_safe(batch_id: int):
    """
    1. Runs main.py as a subprocess.
    2. Catches CRITICAL errors (SyntaxError, ImportError, Segfault).
    3. Logs failures to the Database.
    """
    try:
        result = subprocess.run(
            [sys.executable, "main.py", "--batch_id", str(batch_id)],
            stdout=sys.stdout,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown Error (No stderr output)"
            print(f"\n[LAUNCHER] Pipeline Failed! Return Code: {result.returncode}")
            print(f"[LAUNCHER] Error Output:\n{error_msg}\n")

            log_failure_to_db(error_msg, result.returncode)
            sys.exit(result.returncode)

    except Exception as e:
        logger.critical(f"Launcher Failure: {e}")
        log_failure_to_db(f"Launcher Crash: {e}", 1)
        raise


def log_failure_to_db(message: str, code: int):
    """Attempts to log catastrophic failure to DB."""
    try:
        from config.config import db_config
        from config.database import DBClient

        db = DBClient(db_config)
        db.connect()

        with db.conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {SCHEMA}.error_log_table
                (run_id, log_level, message, created_at)
                VALUES (NULL, 'CRITICAL', %s, %s)
            """, (f"EXIT_CODE_{code}: {message[:500]}", datetime.utcnow()))

        db.close()
        print("[LAUNCHER] Error successfully logged to Database.")

    except Exception as db_e:
        print(f"[LAUNCHER] CRITICAL: Could not log to DB! ({db_e})")


if __name__ == "__main__":
    batch_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_safe(batch_idx)
