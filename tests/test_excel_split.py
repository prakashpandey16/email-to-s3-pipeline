
import unittest
import os
import shutil
import pandas as pd
import logging
import sys

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from processors.excel_splitter import split_excel_into_sheets

class TestExcelSplitter(unittest.TestCase):
    def setUp(self):
        self.test_dir = "test_output"
        os.makedirs(self.test_dir, exist_ok=True)
        
        # Create a dummy Excel file
        self.excel_path = os.path.join(self.test_dir, "test_file.xlsx")
        with pd.ExcelWriter(self.excel_path) as writer:
            pd.DataFrame({'A': [1, 2], 'B': [3, 4]}).to_excel(writer, sheet_name='Sheet1', index=False)
            pd.DataFrame({'C': [5, 6], 'D': [7, 8]}).to_excel(writer, sheet_name='Sheet2', index=False)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_split_excel(self):
        results = split_excel_into_sheets(self.excel_path, self.test_dir)
        
        self.assertEqual(len(results), 2)
        
        # Verify filenames
        filenames = [r['name'] for r in results]
        self.assertIn("test_file_Sheet1.csv", filenames)
        self.assertIn("test_file_Sheet2.csv", filenames)
        
        # Verify content
        for res in results:
            self.assertTrue(os.path.exists(res['path']))
            df = pd.read_csv(res['path'])
            self.assertFalse(df.empty)

if __name__ == '__main__':
    unittest.main()
