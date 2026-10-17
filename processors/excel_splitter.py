import os
import pandas as pd
import logging
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# Max workers for parallel sheet processing
MAX_SHEET_WORKERS = 4


def _process_single_sheet(xls_path: str, sheet_name: str, base_name: str, output_dir: str) -> Dict[str, str]:
    """
    Process a single sheet from an Excel file and save as CSV.
    
    Args:
        xls_path: Path to the Excel file
        sheet_name: Name of the sheet to process
        base_name: Base filename without extension
        output_dir: Directory to save the CSV
        
    Returns:
        Dictionary with 'name', 'path', and 'row_count' for the generated CSV, or None on failure.
    """
    try:
        # Clean sheet name for filename usage
        safe_sheet_name = sheet_name.strip().replace(' ', '_')
        
        # Determine format based on sheet name
        is_pivot = "pivot" in sheet_name.lower()
        
        if is_pivot:
            new_filename = f"{base_name}_{safe_sheet_name}.xlsx"
            output_path = os.path.join(output_dir, new_filename)
            
            # Save as Excel to preserve formatting/types (though interactivity might be lost if just reading data)
            df = pd.read_excel(xls_path, sheet_name=sheet_name)
            row_count = len(df)
            df.to_excel(output_path, index=False)
        else:
            new_filename = f"{base_name}_{safe_sheet_name}.csv"
            output_path = os.path.join(output_dir, new_filename)
            
            # Read and save as CSV
            df = pd.read_excel(xls_path, sheet_name=sheet_name)
            row_count = len(df)
            df.to_csv(output_path, index=False)
        
        logger.info(f"Extracted sheet '{sheet_name}' to '{new_filename}' ({row_count} rows)")
        
        return {
            'name': new_filename, 
            'path': output_path,
            'row_count': row_count
        }
    except Exception as e:
        logger.error(f"Failed to process sheet '{sheet_name}': {e}")
        return None


def split_excel_into_sheets(file_path: str, output_dir: str) -> List[Dict[str, str]]:
    """
    Reads an Excel file and saves each sheet as a separate CSV file in the output directory.
    Uses parallel processing for faster extraction of multi-sheet files.
    
    Args:
        file_path: Path to the source .xlsx file.
        output_dir: Directory where extracted CSV files will be saved.
        
    Returns:
        List of dictionaries with 'name', 'path', and 'row_count' for each generated CSV file.
    """
    results = []
    original_filename = os.path.basename(file_path)
    base_name, _ = os.path.splitext(original_filename)
    
    try:
        logger.info(f"Splitting Excel file: {original_filename}")
        
        # Get sheet names first
        with pd.ExcelFile(file_path) as xls:
            sheet_names = xls.sheet_names
        
        # If only 1-2 sheets, process sequentially (overhead not worth it)
        if len(sheet_names) <= 2:
            for sheet_name in sheet_names:
                result = _process_single_sheet(file_path, sheet_name, base_name, output_dir)
                if result:
                    results.append(result)
        else:
            # Parallel processing for 3+ sheets
            logger.debug(f"Using parallel processing for {len(sheet_names)} sheets")
            
            with ThreadPoolExecutor(max_workers=MAX_SHEET_WORKERS) as executor:
                futures = {
                    executor.submit(_process_single_sheet, file_path, sheet_name, base_name, output_dir): sheet_name
                    for sheet_name in sheet_names
                }
                
                for future in as_completed(futures):
                    sheet_name = futures[future]
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except Exception as e:
                        logger.error(f"Failed to process sheet '{sheet_name}': {e}")
            
        return results
    except Exception as e:
        logger.error(f"Failed to split Excel file {file_path}: {e}")
        # Return empty list on failure so pipeline can decide fallback
        return []
