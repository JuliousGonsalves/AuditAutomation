import boto3
import json
import datetime
import sys

# -----------------------------
# Configuration
# -----------------------------
WORKSPACE_ID = "g-e6010bcf26"
SERVICE_ACCOUNT_ID = "38"
SECRET_NAME = "grafana/service-account-token"
EXPIRES_IN = 30 * 24 * 60 * 60  # 30 days

grafana = boto3.client("grafana")
secrets = boto3.client("secretsmanager")


def main():
    now = datetime.datetime.utcnow()

    # -----------------------------
    # Step 1: List existing tokens
    # -----------------------------
    tokens = grafana.list_workspace_service_account_tokens(
        workspaceId=WORKSPACE_ID,
        serviceAccountId=SERVICE_ACCOUNT_ID
    ).get("serviceAccountTokens", [])

    # -----------------------------
    # Step 2: Delete only expired tokens
    # -----------------------------
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

    # -----------------------------
    # Step 3: Create new token
    # -----------------------------
    token_name = f"auto-rotated-{now.strftime('%Y%m%d%H%M%S%f')}"
    response = grafana.create_workspace_service_account_token(
        workspaceId=WORKSPACE_ID,
        serviceAccountId=SERVICE_ACCOUNT_ID,
        name=token_name,
        secondsToLive=EXPIRES_IN
    )

    new_token = response["serviceAccountToken"]["key"]
    print(f"[INFO] Created new token: {token_name}")

    # -----------------------------
    # Step 4: Update Secrets Manager
    # -----------------------------
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
