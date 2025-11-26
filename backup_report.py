import boto3
import botocore
from datetime import datetime, timedelta, timezone, date
from fpdf import FPDF

# ==========================================
# CONFIGURATION
# ==========================================
AWS_REGION = "us-east-1"

ROLE_ARN = "arn:aws:iam::337909763810:role/audit-automation-role"
SESSION_NAME = "backup-audit-session"
EXTERNAL_ID = None

CLIENT_NAME = "Evosus"
REPORT_TITLE = "Backup Audit Report"

# Previous month (matches CC report logic)
_today = date.today()
_prev_month = _today.month - 1 or 12
_prev_year = _today.year if _today.month != 1 else _today.year - 1
REPORT_MONTH_STR = date(_prev_year, _prev_month, 1).strftime("%B %Y")


# ==========================================
# STS ASSUME ROLE
# ==========================================
def assume_role(role_arn, session_name, external_id=None):
    sts_client = boto3.client("sts", region_name=AWS_REGION)

    args = {"RoleArn": role_arn, "RoleSessionName": session_name}
    if external_id:
        args["ExternalId"] = external_id

    creds = sts_client.assume_role(**args)["Credentials"]

    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"]
    }


# ==========================================
# HELPERS
# ==========================================
def aws_client(service, session):
    return boto3.client(
        service,
        region_name=AWS_REGION,
        aws_access_key_id=session["aws_access_key_id"],
        aws_secret_access_key=session["aws_secret_access_key"],
        aws_session_token=session["aws_session_token"]
    )


def aws_resource(service, session):
    return boto3.resource(
        service,
        region_name=AWS_REGION,
        aws_access_key_id=session["aws_access_key_id"],
        aws_secret_access_key=session["aws_secret_access_key"],
        aws_session_token=session["aws_session_token"]
    )


def format_time(dt):
    if not dt:
        return "-"
    if isinstance(dt, str):
        try:
            dt = datetime.strptime(dt, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except Exception:
            return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ==========================================
# PDF CLASS (MATCHES CLIENTCENTRAL LAYOUT)
# ==========================================
class BorderPDF(FPDF):
    def header(self):
        # Outer border
        self.set_draw_color(0, 0, 0)
        self.set_line_width(0.5)
        self.rect(5, 5, 200, 287)

        # Dark blue header bar
        self.set_xy(5, 5)
        self.set_fill_color(0, 51, 102)  # #003366
        self.rect(5, 5, 200, 15, "F")

        # Header text (white, centered)
        header_text = f"{REPORT_TITLE} - {CLIENT_NAME} - {REPORT_MONTH_STR}"
        self.set_text_color(255, 255, 255)
        self.set_font("Arial", "B", 12)
        self.set_xy(5, 7)
        self.cell(200, 10, header_text, 0, 1, "C")

        # Reset text color
        self.set_text_color(0, 0, 0)
        self.ln(8)

    def footer(self):
        # ClientCentral-style footer
        self.set_y(-13)
        self.set_font("Arial", "I", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Monthly Audit Report - {REPORT_MONTH_STR}", 0, 0, "C")

    # Original table formatting preserved
    def section_title(self, title):
        self.set_font("Arial", "B", 12)
        self.set_fill_color(200, 220, 255)
        self.cell(0, 8, title, 0, 1, "L", fill=True)
        self.ln(2)

    def table(self, header, data):
        if not data:
            return

        self.set_font("Arial", "B", 10)
        page_width = self.w - 2 * self.l_margin
        col_widths = [page_width / len(header)] * len(header)

        # Header row
        for i, col in enumerate(header):
            self.cell(col_widths[i], 7, str(col), 1, 0, "C")
        self.ln()

        self.set_font("Arial", "", 10)
        fill = False

        for row in data:
            if len(row) < len(header):
                row += [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[:len(header)]

            if any("no backup" in str(cell).lower() for cell in row):
                self.set_font("Arial", "I", 10)
                self.set_text_color(200, 0, 0)
            else:
                self.set_font("Arial", "", 10)
                self.set_text_color(0, 0, 0)

            self.set_fill_color(245, 245, 245 if fill else 255)

            for i, item in enumerate(row):
                self.cell(col_widths[i], 6, str(item), 1, 0, "C", fill=fill)

            self.ln()
            fill = not fill

        self.set_text_color(0, 0, 0)
        self.ln(3)


# ==========================================
# RDS BACKUPS
# ==========================================
def get_rds_backups(session):
    rds = aws_client("rds", session)
    clusters = rds.describe_db_clusters().get("DBClusters", [])

    out = []
    for cluster in clusters:
        cid = cluster["DBClusterIdentifier"]
        try:
            snaps = rds.describe_db_cluster_snapshots(DBClusterIdentifier=cid)["DBClusterSnapshots"]
            snaps = sorted(snaps, key=lambda x: x["SnapshotCreateTime"], reverse=True)[:5]

            if snaps:
                for s in snaps:
                    out.append([s["DBClusterSnapshotIdentifier"], format_time(s["SnapshotCreateTime"])])
            else:
                out.append([cid, "No backups configured"])

        except botocore.exceptions.ClientError as e:
            out.append([cid, f"Error: {e}"])

    return out


# ==========================================
# EC2 BACKUPS
# ==========================================
def get_ec2_backups(session):
    ec2 = aws_client("ec2", session)
    ec2_res = aws_resource("ec2", session)

    EXCLUDE = ["autoscaling", "karpenter"]

    def skip(tags):
        for t in tags:
            if any(x in t["Key"].lower() or x in t["Value"].lower() for x in EXCLUDE):
                return True
        return False

    instances = []
    for r in ec2.describe_instances()["Reservations"]:
        for i in r["Instances"]:
            if not skip(i.get("Tags", [])):
                instances.append(i["InstanceId"])

    out = []
    for inst in instances:
        amis = list(ec2_res.images.filter(Filters=[{"Name": "name", "Values": [f"*{inst}*"]}]))

        six_weeks = datetime.now(timezone.utc) - timedelta(weeks=6)
        recent = [
            a for a in amis
            if datetime.strptime(a.creation_date, "%Y-%m-%dT%H:%M:%S.%fZ")
            .replace(tzinfo=timezone.utc) >= six_weeks
        ]

        recent = sorted(recent, key=lambda x: x.creation_date, reverse=True)[:5]

        if recent:
            for a in recent:
                out.append([inst + " (AMI)", a.id, format_time(a.creation_date)])
        else:
            out.append([inst, "No AMIs / DLM backups configured", "-"])

    return out


# ==========================================
# EFS BACKUPS
# ==========================================
def get_efs_backups(session):
    sts = aws_client("sts", session)
    account = sts.get_caller_identity()["Account"]

    efs = aws_client("efs", session)
    backup = aws_client("backup", session)

    vaults = backup.list_backup_vaults()["BackupVaultList"]
    filesystems = efs.describe_file_systems().get("FileSystems", [])

    out = []

    for fs in filesystems:
        fs_id = fs["FileSystemId"]
        backups = []

        for v in vaults:
            try:
                rps = backup.list_recovery_points_by_backup_vault(
                    BackupVaultName=v["BackupVaultName"],
                    ByResourceArn=f"arn:aws:elasticfilesystem:{AWS_REGION}:{account}:file-system/{fs_id}"
                )["RecoveryPoints"]
                backups.extend(rps)
            except botocore.exceptions.ClientError:
                pass

        if backups:
            recent = sorted(backups, key=lambda x: x["CreationDate"], reverse=True)[:5]
            for b in recent:
                backup_id = b["RecoveryPointArn"].split(":")[-1]
                out.append([fs_id, backup_id, format_time(b["CreationDate"])])
        else:
            out.append([fs_id, "No backups configured", "-"])

    return out


# ==========================================
# GENERATE FINAL PDF
# ==========================================
def generate_pdf(filename=None):
    print("🔄 Assuming IAM Role for Backup Audit...")
    session = assume_role(ROLE_ARN, SESSION_NAME, EXTERNAL_ID)

    pdf = BorderPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # RDS SECTION
    pdf.section_title("RDS Backup Details")
    rds = get_rds_backups(session)
    if not rds or all("no backup" in str(r[1]).lower() for r in rds):
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No backups configured for RDS.", 0, 1)
        pdf.set_text_color(0, 0, 0)
    else:
        pdf.table(["Snapshot ID", "Created On"], rds)

    # EC2 SECTION
    pdf.section_title("EC2 Backup Details")
    ec2 = get_ec2_backups(session)
    if not ec2 or all("no ami" in str(r[1]).lower() for r in ec2):
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No backups configured for EC2.", 0, 1)
        pdf.set_text_color(0, 0, 0)
    else:
        pdf.table(["Instance / AMI", "Backup ID", "Created On"], ec2)

    # EFS SECTION
    pdf.section_title("EFS Backup Details")
    efs = get_efs_backups(session)
    if not efs:
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No EFS file systems detected or no backups found.", 0, 1)
        pdf.set_text_color(0, 0, 0)
    elif all("no backups" in str(r[1]).lower() for r in efs):
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No backups configured for EFS.", 0, 1)
        pdf.set_text_color(0, 0, 0)
    else:
        pdf.table(["EFS ID", "Backup ID", "Created On"], efs)

    if not filename:
        filename = "Backup_Audit_Report.pdf"

    pdf.output(filename)
    print(f"✅ Backup audit report generated: {filename}")


# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    generate_pdf()