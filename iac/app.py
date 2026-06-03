#!/usr/bin/env python3
"""
app.py — EcoSense CDK Orchestrateur

Déploie l'infrastructure sur deux régions :
  - us-east-1 (Primary)
  - us-east-2 (Secondary)

Stacks par région :
  1. SnsStack     — SNS topic alertes CRITICAL + abonnés Email/SMS
  2. StorageStack — Kinesis Firehose + S3 + Athena (archivage ALL)
  3. IoTCoreStack — Topic Rules SQL (reçoit SNS ARN + Firehose ARN)

Flux :
  IoT Core
    ├─ CRITICAL → SNS → Email/SMS
    └─ ALL      → Firehose → S3 → Athena
"""

import os

import aws_cdk as cdk
from dotenv import find_dotenv, load_dotenv
from stacks.ecosense_stack import EcoSenseStack

env_path = find_dotenv(usecwd=True)
if env_path:
    load_dotenv(env_path)

AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID")
if not AWS_ACCOUNT_ID:
    raise ValueError(
        "AWS_ACCOUNT_ID manquant dans .env\n"
        "   Récupérer depuis la console AWS (en haut à droite)"
    )

PRIMARY_REGION = os.environ.get("CDK_DEFAULT_REGION", "us-east-1")
SECONDARY_REGION = os.environ.get("SECONDARY_REGION", "us-east-2")
MULTI_REGION = os.environ.get("MULTI_REGION", "true").lower() == "true"

ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")

S3_BUCKET_PREFIX = os.environ.get("S3_BUCKET_PREFIX", "ecosense-archives")
FIREHOSE_BUFFER_SEC = int(os.environ.get("FIREHOSE_BUFFER_SECONDS", "60"))
FIREHOSE_BUFFER_MB = int(os.environ.get("FIREHOSE_BUFFER_MB", "5"))

# 300 = 5 min prod, 20 = démo
FIRST_INTERVAL_SEC = int(os.environ.get("FIRST_INTERVAL_SEC", "300"))


app = cdk.App()


def create_stack(region: str, label: str) -> EcoSenseStack:
    # Bucket custom pour éviter le blocage voc-cancel-cred sur cdk-hnb659fds-*
    assets_bucket = f"ecosense-cdk-{AWS_ACCOUNT_ID}-{region}"
    return EcoSenseStack(
        app,
        f"EcoSense-{label}",
        alert_email=ALERT_EMAIL,
        bucket_name=f"{S3_BUCKET_PREFIX}-{AWS_ACCOUNT_ID}-{region}",
        firehose_buffer_seconds=FIREHOSE_BUFFER_SEC,
        firehose_buffer_mb=FIREHOSE_BUFFER_MB,
        first_interval_sec=FIRST_INTERVAL_SEC,
        env=cdk.Environment(account=AWS_ACCOUNT_ID, region=region),
        synthesizer=cdk.CliCredentialsStackSynthesizer(
            file_assets_bucket_name=assets_bucket,
        ),
    )


primary = create_stack(PRIMARY_REGION, "Primary")
secondary = create_stack(SECONDARY_REGION, "Secondary") if MULTI_REGION else None

for stack in filter(None, [primary, secondary]):
    cdk.Tags.of(stack).add("Project", "EcoSense")
    cdk.Tags.of(stack).add("ManagedBy", "CDK")
    cdk.Tags.of(stack).add("Environment", "Hackathon")

app.synth()
