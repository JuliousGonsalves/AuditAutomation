import boto3
import botocore
from datetime import datetime, timedelta, timezone
from fpdf import FPDF

# ==========================================
# CONFIGURATION
# ==========================================
region_name = "us-east-1"
CLIENT_NAME = "Evosus"
REPORT_TITLE = "CloudOps Backup Audit Report"

# ==========================================
# HELPERS
# ==========================================
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
# CUSTOM BORDER PDF CLASS
# ==========================================
class BorderPDF(FPDF):
    def header(self):
        # Draw border rectangle
        self.set_draw_color(0, 0, 0)
        self.set_line_width(0.5)
        self.rect(5, 5, 200, 287)

        # Report title
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

        # Header
        for i, col in enumerate(header):
            self.cell(col_widths[i], 7, str(col), 1, 0, "C")
        self.ln()

        # Rows with alternating colors
        self.set_font("Arial", "", 10)
        fill = False
        for row in data:
            if len(row) < len(header):
                row += [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[:len(header)]

            # Highlight “No backup” rows in red italics
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
# AWS BACKUP DATA
# ==========================================
def get_rds_backups():
    rds = boto3.client("rds", region_name=region_name)
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

def get_ec2_backups():
    ec2 = boto3.client("ec2", region_name=region_name)
    ec2_res = boto3.resource("ec2", region_name=region_name)
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
        amis = [img for img in ec2_res.images.filter(Filters=[{"Name": "name", "Values": [f"*{inst_id}*"]}])]
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

def get_efs_backups():
    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    efs_client = boto3.client("efs", region_name=region_name)
    backup_client = boto3.client("backup", region_name=region_name)
    vaults = backup_client.list_backup_vaults()["BackupVaultList"]
    filesystems = efs_client.describe_file_systems()["FileSystems"]

    results = []
    for fs in filesystems:
        fs_id = fs["FileSystemId"]
        backups = []
        for v in vaults:
            try:
                rps = backup_client.list_recovery_points_by_backup_vault(
                    BackupVaultName=v["BackupVaultName"],
                    ByResourceArn=f"arn:aws:elasticfilesystem:{region_name}:{account_id}:file-system/{fs_id}"
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
            results.append([fs_id, "No backups configured", "-"])
    return results

# ==========================================
# GENERATE PDF
# ==========================================
def generate_pdf(filename=None):
    pdf = BorderPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # RDS
    pdf.section_title("RDS Backup Details")
    rds_data = get_rds_backups()
    if all("no backup" in str(row[1]).lower() for row in rds_data):
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No backups configured for RDS.", 0, 1)
        pdf.set_text_color(0, 0, 0)
    else:
        pdf.table(["Snapshot ID", "Created On"], rds_data)

    # EC2
    pdf.section_title("EC2 Backup Details")
    ec2_data = get_ec2_backups()
    if all("no backup" in str(row[1]).lower() for row in ec2_data):
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 8, "No backups configured for EC2.", 0, 1)
        pdf.set_text_color(0, 0, 0)
    else:
        pdf.table(["Instance / AMI", "Backup ID", "Created On"], ec2_data)

    # EFS
    pdf.section_title("EFS Backup Details")
    efs_data = get_efs_backups()
    if all("no backup" in str(row[1]).lower() for row in efs_data):
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
