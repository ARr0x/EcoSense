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
from stacks.iot_core_stack import IoTCoreStack
from stacks.sns_stack import SnsStack
from stacks.storage_stack import StorageStack

# =============================================================================
# Charger .env depuis la racine du dépôt
# =============================================================================

env_path = find_dotenv(usecwd=True)
if env_path:
    load_dotenv(env_path)

# Variables obligatoires
AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID")
if not AWS_ACCOUNT_ID:
    raise ValueError(
        "❌ AWS_ACCOUNT_ID manquant dans .env\n"
        "   Récupérer depuis la console AWS (en haut à droite)"
    )

PRIMARY_REGION = os.environ.get("CDK_DEFAULT_REGION", "us-east-1")
SECONDARY_REGION = os.environ.get("SECONDARY_REGION", "us-east-2")

# Variables optionnelles pour SNS
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")  # ex: team@example.com

# Paramètres S3 / Firehose
S3_BUCKET_PREFIX = os.environ.get("S3_BUCKET_PREFIX", "ecosense-archives")
FIREHOSE_BUFFER_SEC = int(os.environ.get("FIREHOSE_BUFFER_SECONDS", "60"))
FIREHOSE_BUFFER_MB = int(os.environ.get("FIREHOSE_BUFFER_MB", "5"))


# =============================================================================
# App CDK
# =============================================================================

app = cdk.App()


def create_region_stacks(region: str, label: str) -> dict:
    """
    Instancie les 3 stacks pour une région donnée.
    Retourne un dict avec les références aux stacks pour les outputs.

    label : "Primary" ou "Secondary"
    """
    env = cdk.Environment(account=AWS_ACCOUNT_ID, region=region)

    # ------------------------------------------------------------------
    # 1. SnsStack — SNS topic + abonnés Email/SMS
    #    Pas de dépendances sur les autres stacks.
    #    Expose : sns_stack.alert_topic (objet SNS Topic)
    # ------------------------------------------------------------------
    sns_stack = SnsStack(
        app,
        f"Sns-{label}",
        alert_email=ALERT_EMAIL,
        env=env,
        synthesizer=cdk.CliCredentialsStackSynthesizer(),
    )

    # ------------------------------------------------------------------
    # 2. StorageStack — Firehose + S3 + Athena
    #    Pas de dépendances sur les autres stacks.
    #    Expose : storage_stack.firehose, storage_stack.bucket
    # ------------------------------------------------------------------
    storage_stack = StorageStack(
        app,
        f"Storage-{label}",
        bucket_name=f"{S3_BUCKET_PREFIX}-{AWS_ACCOUNT_ID}-{region}",
        firehose_buffer_seconds=FIREHOSE_BUFFER_SEC,
        firehose_buffer_mb=FIREHOSE_BUFFER_MB,
        env=env,
        synthesizer=cdk.CliCredentialsStackSynthesizer(),
    )

    # ------------------------------------------------------------------
    # 3. IoTCoreStack — Topic Rules SQL
    #    Dépend de SnsStack (ARN topic alert) et StorageStack (ARN Firehose).
    #    Reçoit les ARN en paramètre → pas de dépendance circulaire.
    # ------------------------------------------------------------------
    iot_stack = IoTCoreStack(
        app,
        f"IoTCore-{label}",
        alert_topic=sns_stack.alert_topic,  # ref objet SNS
        delivery_stream=storage_stack.delivery_stream,  # ref objet Firehose
        env=env,
        synthesizer=cdk.CliCredentialsStackSynthesizer(),
    )

    # IoTCore dépend explicitement des deux autres (ordre de déploiement)
    iot_stack.add_dependency(sns_stack)
    iot_stack.add_dependency(storage_stack)

    return {
        "sns": sns_stack,
        "storage": storage_stack,
        "iot": iot_stack,
    }


# =============================================================================
# Déploiement multi-région
# =============================================================================

primary = create_region_stacks(PRIMARY_REGION, "Primary")
secondary = create_region_stacks(SECONDARY_REGION, "Secondary")

# =============================================================================
# Tags globaux sur tous les stacks
# =============================================================================

for stack in [
    primary["sns"],
    primary["storage"],
    primary["iot"],
    secondary["sns"],
    secondary["storage"],
    secondary["iot"],
]:
    cdk.Tags.of(stack).add("Project", "EcoSense")
    cdk.Tags.of(stack).add("ManagedBy", "CDK")
    cdk.Tags.of(stack).add("Environment", "Hackathon")

# =============================================================================
# Synth
# =============================================================================

app.synth()
