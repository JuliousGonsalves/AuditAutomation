import boto3
from datetime import date
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Frame
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

REPORT_FILE = "Security_Hub_Report.pdf"

# ==========================================
# AWS CONFIG
# ==========================================
AWS_REGION = "us-east-1"
ROLE_ARN = "arn:aws:iam::337909763810:role/audit-automation-role"
SESSION_NAME = "securityhub-audit-session"
EXTERNAL_ID = None

CLIENT_NAME = "Evosus"

# Previous month (same logic as other reports)
_today = date.today()
_prev_month = _today.month - 1 or 12
_prev_year = _today.year if _today.month != 1 else _today.year - 1
REPORT_MONTH_STR = date(_prev_year, _prev_month, 1).strftime("%B %Y")


# ==========================================
# STS Assume Role
# ==========================================
def assume_role(role_arn, session_name, external_id=None):
    sts_client = boto3.client("sts", region_name=AWS_REGION)
    args = {"RoleArn": role_arn, "RoleSessionName": session_name}
    if external_id:
        args["ExternalId"] = external_id
    response = sts_client.assume_role(**args)
    creds = response["Credentials"]
    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
    }


def aws_client(service, session, region=AWS_REGION):
    return boto3.client(
        service,
        region_name=region,
        aws_access_key_id=session["aws_access_key_id"],
        aws_secret_access_key=session["aws_secret_access_key"],
        aws_session_token=session["aws_session_token"],
    )


# ==========================================
# Custom PDF with border + header bar + footer inside border
# ==========================================
class BorderPDF(SimpleDocTemplate):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

    def afterPage(self):
        c: canvas.Canvas = self.canv
        width, height = landscape(A4)

        margin = 25  # same spacing as CC layout

        # Outer border
        c.setLineWidth(1)
        c.rect(margin, margin, width - 2 * margin, height - 2 * margin)

        # Dark blue header bar
        c.setFillColor("#003366")
        c.rect(margin, height - margin - 35, width - 2 * margin, 35, fill=1)

        # Header Title (white, centered)
        title_text = (
            f"Security Hub Compliance Report - {CLIENT_NAME} - {REPORT_MONTH_STR}"
        )
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.white)
        c.drawCentredString(width / 2, height - margin - 15, title_text)

        # Footer inside border
        c.setFont("Helvetica-Oblique", 9)
        c.setFillColor(colors.grey)
        c.drawCentredString(
            width / 2,
            margin + 8,  # inside border (similar to CC placement)
            f"Monthly Audit Report - {REPORT_MONTH_STR}",
        )


# ==========================================
# Generate PDF Content
# ==========================================
def generate_pdf(per_standard_stats, overall_score, total_passed, total_controls, file_name):

    doc = BorderPDF(file_name, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = []

    # Add top spacing (because header bar takes space)
    elements.append(Spacer(1, 50))

    desc_text = (
        "Track your cloud security posture with a summary security score and "
        "per-standard compliance scores. This report shows complete, unfiltered Security Hub data."
    )
    desc_style = ParagraphStyle(
        "Description",
        fontName="Helvetica",
        fontSize=12,
        textColor=colors.black,
        spaceAfter=20,
    )
    elements.append(Paragraph(desc_text, desc_style))

    # Score color logic
    if overall_score >= 80:
        color = "#34a853"
    elif overall_score >= 70:
        color = "#f9ab00"
    else:
        color = "#ea4335"

    score_style = ParagraphStyle(
        "ScoreStyle",
        fontName="Helvetica-Bold",
        fontSize=36,
        textColor=color,
        spaceAfter=12,
    )
    score_para = Paragraph(f"{overall_score}%", score_style)
    elements.append(score_para)

    elements.append(Spacer(1, 20))

    controls_style = ParagraphStyle(
        "ControlsStyle",
        fontName="Helvetica",
        fontSize=14,
        textColor=colors.black,
        spaceAfter=20,
    )
    controls_para = Paragraph(f"{total_passed} of {total_controls} controls passed", controls_style)
    elements.append(controls_para)

    # Table content
    table_data = [["Standard", "Passed", "Failed", "Score (%)"]]

    standard_display_names = {
        "cis-aws-foundations-benchmark": "CIS AWS Foundations Benchmark v3.0.0",
        "pci-dss": "PCI DSS v4.0.1",
        "aws-foundational-security-best-practices": "AWS Foundational Security Best Practices v1.0.0",
    }

    for standard, s in per_standard_stats.items():
        display_name = standard_display_names.get(standard, standard)
        table_data.append([display_name, s["passed"], s["failed"], s["score"]])

    table_data[1:] = sorted(table_data[1:], key=lambda x: x[0])

    table = Table(
        table_data,
        repeatRows=1,
        colWidths=[4 * inch, 1 * inch, 1 * inch, 1 * inch],
    )

    table_style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 11),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
        ]
    )

    table.setStyle(table_style)
    elements.append(table)

    doc.build(elements)
    print(f"📄 Security Hub Report generated: {file_name}")


# ==========================================
# Fetch Security Hub Data
# ==========================================
def fetch_security_scores(session, region_name=None, accountId=None):
    region = region_name or AWS_REGION
    client = aws_client("securityhub", session, region)

    if not accountId:
        accountId = aws_client("sts", session).get_caller_identity()["Account"]

    paginator = client.get_paginator("get_findings").paginate(
        Filters={
            "AwsAccountId": [{"Value": accountId, "Comparison": "EQUALS"}],
            "ProductName": [{"Value": "Security Hub", "Comparison": "EQUALS"}],
            "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
        },
        MaxResults=100,
    )

    standardsDict = {}

    for page in paginator:
        for finding in page["Findings"]:
            if (
                "Compliance" in finding
                and "ProductFields" in finding
                and finding["RecordState"] == "ACTIVE"
                and finding.get("Workflow", {}).get("Status") != "SUPPRESSED"
            ):
                status = finding["Compliance"].get("Status", "UNKNOWN")
                prodFields = finding["ProductFields"]
                control = prodFields.get("StandardsArn", prodFields.get("StandardsGuideArn", "Unknown"))
                rule = prodFields.get("ControlId", prodFields.get("RuleId", "UNKNOWN"))

                parts = control.split("/")
                controlName = parts[1] if len(parts) > 1 else control

                standardsDict.setdefault(controlName, {})[rule] = status

    per_standard_stats = {}
    total_controls = 0
    total_passed = 0

    for standard, controls in standardsDict.items():
        passed = sum(1 for k in controls if controls[k] == "PASSED")
        failed = len(controls) - passed
        score = round(passed / len(controls) * 100) if controls else 0

        per_standard_stats[standard] = {
            "passed": passed,
            "failed": failed,
            "score": score,
        }

        total_controls += len(controls)
        total_passed += passed

    overall_score = round((total_passed / total_controls) * 100) if total_controls else 0

    return per_standard_stats, overall_score, total_passed, total_controls


# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    print("🔄 Assuming IAM Role for Security Hub Report...")
    session = assume_role(ROLE_ARN, SESSION_NAME, EXTERNAL_ID)

    per_standard_stats, overall_score, total_passed, total_controls = fetch_security_scores(
        session, region_name=AWS_REGION
    )

    if per_standard_stats:
        generate_pdf(per_standard_stats, overall_score, total_passed, total_controls, REPORT_FILE)
    else:
        print("No Security Hub data found.")