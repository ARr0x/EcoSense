"""
EcoSense – SES Alert Stack
==========================
Architecture : IoT Core → SNS Topic → SES (Email)
Multi-région  : us-east-1 (primaire) | us-east-2 (failover/passive)

Notes :
  - SNS ne peut pas appeler SES directement via une action native.
    La solution standard AWS est : SNS → abonnement Email (SES sous le capot).
  - Pour un contrôle SES explicite (template, from: custom), on utilise
    SNS → Lambda → SES. Ici on reste sur SNS Email subscription (plus simple).
  - L'adresse 'alert_email' DOIT être vérifiée dans SES si le compte est
    encore en mode Sandbox.
"""

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subs,
    aws_ses as ses,
    aws_iam as iam,
)
from constructs import Construct


class SesAlertStack(Stack):
    """
    Stack SES/SNS déployable en us-east-1 (primaire) ou us-east-2 (failover).

    Contexte CDK attendu (cdk.json ou --context) :
        alert_email     : adresse destinataire des alertes  (obligatoire)
        sender_email    : adresse expéditrice vérifiée SES  (obligatoire)
        is_primary      : "true" | "false"                  (défaut : "true")

    Exports CloudFormation :
        EcoSense-SnsTopicArn-<region>
        EcoSense-SesIdentityArn-<region>
    """

    def __init__(self, scope: "Construct", construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        region      = self.region
        is_primary  = self.node.try_get_context("is_primary") or "true"
        alert_email = self.node.try_get_context("alert_email")
        sender_email = self.node.try_get_context("sender_email")

        if not alert_email:
            raise ValueError(
                "Contexte CDK 'alert_email' obligatoire.\n"
                "Ex : cdk deploy --context alert_email=ops@ecosense.io"
            )
        if not sender_email:
            raise ValueError(
                "Contexte CDK 'sender_email' obligatoire.\n"
                "Ex : cdk deploy --context sender_email=no-reply@ecosense.io"
            )

        # ------------------------------------------------------------------ #
        # 1. Identité SES – vérification de l'adresse expéditrice            #
        # ------------------------------------------------------------------ #
        # Enregistre l'adresse dans SES et déclenche l'email de vérification.
        # En production : préférer une identité de domaine (ses.EmailIdentity
        # avec un domaine Route53 pour validation DKIM automatique).
        ses_identity = ses.EmailIdentity(
            self,
            "SenderIdentity",
            identity=ses.Identity.email(sender_email),
        )

        # ------------------------------------------------------------------ #
        # 2. Topic SNS – point d'entrée des alertes IoT                      #
        # ------------------------------------------------------------------ #
        alert_topic = sns.Topic(
            self,
            "EcoSenseAlertTopic",
            topic_name=f"ecosense-alert-topic-{region}",
            display_name="EcoSense IoT Alerts",
        )

        # ------------------------------------------------------------------ #
        # 3. Abonnement SNS → Email (SES achemine l'email)                   #
        # ------------------------------------------------------------------ #
        # SNS utilise SES en interne pour la livraison.
        # raw_message_delivery=False → SNS formate un corps lisible par email.
        alert_topic.add_subscription(
            sns_subs.EmailSubscription(
                alert_email,
                # json=False : le corps de l'email est en texte brut (plus lisible)
            )
        )

        # ------------------------------------------------------------------ #
        # 4. Policy SNS – autoriser IoT Core à publier                       #
        # ------------------------------------------------------------------ #
        alert_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowIoTCorePublish",
                principals=[iam.ServicePrincipal("iot.amazonaws.com")],
                actions=["sns:Publish"],
                resources=[alert_topic.topic_arn],
                conditions={
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:iot:{region}:{self.account}:*"
                    }
                },
            )
        )

        # ------------------------------------------------------------------ #
        # 5. Outputs CloudFormation                                           #
        # ------------------------------------------------------------------ #
        suffix = "Primary" if is_primary == "true" else "Failover"

        CfnOutput(
            self, f"SnsTopicArn{suffix}",
            export_name=f"EcoSense-SnsTopicArn-{region}",
            value=alert_topic.topic_arn,
            description="ARN du topic SNS à référencer dans IoT Core Topic Rule",
        )

        CfnOutput(
            self, f"SesIdentityArn{suffix}",
            export_name=f"EcoSense-SesIdentityArn-{region}",
            value=ses_identity.email_identity_arn,
            description="ARN de l'identité SES expéditrice",
        )

        # Attributs publics pour les stacks dépendantes (iot_core_stack…)
        self.alert_topic = alert_topic
        self.ses_identity = ses_identity
