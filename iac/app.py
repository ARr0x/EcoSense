#!/usr/bin/env python3
"""
EcoSense – CDK App Entry Point
===============================
Déploie la SesAlertStack en us-east-1 (primaire) et us-east-2 (failover).

Usage :
    cdk deploy --all \
        --context alert_email=ops@ecosense.io \
        --context sender_email=no-reply@ecosense.io

    # Région seule :
    cdk deploy EcoSense-SES-Primary --context alert_email=... --context sender_email=...
"""

import aws_cdk as cdk
from stacks.ses_stack import SesAlertStack

app = cdk.App()

account = app.node.try_get_context("account") or None

# ── Région primaire ── us-east-1
SesAlertStack(
    app,
    "EcoSense-SES-Primary",
    env=cdk.Environment(account=account, region="us-east-1"),
    description="EcoSense – SNS/SES Alert (primaire us-east-1)",
)

# ── Région failover ── us-east-2  (active-passive)
SesAlertStack(
    app,
    "EcoSense-SES-Failover",
    env=cdk.Environment(account=account, region="us-east-2"),
    description="EcoSense – SNS/SES Alert (failover us-east-2)",
)

app.synth()
