import datetime
import os
import re
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from fpdf import FPDF
from PIL import Image

# ===== AWS ROLE + SECRETS MANAGER =====
import boto3
import json

# ===========================
# ROLE CONFIGURATION
# ===========================
ROLE_ARN = "arn:aws:iam::445567102114:role/automation-from-prod"  # <<< UPDATE THIS
SESSION_NAME = "grafana-report-session"
EXTERNAL_ID = None

AWS_REGION = "us-east-1"


def assume_role(role_arn, session_name, external_id=None):
    sts_client = boto3.client("sts", region_name=AWS_REGION)

    if external_id:
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            ExternalId=external_id
        )
    else:
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name
        )

    creds = response["Credentials"]

    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"]
    }


def fetch_grafana_token(secret_name, session):
    client = boto3.client(
        "secretsmanager",
        region_name=AWS_REGION,
        aws_access_key_id=session["aws_access_key_id"],
        aws_secret_access_key=session["aws_secret_access_key"],
        aws_session_token=session["aws_session_token"]
    )

    response = client.get_secret_value(SecretId=secret_name)
    data = json.loads(response["SecretString"])
    return data.get("grafana-api-token")
# =====================================


# ========================
# CLIENT CONFIGURATION AREA
# ========================
CLIENT_CONFIG = {
    "client_name": "Evosus",

    # Grafana Instance Details
    "grafana_url": "https://g-e6010bcf26.grafana-workspace.us-east-1.amazonaws.com",
    "dashboard_uid": "dev3qsnbddgxsc",
    "dashboard_slug": "domain-uptime-status",
    "service_token": "",
    "org_id": "1",

    # Data Filtering
    "namespaces": ["development"],
    "environments": ["lou-dev"],

    # Output Settings
    "output_dir": "./screenshots"
}

# ========================
# PREDEFINED HEADINGS
# ========================
PREDEFINED_HEADINGS = [
    "lou-dev.evosus.com Uptime Panel",
    "louservices-dev.evosus.com Panel",
    "lou-dev.evosus.com health Panel",
    "lou-dev.evosus.com health Panel"
]

# ========================
# CONSTANTS
# ========================
ACCEPT_BUTTON_SELECTORS = [
    'button:has-text("Accept")',
    'button:has-text("I Accept")',
    'button:has-text("Accept all")',
    'button:has-text("Got it")',
    'button:has-text("Ok")',
    '[aria-label="accept cookies"]',
    '.cc-btn.cc-accept',
    '.cookie-consent button',
    '#cookie-accept',
    '[data-testid="cookie-accept"]'
]

BANNER_SELECTORS = [
    '.cc-window', '.cookie-consent', '#cookie-banner', '[data-testid="cookie-banner"]', '.cookie-popup'
]

# ========================
# PDF CLASS
# ========================
class BorderPDF(FPDF):
    def header(self):
        # Outer border
        self.set_draw_color(0, 0, 0)
        self.set_line_width(0.6)
        self.rect(5, 5, 200, 287)

        # Dark blue header bar
        self.set_fill_color(0, 51, 102)
        self.rect(5, 5, 200, 15, style="F")

        # Header title (ASCII only)
        self.set_xy(5, 5)
        self.set_text_color(255, 255, 255)
        self.set_font("Arial", "B", 11)
        self.cell(
            200,
            15,
            f"Monitoring Report - Evosus - {self.report_month}",
            border=0,
            ln=1,
            align="C"
        )

    def footer(self):
        self.set_y(-15)
        self.set_text_color(100, 100, 100)
        self.set_font("Arial", "I", 8)
        self.cell(0, 10, f"Monthly Audit Report - {self.report_month}", align="C")


# ========================
# DATE RANGE HELPER
# ========================
def get_previous_month_range():
    today = datetime.date.today()
    first_day_this_month = today.replace(day=1)
    last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)

    from_epoch = int(datetime.datetime.combine(first_day_prev_month, datetime.time.min).timestamp() * 1000)
    to_epoch = int(datetime.datetime.combine(last_day_prev_month, datetime.time.max).timestamp() * 1000)
    month_name = first_day_prev_month.strftime("%B %Y")  # ASCII only

    return from_epoch, to_epoch, month_name


# ========================
# MAIN SCRIPT
# ========================
def main():

    print("🔄 Assuming IAM role…")
    session = assume_role(ROLE_ARN, SESSION_NAME, EXTERNAL_ID)

    CLIENT_CONFIG["service_token"] = fetch_grafana_token("grafana/service-account-token", session)
    print("🔐 Loaded Grafana API token using assumed role.")

    os.makedirs(CLIENT_CONFIG["output_dir"], exist_ok=True)
    from_epoch, to_epoch, month_name = get_previous_month_range()

    preset_cookies = [
        {"name": "cookieconsent_status", "value": "dismiss", "url": CLIENT_CONFIG["grafana_url"]},
    ]

    pdf = BorderPDF(unit="mm", format="A4")
    pdf.report_month = month_name
    pdf.set_auto_page_break(auto=True, margin=15)

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)

        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Authorization": f"Bearer {CLIENT_CONFIG['service_token']}"}
        )
        context.add_cookies(preset_cookies)
        page = context.new_page()

        # ===== TITLE PAGE =====
        pdf.add_page()
        pdf.ln(20)

        # ===== LOOP ENVIRONMENTS =====
        for ns in CLIENT_CONFIG["namespaces"]:
            for env in CLIENT_CONFIG["environments"]:
                url = (
                    f"{CLIENT_CONFIG['grafana_url']}/d/{CLIENT_CONFIG['dashboard_uid']}/{CLIENT_CONFIG['dashboard_slug']}"
                    f"?orgId={CLIENT_CONFIG['org_id']}&var-namespace={ns}&var-Environment_Name={env}&var-pod_name=All"
                    f"&from={from_epoch}&to={to_epoch}&kiosk=1&tz=UTC"
                )

                print(f"🚀 Processing {ns}.{env} for {month_name}")
                page.goto(url, wait_until="networkidle", timeout=120000)

                accepted = False
                for sel in ACCEPT_BUTTON_SELECTORS:
                    try:
                        page.wait_for_selector(sel, timeout=5000)
                        page.click(sel)
                        accepted = True
                        break
                    except:
                        pass

                if not accepted:
                    hide_css = "\n".join([f"{s} {{ display:none !important; }}" for s in BANNER_SELECTORS])
                    page.add_style_tag(content=hide_css)

                page.wait_for_timeout(2500)

                # PANEL DETECTION
                panel_selectors = [".panel-container", ".react-grid-item", "[data-panelid]", ".grafana-panel"]
                all_panels = []

                for selector in panel_selectors:
                    panels = page.query_selector_all(selector)
                    if panels:
                        all_panels.extend(panels)
                        break

                if not all_panels:
                    potential_panels = page.query_selector_all("div[class*='panel']")
                    all_panels = [p for p in potential_panels if p.bounding_box()]

                # SCREENSHOTS
                for i, panel in enumerate(all_panels):
                    try:
                        panel.scroll_into_view_if_needed()
                        page.wait_for_timeout(800)

                        box = panel.bounding_box()
                        if not box:
                            continue

                        heading = PREDEFINED_HEADINGS[i] if i < len(PREDEFINED_HEADINGS) else f"Panel_{i+1}"
                        sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "_", heading)
                        img_path = f"{CLIENT_CONFIG['output_dir']}/{CLIENT_CONFIG['client_name']}_{month_name}_{sanitized}.png"

                        panel.screenshot(path=img_path)
                        print(f"✅ Saved: {img_path}")

                        # IMAGE SIZING
                        image = Image.open(img_path)
                        width, height = image.size

                        a4_width_mm, a4_height_mm = 200, 277
                        width_mm = width * 25.4 / 96
                        height_mm = height * 25.4 / 96
                        scale = min((a4_width_mm - 20) / width_mm, (a4_height_mm - 40) / height_mm)
                        width_mm *= scale
                        height_mm *= scale

                        if pdf.get_y() + height_mm + 40 > 280:
                            pdf.add_page()

                        # Heading
                        pdf.set_font("Arial", "B", 13)
                        pdf.multi_cell(0, 10, heading, align="C")
                        pdf.ln(5)

                        x_offset = (210 - width_mm) / 2
                        y_start = pdf.get_y()
                        pdf.image(img_path, x=x_offset, y=y_start, w=width_mm, h=height_mm)

                        pdf.set_y(y_start + height_mm + 10)

                    except Exception as e:
                        print(f"❌ Error screenshot panel {i+1}: {e}")

        browser.close()

        pdf_out = os.path.join(
            CLIENT_CONFIG["output_dir"],
            f"Monitoring_Report.pdf"
        )

        pdf.output(pdf_out)
        print(f"✅ Monitoring report created: {pdf_out}")


if __name__ == "__main__":
    main()