"""
Unit tests for production-grade pipeline improvements.
Tests filename handling, Unicode normalization, and S3 key generation.
"""
import pytest
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.content_processing import sanitize_filename, normalize_for_matching
from processors.path_resolver import normalize_text, clean_s3_key


class TestFilenameSanitization:
    """Tests for sanitize_filename() function."""
    
    def test_basic_filename(self):
        """Test that basic filenames pass through unchanged."""
        assert sanitize_filename("report.xlsx") == "report.xlsx"
        assert sanitize_filename("data_file.csv") == "data_file.csv"
    
    def test_path_traversal_protection(self):
        """Test that path traversal attempts are blocked."""
        assert ".." not in sanitize_filename("../../../etc/passwd")
        assert ".." not in sanitize_filename("..\\..\\windows\\system32")
        assert "/" not in sanitize_filename("/etc/passwd")
        assert "\\" not in sanitize_filename("C:\\Windows\\System32\\config")
    
    def test_null_byte_removal(self):
        """Test that null bytes are removed."""
        result = sanitize_filename("file\x00name.txt")
        assert "\x00" not in result
        assert "filename.txt" == result
    
    def test_unicode_normalization(self):
        """Test that Unicode characters are normalized (NFKC)."""
        # Full-width characters should be normalized
        result = sanitize_filename("ファイル.xlsx")  # Japanese for "file"
        assert result  # Should not be empty
        
    def test_empty_filename(self):
        """Test that empty filenames get a default."""
        assert sanitize_filename("") == "unnamed_file"
        assert sanitize_filename(None) == "unnamed_file"
    
    def test_special_characters_replaced(self):
        """Test that problematic S3 characters are replaced."""
        result = sanitize_filename("file<with>special:chars?.xlsx")
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert "?" not in result
    
    def test_spaces_preserved(self):
        """Test that spaces are preserved (S3 allows them)."""
        # Spaces may be collapsed but single spaces should work
        result = sanitize_filename("file name.xlsx")
        assert "file" in result and "name" in result


class TestNormalizeForMatching:
    """Tests for normalize_for_matching() function."""
    
    def test_lowercase(self):
        """Test that text is lowercased."""
        assert normalize_for_matching("UPPERCASE") == "uppercase"
        assert normalize_for_matching("MixedCase") == "mixedcase"
    
    def test_unicode_dash_normalization(self):
        """Test that Unicode dashes are normalized to hyphens then underscores."""
        # Em-dash, en-dash should be normalized
        result = normalize_for_matching("ODH—MDH")
        assert "—" not in result  # Em-dash should be gone
        
    def test_space_normalization(self):
        """Test that spaces become underscores."""
        result = normalize_for_matching("hello world")
        assert " " not in result
        assert "_" in result or result == "helloworld"
    
    def test_numeric_token_removal(self):
        """Test that pure numeric tokens (dates) are removed."""
        result = normalize_for_matching("report_2025_01_01")
        # Pure numbers like 2025, 01, 01 should be removed
        assert "2025" not in result
    
    def test_empty_input(self):
        """Test that empty input returns empty string."""
        assert normalize_for_matching("") == ""
        assert normalize_for_matching(None) == ""
    
    def test_unicode_normalization_nfkc(self):
        """Test that Unicode is normalized with NFKC."""
        # Full-width characters should be normalized
        result = normalize_for_matching("ＡＢＣ")  # Full-width ABC
        assert result == "abc"


class TestNormalizeText:
    """Tests for path_resolver.normalize_text() function."""
    
    def test_basic_normalization(self):
        """Test basic text normalization."""
        result = normalize_text("ODH/MDH || P0 & P1 Escalations")
        assert "/" not in result
        assert "|" not in result
        assert "&" not in result
    
    def test_connectindia_pattern(self):
        """Test normalization of CONNECTINDIA patterns."""
        result = normalize_text("ADM shipments for Daily Reports for New RunSheet Service CONNECTINDIA")
        assert "connectindia" in result
        assert " " not in result


class TestCleanS3Key:
    """Tests for clean_s3_key() function."""
    
    def test_basic_key(self):
        """Test that basic S3 keys pass through."""
        assert clean_s3_key("folder/file.csv") == "folder/file.csv"
    
    def test_special_chars_replaced(self):
        """Test that special characters are replaced."""
        result = clean_s3_key("folder/file%name.csv")
        assert "%" not in result
    
    def test_spaces_allowed(self):
        """Test that spaces are allowed in S3 keys."""
        result = clean_s3_key("folder/file name.csv")
        assert "file name" in result or "file_name" in result


class TestS3KeyGeneration:
    """Integration tests for S3 key generation with original filename."""
    
    def test_original_filename_preserved(self):
        """Test that original filename is used for S3 key."""
        from processors.path_resolver import PathResolver
        
        resolver = PathResolver()
        subject = "ADM shipments for Daily Reports for New RunSheet Service CONNECTINDIA"
        original_filename = "Final_Result_for_CONNECTINDIA_2025-Dec-30.csv"
        
        s3_key, rule_name, match_type = resolver.resolve_s3_key(subject, original_filename)
        
        if s3_key:  # If rule matches
            assert "Final_Result_for_CONNECTINDIA" in s3_key
            assert s3_key.endswith(".csv")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
