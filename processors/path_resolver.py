"""
Path Resolver — Determines S3 target path for each email/file based on DB rules.
Rules are injected from the PipelineManifest (no JSON file dependency).
"""

import os
import re
import logging
from typing import List, Optional
from utils.content_processing import normalize_for_matching as normalize_text, clean_s3_key_name as clean_s3_key

logger = logging.getLogger(__name__)


class PathResolver:
    def __init__(self, rules: List, match_threshold: float = 0.60):
        """
        Args:
            rules: List of EmailRule dataclasses from the loaded RuleSet
            match_threshold: Minimum token overlap score for fuzzy matching
        """
        self.rules = rules
        self.threshold = match_threshold

        # Pre-categorize rules by type for faster lookup
        self.subject_filename_rules = [r for r in rules if r.rule_type == "subject_filename"]
        self.multi_sheet_rules = [r for r in rules if r.rule_type == "multi_sheet"]
        self.regex_rules = [r for r in rules if r.rule_type == "regex"]
        self.subject_only_rules = [r for r in rules if r.rule_type == "subject_only"]
        self.filename_keyword_rules = [r for r in rules if r.rule_type == "filename_keyword"]
        self.sender_rules = [r for r in rules if r.rule_type == "sender"]

        logger.info(
            f"PathResolver initialized: "
            f"{len(self.subject_filename_rules)} subject_filename, "
            f"{len(self.multi_sheet_rules)} multi_sheet, "
            f"{len(self.regex_rules)} regex, "
            f"{len(self.subject_only_rules)} subject_only, "
            f"{len(self.sender_rules)} sender, "
            f"{len(self.filename_keyword_rules)} filename_keyword rules."
        )

    def _token_overlap_score(self, key: str, text: str) -> float:
        """Return ratio of words in key that appear in text."""
        if not key or not text:
            return 0.0
        key_norm = normalize_text(key)
        key_tokens = set(key_norm.split("_"))
        text_tokens = set(text.split("_"))
        if not key_tokens:
            return 0.0
        return len(key_tokens & text_tokens) / len(key_tokens)

    def resolve_s3_key(self, subject: str, file_path: str, is_split: bool = False, sender: str = None) -> tuple:
        """
        Determine S3 key based on subject and filename.
        Returns (s3_key, rule_name, match_type) or (None, None, None).
        """
        file_name = os.path.basename(file_path)
        name_part, ext_part = os.path.splitext(file_name)

        subj_key = normalize_text(subject)
        file_key = normalize_text(name_part)

        destination_path = None
        matched_rule_name = None
        match_type = None

        def check_subject_filename():
            nonlocal destination_path, matched_rule_name, match_type
            for rule in self.subject_filename_rules:
                subj_score = self._token_overlap_score(rule.subject_keyword, subj_key)
                file_score = self._token_overlap_score(rule.filename_keyword, file_key)
                if subj_score >= self.threshold and file_score >= self.threshold:
                    destination_path = rule.target_path_suffix
                    matched_rule_name = f"sf_rule_{rule.rule_id}"
                    match_type = "SUBJECT_FILENAME_MATCH"
                    return True
            return False

        def check_multi_sheet():
            nonlocal destination_path, matched_rule_name, match_type
            for rule in self.multi_sheet_rules:
                keyword = normalize_text(rule.filename_keyword)
                if keyword and keyword in file_key:
                    destination_path = rule.target_path_suffix
                    matched_rule_name = f"ms_rule_{rule.rule_id}"
                    match_type = "FILENAME_MATCH"
                    return True
            return False

        def check_regex():
            nonlocal destination_path, matched_rule_name, match_type
            for rule in self.regex_rules:
                if rule.regex_pattern and re.search(rule.regex_pattern, file_name):
                    destination_path = rule.target_path_suffix
                    matched_rule_name = f"regex_rule_{rule.rule_id}"
                    match_type = "REGEX_MATCH"
                    return True
            return False

        def check_sender():
            nonlocal destination_path, matched_rule_name, match_type
            if not sender:
                return False
            sender_norm = sender.lower()
            for rule in self.sender_rules:
                if rule.sender_keyword and rule.sender_keyword.lower() in sender_norm:
                    destination_path = rule.target_path_suffix
                    matched_rule_name = f"sender_rule_{rule.rule_id}"
                    match_type = "SENDER_MATCH"
                    return True
            return False

        # RULE EVALUATION ORDER
        if is_split:
            if check_multi_sheet(): pass
            elif check_subject_filename(): pass
            elif check_regex(): pass
        else:
            if check_subject_filename(): pass
            elif check_sender(): pass
            elif check_regex(): pass
            elif check_multi_sheet(): pass

        # Fallback: Subject Only Rules
        if not destination_path:
            for rule in self.subject_only_rules:
                if self._token_overlap_score(rule.subject_keyword, subj_key) >= self.threshold:
                    destination_path = rule.target_path_suffix
                    matched_rule_name = f"subj_rule_{rule.rule_id}"
                    match_type = "SUBJECT_MATCH"
                    break

        # Fallback: Filename Keyword Rules
        if not destination_path:
            for rule in self.filename_keyword_rules:
                keyword = normalize_text(rule.filename_keyword)
                if keyword and keyword in file_key:
                    destination_path = rule.target_path_suffix
                    matched_rule_name = f"fk_rule_{rule.rule_id}"
                    match_type = "FILENAME_MATCH"
                    break

        if destination_path:
            safe_name = name_part + ext_part
            if not destination_path.endswith("/"):
                destination_path += "/"
            full_path = f"{destination_path}{safe_name}"
            return clean_s3_key(full_path), matched_rule_name, match_type
        else:
            return None, None, None

    def is_subject_relevant(self, subject: str) -> bool:
        """
        Fail-Fast Check: Returns True if subject matches any known pattern.
        Used for pre-filtering before downloading attachments.
        """
        subj_key = normalize_text(subject)

        for rule in self.subject_filename_rules:
            if self._token_overlap_score(rule.subject_keyword, subj_key) >= self.threshold:
                return True

        for rule in self.subject_only_rules:
            if self._token_overlap_score(rule.subject_keyword, subj_key) >= self.threshold:
                return True

        return False

    def is_sender_relevant(self, sender: str) -> bool:
        """Checks if the sender matches any configured sender rules."""
        if not sender:
            return False
        sender_norm = sender.lower()
        for rule in self.sender_rules:
            if rule.sender_keyword and rule.sender_keyword.lower() in sender_norm:
                return True
        return False
