"""
iot_core_stack.py — EcoSense IoT Core Stack

Crée les Topic Rules SQL qui routent les messages MQTT :
  - CRITICAL → SQS AlertsQueue → Lambda Ingest (agrégation par quartier)
  - ALL      → Kinesis Firehose → S3 → Athena

Reçoit en paramètre :
  - alert_queue_url  : str (depuis alert_aggregator_stack)
  - delivery_stream  : firehose.CfnDeliveryStream (depuis storage_stack)

Utilise LabRole pour les permissions IoT Core → SQS et IoT Core → Firehose.
"""

import aws_cdk as cdk
from aws_cdk import aws_iot as iot
from constructs import Construct


class IoTCoreStack(Construct):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        alert_queue_url: str,
        delivery_stream_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stack = cdk.Stack.of(self)
        lab_role_arn = f"arn:aws:iam::{stack.account}:role/LabRole"

        # =====================================================================
        # Topic Rule 1 — CRITICAL → SQS (agrégation par quartier via Lambda)
        # Filtre les messages avec status = 'CRITICAL'
        # topic(2) AS quartier extrait le quartier du topic MQTT
        # =====================================================================
        iot.CfnTopicRule(
            self,
            "AlertRule",
            rule_name="ecosense_alert_rule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                rule_disabled=False,
                aws_iot_sql_version="2016-03-23",
                sql="SELECT *, topic(2) AS quartier, topic(3) AS sensor_id_topic FROM 'metropole/+/+/telemetry' WHERE status = 'CRITICAL'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        sqs=iot.CfnTopicRule.SqsActionProperty(
                            queue_url=alert_queue_url,
                            role_arn=lab_role_arn,
                            use_base64=False,
                        )
                    )
                ],
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        log_group_name=f"/ecosense/iot/errors/alert/{stack.region}",
                        role_arn=lab_role_arn,
                    )
                ),
            ),
        )

        # =====================================================================
        # Topic Rule 2 — ALL → Firehose
        # 100% du flux, sans filtre
        # Écrit dans Kinesis Firehose → S3 → Athena
        # =====================================================================
        iot.CfnTopicRule(
            self,
            "ArchiveRule",
            rule_name="ecosense_archive_rule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                rule_disabled=False,
                aws_iot_sql_version="2016-03-23",
                sql="SELECT * FROM 'metropole/+/+/telemetry'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        firehose=iot.CfnTopicRule.FirehoseActionProperty(
                            delivery_stream_name=delivery_stream_name,
                            role_arn=lab_role_arn,
                            separator="\n",
                        )
                    )
                ],
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        log_group_name=f"/ecosense/iot/errors/archive/{stack.region}",
                        role_arn=lab_role_arn,
                    )
                ),
            ),
        )

        # =====================================================================
        # Outputs
        # =====================================================================
        cdk.CfnOutput(
            self,
            "AlertRuleName",
            value="ecosense_alert_rule",
            description="Nom de la Topic Rule CRITICAL → SQS",
        )

        cdk.CfnOutput(
            self,
            "ArchiveRuleName",
            value="ecosense_archive_rule",
            description="Nom de la Topic Rule ALL → Firehose",
        )

        cdk.CfnOutput(
            self,
            "MqttTopic",
            value="metropole/+/+/telemetry",
            description="Topic MQTT attendu par les Topic Rules",
        )
