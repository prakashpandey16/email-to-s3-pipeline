
import unittest
import os
import shutil
import pandas as pd
import sys
from unittest.mock import MagicMock

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock config dependencies
sys.modules['pydantic_settings'] = MagicMock()
sys.modules['config.config'] = MagicMock()

# Import after mocking
from processors.excel_splitter import split_excel_into_sheets
from processors.path_resolver import PathResolver

class TestE2EIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = "test_e2e_output"
        os.makedirs(self.test_dir, exist_ok=True)
        self.resolver = PathResolver()
        
        # Manually inject the new rules (mimicking the actual JSON file content)
        # This ensures we aren't dependent on the file loading logic but testing the LOGIC flow
        # However, to be truly E2E, we should load from the actual file if possible, 
        # but the mock might block it. The PathResolver loads from file in __init__. 
        # Since we mocked config, we can't easily rely on defaults. 
        # But PathResolver reads the file directly using os modules, so as long as file exists it should work.
        # Wait, if config is mocked, how does PathResolver find the file?
        # PathResolver uses os.path logic relative to __file__. So it SHOULD find the real JSON file.
        # Let's verify if loading works.

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_excel_split_and_resolve(self):
        # Scenario: 
        # 1. Received "Daily_Report.xlsx" with sheets "CV_DATA_" and "BRSNR_".
        # 2. Split it.
        # 3. Resolve S3 paths for resulting CSVs using configured rules.
        
        filename = "Daily_Report.xlsx"
        file_path = os.path.join(self.test_dir, filename)
        
        # Create Excel with 2 sheets matching "multi_sheet_rules"
        with pd.ExcelWriter(file_path) as writer:
            pd.DataFrame({'A': [1]}).to_excel(writer, sheet_name='CV_DATA_', index=False)
            pd.DataFrame({'B': [2]}).to_excel(writer, sheet_name='BRSNR_', index=False)
            
        # Step 1: Split
        split_results = split_excel_into_sheets(file_path, self.test_dir)
        self.assertEqual(len(split_results), 2)
        
        # Step 2: Resolve logic simulation
        # Note: Resolver usually needs Subject + Filename.
        # Let's say Subject is "Ad-hoc Request" (irrelevant for multi-sheet rules usually)
        subject = "Ad-hoc Request"
        
        resolved_paths = []
        for file_item in split_results:
            path, rule, rtype = self.resolver.resolve_s3_key(subject, file_item['name'])
            resolved_paths.append((file_item['name'], path))
            
        # Step 3: Verify Routing
        # Expectation: 
        # Daily_Report_CV_DATA_.csv -> flipkart-toload/CV Adherence Data/
        # Daily_Report_BRSNR_.csv -> flipkart-toload/CV BRSNR Data/
        
        # Mapping checks
        for name, path in resolved_paths:
            if "CV_DATA_" in name:
                self.assertIn("flipkart-toload/CV Adherence Data", path)
            elif "BRSNR_" in name:
                self.assertIn("flipkart-toload/CV BRSNR Data", path)
            else:
                self.fail(f"Unexpected file: {name}")

    def test_subject_rule_fallback(self):
        # Scenario: Excel file doesn't match multi-sheet explicitly, but Subject matches "subject_rules"
        # Since splitter preserves original name + sheet name, we need to ensure resolver handles it.
        
        subject = "CV | BRSNR" # Matches a subject_rule
        filename = "Unknown_Sheet.xlsx"
        file_path = os.path.join(self.test_dir, filename)
        
        with pd.ExcelWriter(file_path) as writer:
            pd.DataFrame({'X': [1]}).to_excel(writer, sheet_name='RandomSheet', index=False)
            
        split_results = split_excel_into_sheets(file_path, self.test_dir)
        # Result: Unknown_Sheet_RandomSheet.csv
        
        path, rule, rtype = self.resolver.resolve_s3_key(subject, split_results[0]['name'])
        
        # Should hit "CV | BRSNR" subject rule -> flipkart-toload/CV Adherence Data/
        self.assertIn("flipkart-toload/CV Adherence Data", path)
        self.assertEqual(rtype, "SUBJECT_MATCH")

if __name__ == '__main__':
    unittest.main()
