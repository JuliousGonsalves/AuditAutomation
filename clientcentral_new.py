import urllib3
import urllib.parse
import json
import datetime
from datetime import date
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Status mapping
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
            data = json.loads(resp.data)
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

            created_date = datetime.datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%SZ")
            if created_date.month == previous_month and created_date.year == year:
                status_name = STATUS_MAPPING.get(status["id"], f"Unknown Status ID: {status['id']}")
                tickets_by_status.setdefault(status_name, []).append({
                    "id": ticket["id"],
                    "subject": ticket["subject"],
                    "created_at": created_date.strftime("%Y-%m-%d"),
                    "status": status_name
                })

        page += 1

    return tickets_by_status


def generate_pdf_with_border_footer(tickets_by_status, output_file="Ticket_Report_Final.pdf"):
    # A4 page size
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.05)

    # --- Pie Chart centered ---
    labels = []
    sizes = []
    for status, t_list in tickets_by_status.items():
        count = len(t_list)
        if count > 0:
            labels.append(f"{status} ({count})")
            sizes.append(count)

    ax1 = fig.add_axes([0.15, 0.58, 0.7, 0.35])  # centered horizontally
    if sizes:
        ax1.pie(sizes, labels=labels, startangle=140, colors=plt.cm.tab20.colors,
                wedgeprops={'edgecolor':'white'}, textprops={'fontsize':9})
        ax1.set_title("Client Central Ticket Status Report", fontsize=14, pad=10)
    else:
        ax1.text(0.5, 0.5, "No tickets found", ha='center', va='center', fontsize=12)
        ax1.axis('off')

    # --- Pending Tickets Table ---
    pending_statuses = ["On hold", "Awaiting info"]
    pending_tickets = []
    for status in pending_statuses:
        pending_tickets.extend(tickets_by_status.get(status, []))

    ax2 = fig.add_axes([0.05, 0.22, 0.9, 0.33])
    ax2.axis('off')

    if pending_tickets:
        table_data = [["Ticket ID", "Subject", "Created Date", "Status"]] + [
            [t["id"], t["subject"], t["created_at"], t["status"]] for t in pending_tickets
        ]

        max_rows = 20
        scale_factor = min(1.2, max_rows / len(table_data)) if len(table_data) > 1 else 1.0

        table = ax2.table(cellText=table_data, loc='upper center', cellLoc='left',
                          colWidths=[0.12, 0.48, 0.2, 0.2])
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1, scale_factor)
        ax2.set_title("Pending Tickets (On hold & Awaiting info)", fontsize=12, pad=5)
    else:
        ax2.text(0.5, 0.5, "No pending tickets found", ha='center', va='center', fontsize=12)

    # --- Professional Note Below Table ---
    ax3 = fig.add_axes([0.05, 0.1, 0.9, 0.08])
    ax3.axis('off')
    note_text = ("Please review the tickets in 'Awaiting info' status "
                 "and provide your updates at the earliest convenience.")
    ax3.text(0.5, 0.5, note_text, ha='center', va='center', fontsize=10, color='gray', style='italic', wrap=True)

    # --- Add border matching SSO PDF style ---
    ax_border = fig.add_axes([0, 0, 1, 1])
    ax_border.axis('off')
    rect = Rectangle((0.03, 0.03), 0.94, 0.94, fill=False, linewidth=1, edgecolor='black', transform=ax_border.transAxes)
    ax_border.add_patch(rect)

    # --- Save PDF ---
    pdf = PdfPages(output_file)
    pdf.savefig(fig, bbox_inches='tight')
    pdf.close()
    plt.close(fig)

    print(f"✅ Final PDF with professional border and footer generated: {output_file}")


# ----------------- Main Execution -----------------
if __name__ == "__main__":
    cc_token = "28585-S38M3yB-PqF5cE5bqIrlKU0UtCpMxjD3lyZ026avZM5Scs9qSzHF_HvvnR"  # Add your token here
    cc_account_id = 6166

    tickets = fetch_previous_month_tickets(cc_token, cc_account_id)
    generate_pdf_with_border_footer(tickets)
