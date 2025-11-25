import boto3
import json
import datetime
import sys

# -----------------------------
# AWS Configuration
# -----------------------------
AWS_REGION = "us-east-1"

# Update these for your environment:
ROLE_ARN = "arn:aws:iam::445567102114:role/automation-from-prod"   # <<< REQUIRED
SESSION_NAME = "grafana-token-rotation-session"
EXTERNAL_ID = None   # if your role requires external ID

# -----------------------------
# Grafana Token Config
# -----------------------------
WORKSPACE_ID = "g-e6010bcf26"
SERVICE_ACCOUNT_ID = "38"
SECRET_NAME = "grafana/service-account-token"
EXPIRES_IN = 30 * 24 * 60 * 60  # 30 days


# -----------------------------
# STS Assume Role
# -----------------------------
def assume_role(role_arn, session_name, external_id=None):
    sts = boto3.client("sts", region_name=AWS_REGION)
    params = {"RoleArn": role_arn, "RoleSessionName": session_name}
    if external_id:
        params["ExternalId"] = external_id

    resp = sts.assume_role(**params)
    creds = resp["Credentials"]
    return {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"]
    }


# -----------------------------
# AWS Client Helper
# -----------------------------
def aws_client(service, session):
    return boto3.client(
        service,
        region_name=AWS_REGION,
        aws_access_key_id=session["aws_access_key_id"],
        aws_secret_access_key=session["aws_secret_access_key"],
        aws_session_token=session["aws_session_token"]
    )


# -----------------------------
# Main Rotation Logic
# -----------------------------
def main():
    print("[INFO] Assuming IAM role...")
    session = assume_role(ROLE_ARN, SESSION_NAME, EXTERNAL_ID)

    grafana = aws_client("grafana", session)
    secrets = aws_client("secretsmanager", session)

    now = datetime.datetime.utcnow()

    # Step 1: List existing tokens
    tokens = grafana.list_workspace_service_account_tokens(
        workspaceId=WORKSPACE_ID,
        serviceAccountId=SERVICE_ACCOUNT_ID
    ).get("serviceAccountTokens", [])

    # Step 2: Delete expired tokens
    expired_tokens = [
        t for t in tokens
        if t.get("expiresAt") and t["expiresAt"].replace(tzinfo=None) < now
    ]

    for token in expired_tokens:
        grafana.delete_workspace_service_account_token(
            workspaceId=WORKSPACE_ID,
            serviceAccountId=SERVICE_ACCOUNT_ID,
            tokenId=token["id"]
        )
        print(f"[INFO] Deleted expired token: {token['name']}")

    # Step 3: Create new token
    token_name = f"auto-rotated-{now.strftime('%Y%m%d%H%M%S%f')}"
    response = grafana.create_workspace_service_account_token(
        workspaceId=WORKSPACE_ID,
        serviceAccountId=SERVICE_ACCOUNT_ID,
        name=token_name,
        secondsToLive=EXPIRES_IN
    )

    new_token = response["serviceAccountToken"]["key"]
    print(f"[INFO] Created new token: {token_name}")

    # Step 4: Update Secrets Manager
    secrets.put_secret_value(
        SecretId=SECRET_NAME,
        SecretString=json.dumps({"grafana-api-token": new_token})
    )
    print(f"[INFO] Secrets Manager updated: {SECRET_NAME}")

    print("[SUCCESS] Token rotation completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)