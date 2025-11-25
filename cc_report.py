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

ROLE_ARN = "arn:aws:iam::445567102114:role/automation-from-prod"   # <<< UPDATE THIS
SESSION_NAME = "clientcentral-ticket-session"
EXTERNAL_ID = None   # If your role requires it

SECRET_NAME = "ClientCentral/cc-account-token"  # secret JSON contains {"cc-api-token": "..."}
CC_ACCOUNT_ID = 1015
CLIENT_NAME = "Evosus"  # used in header per your choice 4

# Layout tuning
HEADER_HEIGHT = 0.07  # professional header height
LEFT_MARGIN = 0.03
RIGHT_MARGIN = 0.03
TOP_MARGIN = 0.03
BOTTOM_MARGIN = 0.03
# ==========================================


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
        # If not JSON, attempt to treat as raw token
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
# Fetch previous month tickets from ClientCentral
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
                # skip malformed dates
                continue

            if created_date.month == previous_month and created_date.year == year:
                status_name = STATUS_MAPPING.get(status.get("id"), f"Unknown Status ID: {status.get('id')}")
                subject = ticket.get("subject") or ""
                # limit subject length to avoid layout issues
                subject = subject.replace("\n", " ").strip()
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
# PDF Generation with professional empty-state (Option C, white box with border)
# ==========================================
def generate_pdf_with_border_footer(tickets_by_status, output_file="Ticket_Report_Final.pdf"):
    # A4 (portrait) in inches for matplotlib: 8.27 x 11.69
    fig = plt.figure(figsize=(8.27, 11.69))
    # overall margins
    fig.subplots_adjust(left=LEFT_MARGIN, right=1 - RIGHT_MARGIN, top=1 - TOP_MARGIN, bottom=BOTTOM_MARGIN)

    # Draw outer border rectangle (full page)
    ax_border = fig.add_axes([0, 0, 1, 1])
    ax_border.axis('off')
    outer = Rectangle((LEFT_MARGIN, BOTTOM_MARGIN), 1 - LEFT_MARGIN - RIGHT_MARGIN, 1 - TOP_MARGIN - BOTTOM_MARGIN,
                      transform=fig.transFigure, fill=False, linewidth=1, edgecolor='black')
    ax_border.add_patch(outer)

    # Header bar (dark blue) - slim
    header_ax = fig.add_axes([LEFT_MARGIN, 1 - TOP_MARGIN - HEADER_HEIGHT, 1 - LEFT_MARGIN - RIGHT_MARGIN, HEADER_HEIGHT])
    header_ax.axis('off')
    header_rect = Rectangle((0, 0), 1, 1, transform=header_ax.transAxes, color="#003366")
    header_ax.add_patch(header_rect)

    # Title text (clean text only)
    today = date.today()
    previous_month = today.month - 1 or 12
    year = today.year if today.month != 1 else today.year - 1
    previous_month_str = datetime.date(year, previous_month, 1).strftime("%B %Y")
    title_text = f"Client Central Ticket Status Report — {CLIENT_NAME} — {previous_month_str}"
    header_ax.text(0.5, 0.52, title_text, ha='center', va='center', fontsize=14, color='white', weight='bold')

    # Calculate totals
    total_tickets = sum(len(v) for v in tickets_by_status.values())
    pending_statuses = ["On hold", "Awaiting info"]
    pending_tickets = []
    for status in pending_statuses:
        pending_tickets.extend(tickets_by_status.get(status, []))
    pending_count = len(pending_tickets)

    # Content placement:
    # If there are pending tickets -> show pie chart + pending table + footer note
    # If there are tickets but no pending tickets -> show SINGLE white box w/ border and corporate message
    # If NO tickets at all -> similar message but first line "No tickets were found..."
    content_top = 1 - TOP_MARGIN - HEADER_HEIGHT - 0.03  # small gap below header

    if pending_count > 0:
        # PIE CHART area
        pie_ax = fig.add_axes([0.12, 0.55, 0.76, 0.36])  # left, bottom, width, height
        labels = []
        sizes = []
        colors_map = plt.cm.tab20.colors
        for status, t_list in tickets_by_status.items():
            count = len(t_list)
            if count > 0:
                labels.append(f"{status} ({count})")
                sizes.append(count)

        if sizes:
            pie_ax.pie(sizes, labels=labels, startangle=140, colors=colors_map,
                       wedgeprops={'edgecolor': 'white'}, textprops={'fontsize': 9})
        else:
            pie_ax.text(0.5, 0.5, "No tickets found", ha='center', va='center', fontsize=12)
            pie_ax.axis('off')

        # Pending table area
        table_ax = fig.add_axes([0.05, 0.22, 0.9, 0.30])
        table_ax.axis('off')

        if pending_tickets:
            table_data = [["Ticket ID", "Subject", "Created Date", "Status"]] + [
                [t["id"], t["subject"], t["created_at"], t["status"]] for t in pending_tickets
            ]

            max_rows = 20
            scale_factor = min(1.2, max_rows / len(table_data)) if len(table_data) > 1 else 1.0

            tbl = table_ax.table(cellText=table_data, loc='upper center', cellLoc='left',
                                 colWidths=[0.12, 0.48, 0.2, 0.2])
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(9)
            tbl.scale(1, scale_factor)
            table_ax.set_title("Pending Tickets (On hold & Awaiting info)", fontsize=12, pad=5)
        else:
            # should not occur since pending_count>0, but guard anyway
            pending_ax = fig.add_axes([0.12, 0.30, 0.76, 0.15])
            pending_ax.axis('off')
            pending_box = Rectangle((0, 0), 1, 1, transform=pending_ax.transAxes, fill=False, edgecolor="#e6e6e6", linewidth=1)
            pending_ax.add_patch(pending_box)
            pending_ax.text(0.5, 0.6, "No pending tickets found", ha='center', va='center', fontsize=12, weight='bold', color="#0b2545")
            pending_ax.text(0.5, 0.35, "There are no tickets in 'On hold' or 'Awaiting info' statuses for the selected month.", ha='center', va='center', fontsize=10, color="#333333")

        # Footer note (only shown when pending tickets exist)
        note_ax = fig.add_axes([0.05, 0.02, 0.9, 0.06])
        note_ax.axis('off')
        note_text = "Please review the tickets in 'Awaiting info' status and provide your updates at the earliest convenience."
        note_ax.text(0.5, 0.5, textwrap.fill(note_text, width=90), ha='center', va='center', fontsize=9, color='gray', style='italic')

    else:
        # No pending tickets - show single clean white box with border only
        # If no tickets at all, we adjust the main message
        main_title = f"No pending tickets were found for {previous_month_str}."
        main_msg = ""  # no secondary line

        # central box (white background, border only)
        box_left = 0.12
        box_width = 0.76
        box_bottom = 0.48
        box_height = 0.28
        box_ax = fig.add_axes([box_left, box_bottom, box_width, box_height])
        box_ax.axis('off')
        # draw border-only rectangle
        border_box = Rectangle((0, 0), 1, 1, transform=box_ax.transAxes, fill=False, edgecolor="#d7dbe0", linewidth=1)
        box_ax.add_patch(border_box)

        # Add text centered
        wrapped_title = textwrap.fill(main_title, width=60)
        wrapped_msg = textwrap.fill(main_msg, width=70)
        box_ax.text(0.5, 0.62, wrapped_title, ha='center', va='center', fontsize=14, weight='bold', color="#0b2545")
        box_ax.text(0.5, 0.38, wrapped_msg, ha='center', va='center', fontsize=11, color="#333333")

    # Save PDF
    pdf = PdfPages(output_file)
    pdf.savefig(fig, bbox_inches='tight')
    pdf.close()
    plt.close(fig)
    print(f"✅ Final PDF generated: {output_file}")


# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    # Assume role and fetch token
    print("🔄 Assuming IAM Role & loading ClientCentral API token…")
    session = assume_role(ROLE_ARN, SESSION_NAME, EXTERNAL_ID)

    cc_token = fetch_cc_token(session)  # loads "cc-api-token" from Secrets Manager
    tickets = fetch_previous_month_tickets(cc_token, CC_ACCOUNT_ID)

    # Generate report
    generate_pdf_with_border_footer(tickets)