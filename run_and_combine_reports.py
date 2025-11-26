#!/usr/bin/env python3
import os
import subprocess
import time
from PyPDF2 import PdfMerger
import boto3

# ================================================================
# CONFIGURATION
# ================================================================
S3_BUCKET = "evosus-audit-report"

# Script file names
SCRIPTS = {
    "cover_page": "cover_page.py",
    "sso": "user_report.py",
    "securityhub": "securityhub_report.py",
    "clientcentral": "cc_report.py",
    "backup": "backup_report.py",
    "monitoring": "grafana_report.py"
}

# Expected PDF outputs per script
PDF_OUTPUTS = {
    "cover_page": "Monthly_Audit_Intro.pdf",
    "sso": "SSO_User_List_Evosus.pdf",
    "securityhub": "Security_Hub_Report.pdf",
    "clientcentral": "Ticket_Report_Final.pdf",
    "backup": "Backup_Audit_Report.pdf",
    "monitoring": "screenshots/Monitoring_Report.pdf"
}

# Final merged file
FINAL_OUTPUT = "Evosus_Monthly_Audit_Report_Combined.pdf"

# Merge order (IMPORTANT)
MERGE_ORDER = [
    "cover_page",
    "sso",
    "securityhub",
    "clientcentral",
    "backup",
    "monitoring"
]

# ================================================================
# FUNCTION — Run a script & wait for PDF
# ================================================================
def run_script_and_wait(script_name, expected_pdf):
    print(f"\n▶ Running: {script_name}")

    try:
        subprocess.run(["python3", script_name], check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Script failed: {script_name} — {e}")
        return False

    # Wait for PDF to appear
    timeout = 180
    waited = 0

    while waited < timeout:
        if os.path.exists(expected_pdf):
            print(f"✅ PDF generated: {expected_pdf}")
            return True

        time.sleep(1)
        waited += 1

    print(f"❌ TIMEOUT: PDF not generated: {expected_pdf}")
    return False


# ================================================================
# FUNCTION — Merge PDFs
# ================================================================
def merge_pdfs(pdf_files, output_file):
    print("\n🔄 Merging PDFs…")

    merger = PdfMerger()

    for pdf in pdf_files:
        if os.path.exists(pdf):
            print(f"➕ Adding: {pdf}")
            merger.append(pdf)
        else:
            print(f"⚠️ Missing PDF, skipping: {pdf}")

    merger.write(output_file)
    merger.close()

    print(f"\n📄 Combined Audit Report Created: {output_file}")


# ================================================================
# FUNCTION — Upload PDFs to S3 using jumpbox IAM role
# ================================================================
def upload_reports_to_s3(pdf_files):
    print("\n📤 Uploading reports to S3 (using EC2 IAM role)…")

    s3 = boto3.client("s3")  # ← Uses instance profile credentials automatically

    for pdf in pdf_files:
        if os.path.exists(pdf):
            print(f"⬆️ Uploading: {pdf}")
            s3.upload_file(
                Filename=pdf,
                Bucket=S3_BUCKET,
                Key=os.path.basename(pdf)
            )
        else:
            print(f"⚠️ Cannot upload (file missing): {pdf}")

    print(f"\n🎉 All available reports uploaded to S3 bucket: {S3_BUCKET}")


# ================================================================
# MAIN
# ================================================================
if __name__ == "__main__":

    generated_pdfs = []

    print("\n==============================")
    print("🚀 MONTHLY AUDIT REPORT GENERATOR STARTED")
    print("==============================\n")

    # Step 1 — Run all scripts in order and collect PDFs
    for key in MERGE_ORDER:
        script_name = SCRIPTS[key]
        pdf_name = PDF_OUTPUTS[key]

        success = run_script_and_wait(script_name, pdf_name)
        if success:
            generated_pdfs.append(pdf_name)

    # Step 2 — Merge all available PDFs
    print("\n==============================")
    print("📚 MERGING ALL PDF REPORTS")
    print("==============================\n")

    merge_pdfs(generated_pdfs, FINAL_OUTPUT)

    # Step 3 — Upload all PDFs + merged PDF to S3
    print("\n==============================")
    print("📤 UPLOADING TO S3")
    print("==============================\n")

    upload_reports_to_s3(generated_pdfs + [FINAL_OUTPUT])

    print("\n==============================")
    print("🎉 ALL DONE!")
    print("==============================\n")
