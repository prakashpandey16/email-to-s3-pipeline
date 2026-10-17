
import sys
import os
import csv
from datetime import datetime

# Define absolute path to be sure
# Assuming standard layout from user info: c:\Users\praka\Desktop\Framework_code\email_to_s3_pipeline\logs\audit_log.csv
LOG_DIR = r"c:\Users\praka\Desktop\Framework_code\email_to_s3_pipeline\logs"
LOG_FILE = os.path.join(LOG_DIR, "audit_log.csv")

os.makedirs(LOG_DIR, exist_ok=True)

def write_sample_log():
    # Headers matching main.py
    headers = ['timestamp', 'email_subject', 'filename', 'matched_rule', 's3_path', 'status']
    
    data = [
        (datetime.now().isoformat(), "ADM shipments for Daily Reports", "Final_Result_for_CONNECTINDIA.xlsx", "rule_sf_01", "flipkart-toload/ADM Shipments/Final_Result_for_CONNECTINDIA.xlsx", "SUCCESS"),
        (datetime.now().isoformat(), "Random Spam Email", "offer.pdf", "N/A", "N/A", "NO_RULE"),
        (datetime.now().isoformat(), "Prexo Breach Report", "Prexo Breach Report.xlsx", "rule_sf_11", "flipkart-toload/prexo_breach/Prexo Breach Report.xlsx", "DUPLICATE"),
        (datetime.now().isoformat(), "ODH COD Pendency", "CONNECTINDIA.csv", "rule_sf_06", "flipkart-toload/odh_cod_pendency/CONNECTINDIA.csv", "UPLOAD_ERROR")
    ]
    
    print(f"Writing to {LOG_FILE}")
    try:
        with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(data)
        print("Success")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    write_sample_log()
