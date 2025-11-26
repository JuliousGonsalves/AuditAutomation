import boto3
from datetime import date
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

# ==========================================
# CONFIGURATION
# ==========================================
AWS_REGION = "us-east-1"

ROLE_ARN = "arn:aws:iam::711387139366:role/audit-automation-role"   # <<< UPDATE THIS
SESSION_NAME = "sso-user-list-session"
EXTERNAL_ID = None   # Only if required

EXCLUDE_DOMAINS = ["epiuse.com", "afonza.com"]
REPORT_FILE = "SSO_User_List_Evosus.pdf"
CLIENT_NAME = "Evosus"

# Previous month (same logic as other reports)
_today = date.today()
_prev_month = _today.month - 1 or 12
_prev_year = _today.year if _today.month != 1 else _today.year - 1
REPORT_MONTH_STR = date(_prev_year, _prev_month, 1).strftime("%B %Y")
# ==========================================


# ==========================================
# STS Assume Role
# ==========================================
def assume_role(role_arn, session_name, external_id=None):
    sts_client = boto3.client("sts", region_name=AWS_REGION)

    if external_id:
        resp = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            ExternalId=external_id
        )
    else:
        resp = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name
        )

    creds = resp["Credentials"]
    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"]
    }


# ==========================================
# Client helper using assumed role
# ==========================================
def aws_client(service, session):
    return boto3.client(
        service,
        region_name=AWS_REGION,
        aws_access_key_id=session["aws_access_key_id"],
        aws_secret_access_key=session["aws_secret_access_key"],
        aws_session_token=session["aws_session_token"],
    )


# ==========================================
# PDF Class (border + blue header + footer inside border)
# ==========================================
class BorderPDF(SimpleDocTemplate):
    """Custom PDF class with border, header bar, and footer."""
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

    def afterPage(self):
        c: canvas.Canvas = self.canv
        width, height = landscape(A4)

        margin = 25
        c.setLineWidth(1)

        # Outer border
        c.rect(margin, margin, width - 2 * margin, height - 2 * margin)

        # Dark blue header bar
        c.setFillColor("#003366")
        c.rect(margin, height - margin - 35, width - 2 * margin, 35, fill=1)

        # Header text (white, centered)
        header_text = f"SSO User List - {CLIENT_NAME} - {REPORT_MONTH_STR}"
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.white)
        c.drawCentredString(width / 2, height - margin - 15, header_text)

        # Footer inside border
        c.setFont("Helvetica-Oblique", 9)
        c.setFillColor(colors.grey)
        c.drawCentredString(
            width / 2,
            margin + 8,  # inside border, similar to other reports
            f"Monthly Audit Report - {REPORT_MONTH_STR}",
        )


# ==========================================
# AWS Logic (now using STS session)
# ==========================================
def get_identity_store_id(session):
    """Fetch the AWS IAM Identity Store ID dynamically."""
    sso_admin = aws_client("sso-admin", session)
    instances = sso_admin.list_instances()

    if not instances["Instances"]:
        raise Exception("❌ No Identity Center instance found in this account/region.")

    return instances["Instances"][0]["IdentityStoreId"]


def list_all_users(session, identity_store_id, exclude_domains=None):
    """List all users and exclude emails from unwanted domains."""
    identity = aws_client("identitystore", session)

    paginator = identity.get_paginator("list_users")
    page_iterator = paginator.paginate(
        IdentityStoreId=identity_store_id,
        PaginationConfig={"PageSize": 50}
    )

    exclude_domains = [d.lower() for d in (exclude_domains or [])]
    users = []

    for page in page_iterator:
        for user in page.get("Users", []):
            username = user.get("UserName", "-")
            displayname = user.get("DisplayName", "-")
            email = next((e["Value"] for e in user.get("Emails", []) if e.get("Primary")), "-")

            if email and any(email.lower().endswith(f"@{d}") for d in exclude_domains):
                continue

            users.append([username, displayname, email])

    return users


# ==========================================
# PDF Generation
# ==========================================
def generate_pdf_report(users, file_name):
    """Generate PDF report."""
    doc = BorderPDF(file_name, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = []

    # Spacer to account for header bar area
    elements.append(Spacer(1, 50))

    # Table
    data = [["Username", "Display Name", "Email"]] + users
    table = Table(data, repeatRows=1, colWidths=[2.5 * inch, 3 * inch, 3 * inch])

    table_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 11),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
    ])
    table.setStyle(table_style)

    elements.append(table)

    doc.build(elements)
    print(f"📄 PDF generated: {file_name}")


# ==========================================
# Main Entry
# ==========================================
def main():
    try:
        print("🔄 Assuming IAM Role for Identity Center User List...")
        session = assume_role(ROLE_ARN, SESSION_NAME, EXTERNAL_ID)

        identity_store_id = get_identity_store_id(session)
        users = list_all_users(session, identity_store_id, exclude_domains=EXCLUDE_DOMAINS)

        if not users:
            print("⚠️ No users found after filtering excluded domains.")
            return

        generate_pdf_report(users, REPORT_FILE)

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()