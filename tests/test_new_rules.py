
import sys
import os
import unittest
from unittest.mock import MagicMock

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock config.config if it cannot be imported (or just always mock it for unit test isolation)
sys.modules['config.config'] = MagicMock()
sys.modules['pydantic_settings'] = MagicMock()

from processors.path_resolver import PathResolver

class TestNewRules(unittest.TestCase):
    def setUp(self):
        self.resolver = PathResolver()

    def test_subject_filename_rule(self):
        # Rule: "ODH/MDH || RVP Data" + "RVP_Data" -> "flipkart-toload/Rvp Data/"
        subject = "Fwd: ODH/MDH || RVP Data Report"
        filename = "Start_RVP_Data_End.xlsx"
        
        path, rule, rtype = self.resolver.resolve_s3_key(subject, filename)
        self.assertEqual(path, "flipkart-toload/Rvp Data/Start_RVP_Data_End.xlsx")
        self.assertEqual(rule, "subject_filename_rule_1") 
        self.assertEqual(rtype, "SUBJECT_FILENAME_MATCH")

    def test_multi_sheet_rule(self):
        # Rule: "CV_DATA_" -> "flipkart-toload/CV Adherence Data/"
        subject = "Random Subject"
        filename = "CV_DATA_2023.xlsx"
        
        path, rule, rtype = self.resolver.resolve_s3_key(subject, filename)
        self.assertEqual(path, "flipkart-toload/CV Adherence Data/CV_DATA_2023.xlsx")
        self.assertIn("multisheet_rule", rule)

    def test_subject_rule(self):
        # Rule: "CV | BRSNR" -> "flipkart-toload/CV Adherence Data/"
        subject = "CV | BRSNR Report"
        filename = "RandomFile.xlsx"
        
        path, rule, rtype = self.resolver.resolve_s3_key(subject, filename)
        self.assertEqual(path, "flipkart-toload/CV Adherence Data/RandomFile.xlsx")
        self.assertIn("subject_rule", rule)
        self.assertEqual(rtype, "SUBJECT_MATCH")

    def test_precedence_multi_sheet_over_subject(self):
        # Subject Rule: "CV | BRSNR" -> "CV Adherence Data"
        # Multi Sheet Rule: "BRSNR_" -> "CV BRSNR Data"
        
        subject = "CV | BRSNR Report"
        filename = "BRSNR_File.xlsx"
        
        # Should match multi_sheet_rule (filename specific) because it's higher precedence in my code
        path, rule, rtype = self.resolver.resolve_s3_key(subject, filename)
        self.assertEqual(path, "flipkart-toload/CV BRSNR Data/BRSNR_File.xlsx")
        self.assertIn("multisheet_rule", rule)
        self.assertEqual(rtype, "FILENAME_MATCH")

if __name__ == '__main__':
    unittest.main()
