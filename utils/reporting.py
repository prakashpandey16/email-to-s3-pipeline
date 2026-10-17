"""
Pipeline Reporter — Generates activity reports and sends alerts via SES.
Configured via NotificationConfig (injected from manifest).
Queries new email_to_s3_config schema for report data.
"""

from datetime import datetime, timedelta, time, timezone
import logging
from typing import List, Dict, Optional
import pytz

from config.database import DBClient
from connectors.notifications import EmailNotificationClient

logger = logging.getLogger(__name__)

# Timezone constants
IST = pytz.timezone("Asia/Kolkata")
UTC = pytz.UTC

SCHEMA = "email_to_s3_config"


class PipelineReporter:
    def __init__(self, db_config: dict, notification_config=None, target_config=None):
        """
        Args:
            db_config: Database connection dict
            notification_config: NotificationConfig dataclass
            target_config: TargetConfig (fallback credentials for SES if needed)
        """
        self.db_config = db_config
        self.notification_config = notification_config

        if notification_config:
            self.notification = EmailNotificationClient(notification_config, fallback_target_config=target_config)
            self.schedule = notification_config.schedule_times or ["20:00"]
            self.failure_recipient = notification_config.failure_recipient_email
        else:
            self.notification = None
            self.schedule = []
            self.failure_recipient = None

    def _parse_schedule_time(self, time_str: str) -> tuple:
        """Parse 'HH:MM' string to (hour, minute) tuple."""
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

    def _get_report_window(self, current_time_str: str) -> tuple:
        """
        Determine start/end time for the report window.
        Returns: (start_time_ist, end_time_ist, start_time_utc, end_time_utc)
        """
        schedule = sorted(self.schedule)
        if not schedule:
            return None, None, None, None

        try:
            idx = schedule.index(current_time_str)
        except ValueError:
            return None, None, None, None

        prev_time_str = schedule[idx - 1]

        current_hour, current_min = self._parse_schedule_time(current_time_str)
        prev_hour, prev_min = self._parse_schedule_time(prev_time_str)

        now_ist = datetime.now(IST)
        today = now_ist.date()

        end_time_ist = IST.localize(datetime.combine(today, time(current_hour, current_min, 0)))

        prev_total = prev_hour * 60 + prev_min
        curr_total = current_hour * 60 + current_min

        if prev_total >= curr_total:
            start_date = today - timedelta(days=1)
        else:
            start_date = today

        start_time_ist = IST.localize(datetime.combine(start_date, time(prev_hour, prev_min, 0)))

        start_time_utc = start_time_ist.astimezone(UTC).replace(tzinfo=None)
        end_time_utc = end_time_ist.astimezone(UTC).replace(tzinfo=None)

        return start_time_ist, end_time_ist, start_time_utc, end_time_utc

    def _generate_html(self, start: datetime, end: datetime, files: List[Dict], failures: List[Dict]) -> str:
        """Generate Professional HTML Report."""

        total_files = len(files)
        total_errors = len(failures)
        success_count = total_files

        status_color = "#28a745" if total_errors == 0 else "#dc3545"

        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #444; background-color: #f4f6f9; margin: 0; padding: 20px; }}
                .container {{ max-width: 900px; margin: 0 auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }}
                .header {{ border-bottom: 2px solid {status_color}; padding-bottom: 15px; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; }}
                h2 {{ margin: 0; color: #2c3e50; font-size: 24px; }}
                .date-badge {{ background: #eee; padding: 5px 12px; border-radius: 15px; font-size: 12px; font-weight: 600; color: #666; }}
                .metrics-grid {{ display: flex; gap: 15px; margin-bottom: 30px; }}
                .card {{ flex: 1; padding: 15px; background: #f8f9fa; border-radius: 6px; border-left: 4px solid #ddd; }}
                .card.success {{ border-left-color: #28a745; }}
                .card.error {{ border-left-color: #dc3545; }}
                .card-title {{ font-size: 12px; text-transform: uppercase; color: #888; letter-spacing: 0.5px; display: block; margin-bottom: 5px; }}
                .card-value {{ font-size: 24px; font-weight: bold; color: #333; }}
                h3 {{ color: #34495e; font-size: 18px; margin-top: 30px; margin-bottom: 10px; border-left: 4px solid #0056b3; padding-left: 10px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; background: #fff; }}
                th {{ background-color: #f1f3f5; color: #495057; font-weight: 600; text-align: left; padding: 10px; border-bottom: 2px solid #dee2e6; }}
                td {{ padding: 10px; border-bottom: 1px solid #eee; color: #555; }}
                tr:last-child td {{ border-bottom: none; }}
                tr:hover {{ background-color: #f8f9fa; }}
                .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
                .badge-success {{ background: #d4edda; color: #155724; }}
                .badge-fail {{ background: #f8d7da; color: #721c24; }}
                .footer {{ margin-top: 40px; padding: 15px; background-color: #2c3e50; color: #fff; border-radius: 6px; text-align: center; font-size: 11px; }}
                .empty-row {{ color: #999; font-style: italic; text-align: center; padding: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Pipeline Activity Report</h2>
                    <span class="date-badge">{start.strftime('%b %d, %H:%M')} - {end.strftime('%H:%M')}</span>
                </div>
                <div class="metrics-grid">
                    <div class="card success">
                        <span class="card-title">Files Ingested</span>
                        <span class="card-value">{success_count}</span>
                    </div>
                    <div class="card error">
                        <span class="card-title">Processing Accuracy</span>
                        <span class="card-value">{100 if total_files == 0 and total_errors == 0 else (round((success_count / (success_count + total_errors)) * 100) if (success_count + total_errors) > 0 else 0)}%</span>
                    </div>
                    <div class="card error">
                        <span class="card-title">Errors</span>
                        <span class="card-value" style="color: {'#dc3545' if total_errors > 0 else '#333'}">{total_errors}</span>
                    </div>
                </div>

                <h3>Files Loaded to S3</h3>
                <table>
                    <thead><tr>
                        <th width="30%">File Name</th>
                        <th width="40%">S3 Path</th>
                        <th width="10%">Status</th>
                        <th width="20%">Ingestion Time</th>
                    </tr></thead>
                    <tbody>
        """

        if not files:
            html += "<tr><td colspan='4' class='empty-row'>No files ingested in this window.</td></tr>"
        else:
            for f in files:
                html += f"""
                <tr>
                    <td><strong>{f['file_name']}</strong></td>
                    <td style="color:#777;">{f['target_key']}</td>
                    <td><span class="badge badge-success">SUCCESS</span></td>
                    <td>{f['processed_at']}</td>
                </tr>
                """

        html += """
                    </tbody>
                </table>
                <h3>Processing Failures / Logs</h3>
                <table>
                    <thead><tr>
                        <th width="15%">Run ID</th>
                        <th width="50%">Message</th>
                        <th width="10%">Level</th>
                        <th width="25%">Time</th>
                    </tr></thead>
                    <tbody>
        """

        if not failures:
            html += "<tr><td colspan='4' class='empty-row'>No errors or warnings recorded.</td></tr>"
        else:
            for err in failures:
                level_class = "badge-fail" if err["log_level"] == "ERROR" else "badge-warning"
                html += f"""
                <tr>
                    <td style="font-family:monospace; font-size:11px;">{str(err['run_id'])[:8]}...</td>
                    <td style="color: #dc3545;">{err['message']}</td>
                    <td><span class="badge {level_class}">{err['log_level']}</span></td>
                    <td>{err['created_at']}</td>
                </tr>
                """

        html += f"""
                    </tbody>
                </table>
                <div class="footer">
                    Generated by EmailToS3Pipeline &bull; {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                </div>
            </div>
        </body>
        </html>
        """
        return html

    def generate_daily_report(self, force_time: str = None):
        """Main reporting logic. Called by orchestration."""
        if not self.notification:
            logger.info("Notifications not configured. Skipping report.")
            return

        now_ist = datetime.now(IST)
        current_time_str = force_time if force_time else f"{now_ist.hour:02d}:{now_ist.minute:02d}"

        if current_time_str not in self.schedule:
            logger.info(f"Time {current_time_str} not in schedule {self.schedule}. Skipping report.")
            return

        start_ist, end_ist, start_utc, end_utc = self._get_report_window(current_time_str)
        if not start_ist:
            return

        logger.info(f"Generating Report for Window (IST): {start_ist} -> {end_ist}")

        db = DBClient(self.db_config)
        try:
            db.connect()

            # Fetch Successful Files from new schema
            files_query = f"""
                SELECT file_name, target_key, processed_at 
                FROM {SCHEMA}.processed_files_log_table 
                WHERE processed_at >= %s AND processed_at < %s
                ORDER BY processed_at DESC
            """
            success_files = db.execute_query(files_query, (start_utc, end_utc)) or []

            for f in success_files:
                if f.get("processed_at"):
                    utc_time = f["processed_at"]
                    if utc_time.tzinfo is None:
                        utc_time = UTC.localize(utc_time)
                    f["processed_at"] = utc_time.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")

            # Fetch Failures from new schema
            error_query = f"""
                SELECT run_id, message, log_level, created_at 
                FROM {SCHEMA}.error_log_table 
                WHERE created_at >= %s AND created_at < %s
                ORDER BY created_at DESC
            """
            failures = db.execute_query(error_query, (start_utc, end_utc)) or []

            for err in failures:
                if err.get("created_at"):
                    utc_time = err["created_at"]
                    if utc_time.tzinfo is None:
                        utc_time = UTC.localize(utc_time)
                    err["created_at"] = utc_time.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")

            html_body = self._generate_html(start_ist, end_ist, success_files, failures)
            subject_line = f"Pipeline Report ({start_ist.strftime('%H:%M')} - {end_ist.strftime('%H:%M')} IST)"
            self.notification.send_email(subject_line, html_body)

        except Exception as e:
            logger.error(f"Failed to generate daily report: {e}")
        finally:
            db.close()

    def send_failure_alert(self, context: str, error_message: str):
        """Sends a high-priority alert email on pipeline failure."""
        if not self.notification:
            logger.warning("Cannot send failure alert: notifications not configured.")
            return

        subject = f"CRITICAL ALERT: Pipeline Failed ({context})"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f8d7da; color: #721c24;">
            <div style="background: #fff; padding: 30px; border-radius: 8px; border-left: 6px solid #dc3545; box-shadow: 0 4px 15px rgba(0,0,0,0.1);">
                <h2 style="color: #dc3545; margin-top:0;">Pipeline Critical Failure</h2>
                <p style="font-size: 14px; color: #555;"><strong>Time:</strong> {timestamp}</p>
                <p style="font-size: 14px; color: #555;"><strong>Context:</strong> {context}</p>
                <div style="background-color: #f8f9fa; padding: 15px; border-radius: 4px; border: 1px solid #ddd; margin-top: 20px;">
                    <h3 style="margin-top: 0; font-size: 14px; color: #333;">Error Trace:</h3>
                    <pre style="white-space: pre-wrap; word-wrap: break-word; font-family: 'Consolas', monospace; color: #d63384; font-size: 12px;">{error_message}</pre>
                </div>
                <p style="margin-top: 30px; font-size: 12px; color: #666;">
                    Check <code>{SCHEMA}.error_log_table</code> for details.
                </p>
            </div>
        </body>
        </html>
        """
        try:
            self.notification.send_email(subject, html_body, override_recipient=self.failure_recipient)
            logger.info("Critical Failure Alert sent via Email.")
        except Exception as e:
            logger.critical(f"Failed to send failure alert: {e}")

    def send_email(self, subject: str, body: str):
        """Pass-through for simple email sends (e.g. health warnings)."""
        if self.notification:
            self.notification.send_email(subject, body)
