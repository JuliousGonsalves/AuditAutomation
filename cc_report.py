#!/usr/bin/env python3
import urllib3
import urllib.parse
import json
import datetime
from datetime import date
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
import boto3
import textwrap
import os

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# AWS / CLIENTCENTRAL CONFIG
# ==========================================
AWS_REGION = "us-east-1"

ROLE_ARN = "arn:aws:iam::445567102114:role/automation-from-prod"
SESSION_NAME = "clientcentral-ticket-session"
EXTERNAL_ID = None

SECRET_NAME = "ClientCentral/cc-account-token"
CC_ACCOUNT_ID = 6166
CLIENT_NAME = "Evosus"

# Layout tuning
HEADER_HEIGHT = 0.07
LEFT_MARGIN = 0.03
RIGHT_MARGIN = 0.03
TOP_MARGIN = 0.03
BOTTOM_MARGIN = 0.03


# ==========================================
# STS Assume Role + AWS helper
# ==========================================
def assume_role(role_arn, session_name, external_id=None):
    sts = boto3.client("sts", region_name=AWS_REGION)
    args = {"RoleArn": role_arn, "RoleSessionName": session_name}
    if external_id:
        args["ExternalId"] = external_id
    resp = sts.assume_role(**args)
    creds = resp["Credentials"]
    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"]
    }


def aws_client(service, session):
    return boto3.client(
        service,
        region_name=AWS_REGION,
        aws_access_key_id=session["aws_access_key_id"],
        aws_secret_access_key=session["aws_secret_access_key"],
        aws_session_token=session["aws_session_token"]
    )


def fetch_cc_token(session):
    sm = aws_client("secretsmanager", session)
    response = sm.get_secret_value(SecretId=SECRET_NAME)
    secret_string = response.get("SecretString", "{}")
    try:
        secret = json.loads(secret_string)
    except Exception:
        secret = {"cc-api-token": secret_string}

    token = secret.get("cc-api-token")
    if not token:
        raise Exception("Missing 'cc-api-token' key in Secrets Manager secret")

    return token


# ==========================================
# STATUS MAPPING
# ==========================================
STATUS_MAPPING = {
    468: "On hold",
    469: "Awaiting info",
    470: "Answered",
    471: "Closed",
    479: "Cancelled",
    512: "Completed",
    565: "Change Failed",
    848: "Notification Sent",
    849: "Acknowledged",
}


# ==========================================
# Fetch previous month tickets
# ==========================================
def fetch_previous_month_tickets(cc_token, cc_account_id):
    tickets_by_status = {name: [] for name in STATUS_MAPPING.values()}
    page = 1
    http = urllib3.PoolManager()

    today = date.today()
    previous_month = today.month - 1 or 12
    year = today.year if today.month != 1 else today.year - 1

    while True:
        query_params = {
            "token": cc_token,
            "filter": f"account={cc_account_id}",
            "select": "id,subject,created_at,status.*",
            "page": page
        }

        url = f"https://clientcentral.io/api/v1/tickets.json?{urllib.parse.urlencode(query_params)}"

        try:
            resp = http.request("GET", url, headers={"Accept": "application/json"})
            data = json.loads(resp.data.decode() if isinstance(resp.data, (bytes, bytearray)) else resp.data)
        except Exception as e:
            print(f"Error fetching data: {e}")
            break

        if not data.get("data"):
            break

        for ticket in data["data"]:
            created_str = ticket.get("created_at")
            status = ticket.get("status")

            if not created_str or not status:
                continue

            try:
                created_date = datetime.datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                continue

            if created_date.month == previous_month and created_date.year == year:
                status_name = STATUS_MAPPING.get(status.get("id"), f"Unknown Status ID: {status.get('id')}")
                subject = (ticket.get("subject") or "").replace("\n", " ").strip()

                if len(subject) > 200:
                    subject = subject[:197] + "..."

                tickets_by_status.setdefault(status_name, []).append({
                    "id": ticket.get("id"),
                    "subject": subject,
                    "created_at": created_date.strftime("%Y-%m-%d"),
                    "status": status_name
                })

        page += 1

    return tickets_by_status


# ==========================================
# Render helpers
# ==========================================
def wrap_text(text, width=70):
    return "\n".join(textwrap.fill(line, width=width) for line in text.splitlines())


# ==========================================
# PDF Generation
# ==========================================
def generate_pdf_with_border_footer(tickets_by_status, output_file="Ticket_Report_Final.pdf"):

    fig = plt.figure(figsize=(8.27, 11.69))
    fig.subplots_adjust(left=LEFT_MARGIN, right=1 - RIGHT_MARGIN, top=1 - TOP_MARGIN, bottom=BOTTOM_MARGIN)

    # Draw page border
    ax_border = fig.add_axes([0, 0, 1, 1])
    ax_border.axis('off')
    outer = Rectangle(
        (LEFT_MARGIN, BOTTOM_MARGIN),
        1 - LEFT_MARGIN - RIGHT_MARGIN,
        1 - TOP_MARGIN - BOTTOM_MARGIN,
        transform=fig.transFigure,
        fill=False,
        linewidth=1,
        edgecolor='black'
    )
    ax_border.add_patch(outer)

    # Header bar
    header_ax = fig.add_axes([LEFT_MARGIN, 1 - TOP_MARGIN - HEADER_HEIGHT,
                              1 - LEFT_MARGIN - RIGHT_MARGIN, HEADER_HEIGHT])
    header_ax.axis('off')
    header_rect = Rectangle((0, 0), 1, 1, transform=header_ax.transAxes, color="#003366")
    header_ax.add_patch(header_rect)

    today = date.today()
    previous_month = today.month - 1 or 12
    year = today.year if today.month != 1 else today.year - 1
    previous_month_str = datetime.date(year, previous_month, 1).strftime("%B %Y")

    title_text = f"Client Central Ticket Status Report — {CLIENT_NAME} — {previous_month_str}"
    header_ax.text(0.5, 0.52, title_text,
                   ha='center', va='center', fontsize=14, color='white', weight='bold')

    # Calculate pending tickets
    pending_statuses = ["On hold", "Awaiting info"]
    pending_tickets = []

    for status in pending_statuses:
        pending_tickets.extend(tickets_by_status.get(status, []))

    pending_count = len(pending_tickets)

    # ==========================================
    # CASE 1 — Pending tickets exist
    # ==========================================
    if pending_count > 0:
        pie_ax = fig.add_axes([0.12, 0.55, 0.76, 0.36])
        labels = []
        sizes = []
        colors_map = plt.cm.tab20.colors

        for status, t_list in tickets_by_status.items():
            count = len(t_list)
            if count > 0:
                labels.append(f"{status} ({count})")
                sizes.append(count)

        pie_ax.pie(
            sizes,
            labels=labels,
            startangle=140,
            colors=colors_map,
            wedgeprops={'edgecolor': 'white'},
            textprops={'fontsize': 9}
        )

        table_ax = fig.add_axes([0.05, 0.22, 0.90, 0.30])
        table_ax.axis('off')

        table_data = [["Ticket ID", "Subject", "Created Date", "Status"]] + [
            [t["id"], t["subject"], t["created_at"], t["status"]] for t in pending_tickets
        ]

        tbl = table_ax.table(cellText=table_data, loc='upper center',
                             cellLoc='left',
                             colWidths=[0.12, 0.48, 0.2, 0.2])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)

        table_ax.set_title("Pending Tickets (On hold & Awaiting info)", fontsize=12, pad=5)

    # ==========================================
    # CASE 2 — NO Pending Tickets (NEW PIE CHART)
    # ==========================================
    else:
        pie_ax = fig.add_axes([0.18, 0.52, 0.65, 0.35])
        labels = []
        sizes = []
        cmap = plt.cm.tab20.colors

        for status, t_list in tickets_by_status.items():
            count = len(t_list)
            if count > 0:
                labels.append(f"{status} ({count})")
                sizes.append(count)

        if sizes:
            pie_ax.pie(
                sizes,
                labels=labels,
                startangle=140,
                colors=cmap,
                wedgeprops={'edgecolor': 'white'},
                textprops={'fontsize': 9}
            )
            pie_ax.set_title(
                f"Ticket Distribution — {previous_month_str}",
                fontsize=12, pad=10, weight="bold"
            )
        else:
            pie_ax.text(
                0.5, 0.5,
                "No tickets found for selected month.",
                ha='center', va='center',
                fontsize=12, color="gray"
            )
            pie_ax.axis('off')

        text_ax = fig.add_axes([0.18, 0.27, 0.65, 0.15])
        text_ax.axis('off')

        text_ax.text(
            0.5, 0.62,
            f"No pending tickets were found for {previous_month_str}.",
            ha='center', va='center',
            fontsize=13, weight='bold', color="#0b2545"
        )

        text_ax.text(
            0.5, 0.33,
            "All tickets are currently in answered/closed/completed states.",
            ha='center', va='center',
            fontsize=11, color="#555555"
        )

    # ------------------------------------------
    # FOOTER
    # ------------------------------------------
    footer_ax = fig.add_axes([
        LEFT_MARGIN,
        BOTTOM_MARGIN + 0.005,
        1 - LEFT_MARGIN - RIGHT_MARGIN,
        0.03
    ])
    footer_ax.axis('off')
    footer_ax.text(
        0.5, 0.5,
        f"Monthly Audit Report — {previous_month_str}",
        ha="center", va="center",
        fontsize=9, color="gray"
    )

    pdf = PdfPages(output_file)
    pdf.savefig(fig, bbox_inches='tight')
    pdf.close()
    plt.close(fig)

    print(f"✅ Final PDF generated: {output_file}")


# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    print("🔄 Assuming IAM Role & loading ClientCentral API token…")

    session = assume_role(ROLE_ARN, SESSION_NAME, EXTERNAL_ID)
    cc_token = fetch_cc_token(session)

    tickets = fetch_previous_month_tickets(cc_token, CC_ACCOUNT_ID)

    generate_pdf_with_border_footer(tickets)