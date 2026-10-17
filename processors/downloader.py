"""
File Downloader — Handles HTTP downloads, ZIP/GZIP extraction.
No global config imports. Defaults are sensible; overridable via constructor.
"""

import requests
import os
import shutil
import gzip
import logging
import zipfile
from typing import List, Dict
from urllib.parse import urlparse, unquote
from utils.decorators import monitor_and_log, retry_with_backoff

logger = logging.getLogger(__name__)

# Office document extensions — technically ZIP files, should NOT be extracted
OFFICE_EXTENSIONS = {".xlsx", ".docx", ".pptx"}


class FileDownloader:
    def __init__(self, pipeline_config=None):
        """
        Args:
            pipeline_config: PipelineConfig dataclass (optional, provides timeouts etc.)
        """
        if pipeline_config:
            self.http_timeout = (pipeline_config.connection_timeout_sec, pipeline_config.read_timeout_sec)
            self.max_extracted_size_bytes = pipeline_config.max_extracted_size_mb * 1024 * 1024
            self.max_zip_depth = pipeline_config.max_zip_depth
        else:
            self.http_timeout = (10, 60)
            self.max_extracted_size_bytes = 500 * 1024 * 1024
            self.max_zip_depth = 2

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    @monitor_and_log(log_message="Stream init failed", reraise=True)
    def download_stream(self, url: str) -> tuple:
        """
        Initiates a stream download from URL.
        Returns: (filename, stream)
        """
        parsed_url = urlparse(url)
        file_name = unquote(os.path.basename(parsed_url.path))

        logger.info(f"Initiating stream download: {url} (filename: {file_name})")

        response = requests.get(url, stream=True, timeout=self.http_timeout)
        response.raise_for_status()

        stream = response.raw
        response.raw.decode_content = True
        return file_name, stream


def decompress_gzip_file(file_path: str, extract_to: str) -> Dict[str, str]:
    """Decompresses a .gz file and returns the decompressed file info."""
    original_name = os.path.basename(file_path)
    if original_name.lower().endswith(".gz"):
        decompressed_name = original_name[:-3]
    else:
        decompressed_name = original_name + ".decompressed"

    decompressed_path = os.path.join(extract_to, decompressed_name)
    logger.info(f"Decompressing gzip: {file_path} -> {decompressed_name}")

    try:
        with gzip.open(file_path, "rb") as f_in:
            with open(decompressed_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        return {"name": decompressed_name, "path": decompressed_path}
    except Exception as e:
        logger.error(f"Failed to decompress gzip {file_path}: {e}")
        return {"name": original_name, "path": file_path}


def extract_and_collect_files(
    file_path: str, extract_to: str, max_depth: int = 2,
    max_extracted_size_bytes: int = 500 * 1024 * 1024,
) -> List[Dict[str, str]]:
    """
    Checks if a file is a zip or gzip, extracts it, and returns all files.
    Includes zip bomb protection.
    """
    results = []
    file_name = os.path.basename(file_path)

    # Skip extraction for Office documents
    if os.path.splitext(file_name.lower())[1] in OFFICE_EXTENSIONS:
        logger.debug(f"Skipping ZIP extraction for Office document: {file_name}")
        return [{"name": file_name, "path": file_path}]

    # Handle GZIP files
    if file_name.lower().endswith(".gz") and not zipfile.is_zipfile(file_path):
        return [decompress_gzip_file(file_path, extract_to)]

    # Handle ZIP files
    if zipfile.is_zipfile(file_path):
        try:
            logger.info(f"Extracting zip: {file_path}")
            with zipfile.ZipFile(file_path, "r") as zip_ref:
                total_uncompressed = sum(info.file_size for info in zip_ref.infolist())
                if total_uncompressed > max_extracted_size_bytes:
                    logger.error(f"ZIP BOMB DETECTED: {total_uncompressed} bytes. File: {file_path}")
                    return []

                compressed_size = os.path.getsize(file_path)
                if compressed_size > 0:
                    ratio = total_uncompressed / compressed_size
                    if ratio > 100:
                        logger.warning(f"Suspicious compression ratio ({ratio:.0f}x) in {file_path}.")

                zip_ref.extractall(extract_to)

            for root, _, files in os.walk(extract_to):
                for file in files:
                    full_path = os.path.join(root, file)
                    if full_path == file_path:
                        continue
                    if max_depth > 0 and zipfile.is_zipfile(full_path):
                        nested_dir = os.path.join(extract_to, f"nested_{os.path.basename(file)}")
                        os.makedirs(nested_dir, exist_ok=True)
                        results.extend(extract_and_collect_files(
                            full_path, nested_dir, max_depth - 1, max_extracted_size_bytes
                        ))
                    else:
                        results.append({"name": os.path.basename(full_path), "path": full_path})
            return results

        except (zipfile.BadZipFile, Exception) as e:
            logger.error(f"Failed to extract zip {file_path}: {e}")
            return [{"name": file_name, "path": file_path}]

    # Fallback: Return original file
    return [{"name": file_name, "path": file_path}]
