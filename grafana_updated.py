import datetime
import os
import re
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
from fpdf import FPDF
from PIL import Image

# ===== ADDED FOR SECRETS MANAGER =====
import boto3
import json

def fetch_grafana_token(secret_name):
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)
    data = json.loads(response["SecretString"])
    return data.get("grafana-api-token")
# =====================================

# ========================
# CLIENT CONFIGURATION AREA
# ========================
CLIENT_CONFIG = {
    "client_name": "Evosus",  # Unique identifier for this client

    # Grafana Instance Details
    "grafana_url": "https://g-e6010bcf26.grafana-workspace.us-east-1.amazonaws.com",
    "dashboard_uid": "dev3qsnbddgxsc",
    "dashboard_slug": "domain-uptime-status",
    "service_token": "",  # Will be loaded from Secrets Manager
    "org_id": "1",  # Grafana organization ID

    # Data Filtering
    "namespaces": ["development"],
    "environments": ["lou-dev"],

    # Output Settings
    "output_dir": "./screenshots"
}

# ========================
# PREDEFINED HEADINGS (in order of screenshots)
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
# CUSTOM PDF CLASS (Adds Border, Footer)
# ========================
class BorderPDF(FPDF):
    def header(self):
        self.set_draw_color(0, 0, 0)
        self.set_line_width(0.5)
        self.rect(5, 5, 200, 287)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, f"{CLIENT_CONFIG['client_name']} | CloudOps Monitoring Report | Page {self.page_no()}", align="C")

# ========================
# HELPER FUNCTIONS
# ========================
def get_previous_month_range():
    today = datetime.date.today()
    first_day_this_month = today.replace(day=1)
    last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)

    from_epoch = int(datetime.datetime.combine(first_day_prev_month, datetime.time.min).timestamp() * 1000)
    to_epoch = int(datetime.datetime.combine(last_day_prev_month, datetime.time.max).timestamp() * 1000)
    month_name = first_day_prev_month.strftime("%B %Y")

    return from_epoch, to_epoch, month_name

# ========================
# MAIN SCRIPT
# ========================
def main():

    # ===== LOAD TOKEN FROM SECRETS MANAGER =====
    CLIENT_CONFIG["service_token"] = fetch_grafana_token("grafana/service-account-token")
    print("🔐 Loaded Grafana API token from AWS Secrets Manager")
    # ===========================================

    os.makedirs(CLIENT_CONFIG["output_dir"], exist_ok=True)
    from_epoch, to_epoch, month_name = get_previous_month_range()

    preset_cookies = [
        {"name": "cookieconsent_status", "value": "dismiss", "url": CLIENT_CONFIG["grafana_url"]},
    ]

    pdf = BorderPDF(unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Authorization": f"Bearer {CLIENT_CONFIG['service_token']}"}
        )
        context.add_cookies(preset_cookies)
        page = context.new_page()

        # ====== TITLE PAGE ======
        pdf.add_page()
        pdf.set_font("Arial", "B", 18)
        pdf.cell(0, 15, f"Monitoring Report - {month_name}", align="C", ln=True)
        pdf.set_font("Arial", "", 13)
        pdf.cell(0, 10, f"Generated on: {datetime.datetime.now().strftime('%B %d, %Y')}", align="C", ln=True)
        pdf.ln(25)

        pdf.set_draw_color(180, 180, 180)
        pdf.set_line_width(0.3)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(15)

        for ns in CLIENT_CONFIG["namespaces"]:
            for env in CLIENT_CONFIG["environments"]:
                url = (
                    f"{CLIENT_CONFIG['grafana_url']}/d/{CLIENT_CONFIG['dashboard_uid']}/{CLIENT_CONFIG['dashboard_slug']}"
                    f"?orgId={CLIENT_CONFIG['org_id']}&var-namespace={ns}&var-Environment_Name={env}&var-pod_name=All"
                    f"&from={from_epoch}&to={to_epoch}&kiosk=1&tz=UTC"
                )

                print(f"🚀 Processing client '{CLIENT_CONFIG['client_name']}': {ns}.{env} for {month_name}")
                page.goto(url, wait_until="networkidle", timeout=120_000)

                accepted = False
                for sel in ACCEPT_BUTTON_SELECTORS:
                    try:
                        page.wait_for_selector(sel, timeout=5000)
                        page.click(sel)
                        accepted = True
                        page.wait_for_timeout(1000)
                        break
                    except PWTimeoutError:
                        continue
                    except Exception:
                        continue

                if not accepted:
                    hide_css = "\n".join(
                        [f"{s} {{ display:none !important; visibility:hidden !important; opacity:0 !important; }}" 
                         for s in BANNER_SELECTORS]
                    )
                    page.add_style_tag(content=hide_css)
                    page.wait_for_timeout(500)

                page.evaluate("""
                    () => {
                        const texts = ['cookie', 'cookies', 'consent', 'gdpr'];
                        const all = Array.from(document.querySelectorAll('div,section,aside'));
                        all.forEach(el => {
                            try {
                                const txt = (el.innerText || '').toLowerCase();
                                if (texts.some(t => txt.includes(t)) && (el.offsetHeight > 10 || el.offsetWidth > 10)) {
                                    el.remove();
                                }
                            } catch(e){}
                        });
                    }
                """)
                page.wait_for_timeout(3000)

                panel_selectors = [".panel-container", ".react-grid-item", "[data-panelid]", ".grafana-panel", ".panel", ".panel-content"]
                all_panels = []
                for selector in panel_selectors:
                    panels = page.query_selector_all(selector)
                    if panels:
                        all_panels.extend(panels)
                        break

                if not all_panels:
                    potential_panels = page.query_selector_all("div[class*='panel'], div[class*='chart'], div[class*='graph']")
                    all_panels = [p for p in potential_panels if p.bounding_box() and p.bounding_box()['height'] > 100]

                for i, panel in enumerate(all_panels):
                    try:
                        panel.scroll_into_view_if_needed()
                        page.wait_for_timeout(1000)

                        box = panel.bounding_box()
                        if not box or box['width'] < 50 or box['height'] < 50:
                            continue

                        heading = PREDEFINED_HEADINGS[i] if i < len(PREDEFINED_HEADINGS) else f"Panel_{i+1}"
                        sanitized_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", heading)
                        img_path = f"{CLIENT_CONFIG['output_dir']}/{CLIENT_CONFIG['client_name']}_{month_name}_{sanitized_title}.png"

                        panel.screenshot(path=img_path)
                        print(f"✅ Saved: {img_path}")

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

                        pdf.set_font("Arial", "B", 13)
                        pdf.multi_cell(0, 10, heading, align="C")
                        pdf.ln(5)

                        x_offset = (210 - width_mm) / 2
                        y_start = pdf.get_y()
                        pdf.image(img_path, x=x_offset, y=y_start, w=width_mm, h=height_mm)
                        pdf.set_y(y_start + height_mm + 8)

                        pdf.set_draw_color(180, 180, 180)
                        pdf.set_line_width(0.3)
                        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                        pdf.ln(10)

                        print(f"🖼️ Added to PDF: {heading}")

                    except Exception as e:
                        print(f"❌ Failed screenshot for panel {i+1}: {e}")

        browser.close()

        pdf_output_path = os.path.join(
            CLIENT_CONFIG["output_dir"],
            f"{CLIENT_CONFIG['client_name']}_Monitoring_Report_{month_name.replace(' ', '_')}.pdf"
        )
        pdf.output(pdf_output_path)
        print(f"✅ Monitoring report created successfully: {pdf_output_path}")


if __name__ == "__main__":
    main()
