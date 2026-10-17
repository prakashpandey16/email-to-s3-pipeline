import re
import html
import logging
import unicodedata
import os
from urllib.parse import urlparse
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def sanitize_filename(filename: str) -> str:
    r"""
    Sanitize filename for safe S3 upload:
    - Remove path traversal characters (../, ..\)
    - Replace special characters with underscore
    - Normalize unicode (NFKC)
    - Prevent null bytes
    - Strip leading/trailing whitespace
    
    Args:
        filename: Original filename from email attachment
        
    Returns:
        Sanitized filename safe for S3 storage
    """
    if not filename:
        return "unnamed_file"
    
    # Unicode normalization
    filename = unicodedata.normalize("NFKC", filename)
    
    # Remove null bytes (security)
    filename = filename.replace('\x00', '')
    
    # Get basename only (remove any path components)
    filename = os.path.basename(filename)
    
    # Remove path traversal patterns
    filename = filename.replace('..', '')
    filename = filename.replace('/', '_').replace('\\', '_')
    
    # Replace problematic S3 characters with underscore
    # Keep: alphanumeric, dash, underscore, dot, space
    filename = re.sub(r'[^\w\-. ]', '_', filename, flags=re.UNICODE)
    
    # Collapse multiple underscores/spaces
    filename = re.sub(r'[_\s]+', '_', filename)
    
    # Strip leading/trailing whitespace and underscores
    filename = filename.strip(' _')
    
    # Ensure we have a valid filename
    if not filename or filename == '.':
        return "unnamed_file"
    
    return filename


def normalize_for_matching(text: str) -> str:
    """
    Normalize text for rule matching:
    - Unicode normalization (NFKC)
    - Lowercase
    - Replace Unicode dashes with hyphen
    - Replace spaces, slashes, hyphens, plus, pipe with underscore
    - Remove non-alphanumeric/underscore characters
    - Remove numeric tokens (dates)
    - Collapse multiple underscores
    
    Args:
        text: Text to normalize (subject or filename)
        
    Returns:
        Normalized text for consistent matching
    """
    if not text:
        return ""
    
    # Unicode normalization first
    text = unicodedata.normalize("NFKC", text)
    
    # Lowercase
    text = text.lower()
    
    # Replace Unicode dashes with hyphen
    text = re.sub(r"[–—−]", "-", text)
    
    # Replace spaces, slashes, hyphens, plus, pipe with underscore
    text = re.sub(r"[\/\\\s\-\+\|]+", "_", text)
    
    # Remove non-alphanumeric/underscore characters
    text = re.sub(r"[^a-z0-9_]", "", text)
    
    # Remove numeric tokens (dates) - crude approximation
    text = "_".join([t for t in text.split("_") if not t.isdigit()])
    
    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text).strip("_")
    
    return text


def sanitize_url(raw_url: str) -> Optional[str]:
    """Sanitize and validate URL."""
    if not raw_url:
        return None
    url = html.unescape(raw_url).strip()
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return None
    return url

def extract_download_url(text: str, download_url_pattern: str) -> Optional[str]:
    """Extract signed download URL using configured pattern, ignoring image links."""
    if not download_url_pattern:
        return None
    match = re.search(download_url_pattern, text)
    if match:
        url = sanitize_url(match.group(0))
        if url:
            # Strip common trailing punctuation the regex might have picked up
            clean_url = url.rstrip(',.;:!)')
            parsed = urlparse(clean_url)
            # Use the parsed path's extension, ensuring no trailing chars
            path_ext = os.path.splitext(parsed.path)[1].lower()
            if path_ext in ('.jpeg', '.jpg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.svg'):
                logger.debug(f"Ignoring image link: {url}")
                return None
            
            # Note: Returning clean_url since the comma shouldn't be part of the URL anyway
            return clean_url
    return None

def clean_s3_key_name(text: str) -> str:
    """
    Sanitize S3 key ensuring no special characters like %, space, etc.
    Allowed: a-z, A-Z, 0-9, -, _, /, .
    """
    if not text:
        return ""
    # Replace known bad chars with underscore or empty
    # Allow spaces as requested by user configuration
    return re.sub(r'[^a-zA-Z0-9/_\-\. ]', '_', text)
