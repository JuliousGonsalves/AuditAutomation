import boto3
import botocore
from datetime import datetime, timedelta, timezone
from fpdf import FPDF

# ==========================================
# CONFIGURATION
# ==========================================
AWS_REGION = "us-east-1"

ROLE_ARN = "arn:aws:iam::337909763810:role/audit-automation-role"  # <<< UPDATE THIS
SESSION_NAME = "backup-audit-session"
EXTERNAL_ID = None  # Only if required

CLIENT_NAME = "Evosus"
REPORT_TITLE = "CloudOps Backup Audit Report"

# ==========================================
# STS ASSUME ROLE
# ==========================================
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

# ==========================================
# HELPERS
# ==========================================
def aws_client(service, session):
    """Create boto3 client using assumed role credentials."""
    return boto3.client(
        service,
        region_name=AWS_REGION,
        aws_access_key_id=session["aws_access_key_id"],
        aws_secret_access_key=session["aws_secret_access_key"],
        aws_session_token=session["aws_session_token"]
    )

def aws_resource(service, session):
    """Create boto3 resource using assumed role credentials."""
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
# PDF CLASS
# ==========================================
class BorderPDF(FPDF):
    def header(self):
        self.set_draw_color(0, 0, 0)
        self.set_line_width(0.5)
        self.rect(5, 5, 200, 287)

        self.set_font("Arial", "B", 14)
        self.cell(0, 10, REPORT_TITLE, 0, 1, "C")
        self.set_font("Arial", "", 10)
        self.cell(0, 8, f"Generated on: {datetime.now().strftime('%B %d, %Y %H:%M:%S')}", 0, 1, "C")
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, f"{CLIENT_NAME} | {REPORT_TITLE} | Page {self.page_no()}", align="C")

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

        # Data rows
        self.set_font("Arial", "", 10)
        fill = False

        for row in data:
            if len(row) < len(header):
                row += [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[:len(header)]

            # Highlight “no backup” rows
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

    result = []
    for cluster in clusters:
        cluster_id = cluster["DBClusterIdentifier"]
        try:
            snapshots = rds.describe_db_cluster_snapshots(DBClusterIdentifier=cluster_id).get("DBClusterSnapshots", [])
            recent = sorted(snapshots, key=lambda x: x["SnapshotCreateTime"], reverse=True)[:5]

            if recent:
                for snap in recent:
                    result.append([snap["DBClusterSnapshotIdentifier"], format_time(snap["SnapshotCreateTime"])])
            else:
                result.append([cluster_id, "No backups configured"])

        except botocore.exceptions.ClientError as e:
            result.append([cluster_id, f"Error: {e}"])

    return result

# ==========================================
# EC2 BACKUPS
# ==========================================
def get_ec2_backups(session):
    ec2 = aws_client("ec2", session)
    ec2_res = aws_resource("ec2", session)

    EXCLUDE_TAGS = ["autoscaling", "karpenter"]

    def has_excluded_tags(tags):
        for tag in tags:
            for ex in EXCLUDE_TAGS:
                if ex in tag["Key"].lower() or ex in tag["Value"].lower():
                    return True
        return False

    instances = []
    for r in ec2.describe_instances()["Reservations"]:
        for i in r["Instances"]:
            if not has_excluded_tags(i.get("Tags", [])):
                instances.append(i["InstanceId"])

    results = []
    for inst_id in instances:
        amis = list(ec2_res.images.filter(Filters=[{"Name": "name", "Values": [f"*{inst_id}*"]}]))

        six_weeks_ago = datetime.now(timezone.utc) - timedelta(weeks=6)
        recent_amis = sorted(
            [a for a in amis if datetime.strptime(a.creation_date, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) >= six_weeks_ago],
            key=lambda x: x.creation_date,
            reverse=True
        )[:5]

        if recent_amis:
            for a in recent_amis:
                results.append([inst_id + " (AMI)", a.id, format_time(a.creation_date)])
        else:
            results.append([inst_id, "No AMIs / DLM backups configured", "-"])

    return results

# ==========================================
# EFS BACKUPS
# ==========================================
def get_efs_backups(session):
    sts_client = aws_client("sts", session)
    account_id = sts_client.get_caller_identity()["Account"]

    efs_client = aws_client("efs", session)
    backup_client = aws_client("backup", session)

    vaults = backup_client.list_backup_vaults()["BackupVaultList"]
    filesystems = efs_client.describe_file_systems().get("FileSystems", [])

    results = []

    for fs in filesystems:
        fs_id = fs["FileSystemId"]
        backups = []

        for v in vaults:
            try:
                rps = backup_client.list_recovery_points_by_backup_vault(
                    BackupVaultName=v["BackupVaultName"],
                    ByResourceArn=f"arn:aws:elasticfilesystem:{AWS_REGION}:{account_id}:file-system/{fs_id}"
                ).get("RecoveryPoints", [])
                backups.extend(rps)
            except botocore.exceptions.ClientError:
                continue

        if backups:
            recent = sorted(backups, key=lambda x: x["CreationDate"], reverse=True)[:5]
            for b in recent:
                backup_id = b["RecoveryPointArn"].split(":")[-1]
                results.append([fs_id, backup_id, format_time(b["CreationDate"])])
        else:
            # No backups for this FS
            results.append([fs_id, "No backups configured", "-"])

    return results

# ==========================================
# GENERATE PDF
# ==========================================
def generate_pdf(filename=None):
    print("🔄 Assuming IAM Role for Backup Audit...")
    session = assume_role(ROLE_ARN, SESSION_NAME, EXTERNAL_ID)

    pdf = BorderPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # RDS SECTION
    pdf.section_title("RDS Backup Details")
    rds_data = get_rds_backups(session)
    if not rds_data or all("no backup" in str(row[1]).lower() for row in rds_data):
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No backups configured for RDS.", 0, 1)
        pdf.set_text_color(0, 0, 0)
    else:
        pdf.table(["Snapshot ID", "Created On"], rds_data)

    # EC2 SECTION
    pdf.section_title("EC2 Backup Details")
    ec2_data = get_ec2_backups(session)
    if not ec2_data or all("no backup" in str(row[1]).lower() for row in ec2_data):
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No backups configured for EC2.", 0, 1)
        pdf.set_text_color(0, 0, 0)
    else:
        pdf.table(["Instance / AMI", "Backup ID", "Created On"], ec2_data)

    # EFS SECTION (with fix)
    pdf.section_title("EFS Backup Details")
    efs_data = get_efs_backups(session)

    if not efs_data:
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No EFS file systems detected or no backups found.", 0, 1)
        pdf.set_text_color(0, 0, 0)

    elif all("no backup" in str(row[1]).lower() for row in efs_data):
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No backups configured for EFS.", 0, 1)
        pdf.set_text_color(0, 0, 0)

    else:
        pdf.table(["EFS ID", "Backup ID", "Created On"], efs_data)

    if not filename:
        filename = f"{CLIENT_NAME}_Backup_Audit_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    pdf.output(filename)
    print(f"✅ Backup audit report generated: {filename}")

# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    generate_pdf()