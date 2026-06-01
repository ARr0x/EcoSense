"""
sns_stack.py — EcoSense SNS Stack

Crée le SNS Topic pour les alertes CRITICAL et ses abonnés Email/SMS.
Utilisé par iot_core_stack comme destination de la Topic Rule CRITICAL.

Produit :
  - self.alert_topic : sns.Topic (passé à iot_core_stack)
"""

import aws_cdk as cdk
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct


class SnsStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        alert_email: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # LabRole ARN — utilisé pour la policy SNS
        lab_role_arn = f"arn:aws:iam::{self.account}:role/LabRole"

        # =====================================================================
        # SNS Topic — alertes CRITICAL
        # =====================================================================
        self.alert_topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name=f"ecosense-alert-{self.region}",
            display_name=f"EcoSense CRITICAL Alerts ({self.region})",
        )

        # =====================================================================
        # Abonnés
        # =====================================================================

        # Email (si renseigné dans .env)
        if alert_email:
            self.alert_topic.add_subscription(
                subscriptions.EmailSubscription(alert_email)
            )
            cdk.CfnOutput(
                self,
                "AlertEmail",
                value=alert_email,
                description="Email abonné aux alertes CRITICAL",
            )

        # =====================================================================
        # Policy SNS — autorise IoT Core (via LabRole) à publier
        # =====================================================================
        self.alert_topic.add_to_resource_policy(
            cdk.aws_iam.PolicyStatement(
                sid="AllowIoTCorePublish",
                effect=cdk.aws_iam.Effect.ALLOW,
                principals=[
                    cdk.aws_iam.ArnPrincipal(lab_role_arn),
                ],
                actions=["sns:Publish"],
                resources=[self.alert_topic.topic_arn],
            )
        )

        # =====================================================================
        # Outputs
        # =====================================================================
        cdk.CfnOutput(
            self,
            "AlertTopicArn",
            value=self.alert_topic.topic_arn,
            description="ARN du SNS Topic alertes CRITICAL",
            export_name=f"EcoSenseAlertTopicArn-{self.region}",
        )

        cdk.CfnOutput(
            self,
            "AlertTopicName",
            value=self.alert_topic.topic_name,
            description="Nom du SNS Topic",
        )
