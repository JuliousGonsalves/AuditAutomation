import boto3
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

# ========== CONFIGURATION ==========
EXCLUDE_DOMAINS = ["epiuse.com", "afonza.com"]
REPORT_FILE = "SSO_User_List_Evosus.pdf"
# ==================================


class BorderPDF(SimpleDocTemplate):
    """Custom PDF class with consistent border and footer."""
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)

    def afterPage(self):
        c: canvas.Canvas = self.canv
        width, height = landscape(A4)

        # Outer border
        margin = 25
        c.setLineWidth(1)
        c.rect(margin, margin, width - 2 * margin, height - 2 * margin)

        # Footer (no CloudOps or user count)
        footer_text = f"Evosus | SSO User List | Page {self.page}"
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.grey)
        c.drawCentredString(width / 2, 18, footer_text)


def get_identity_store_id():
    """Fetch the AWS IAM Identity Store ID dynamically."""
    sso_admin = boto3.client("sso-admin")
    instances = sso_admin.list_instances()

    if not instances["Instances"]:
        raise Exception("❌ No Identity Center instance found in this account/region.")

    return instances["Instances"][0]["IdentityStoreId"]


def list_all_users(identity_store_id, exclude_domains=None):
    """List all users and exclude those with specific email domains."""
    client = boto3.client("identitystore")
    paginator = client.get_paginator("list_users")

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


def generate_pdf_report(users, file_name):
    """Generate a polished PDF report with border, footer, and styled table."""
    doc = BorderPDF(file_name, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = []

    # ===== Header =====
    title = Paragraph("<b>SSO User List - Evosus</b>", styles["Title"])
    elements += [title, Spacer(1, 20)]

    # ===== Table =====
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

    # ===== Build PDF =====
    doc.build(elements)
    print(f"📄 PDF generated: {file_name}")


def main():
    try:
        identity_store_id = get_identity_store_id()
        users = list_all_users(identity_store_id, exclude_domains=EXCLUDE_DOMAINS)

        if not users:
            print("⚠️ No users found after filtering excluded domains.")
            return

        generate_pdf_report(users, REPORT_FILE)

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
