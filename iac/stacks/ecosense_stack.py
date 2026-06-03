"""
ecosense_stack.py — EcoSense Stack principal

Stack parent qui orchestre les constructs par région :
  - SnsStack              : SNS topic alertes CRITICAL
  - StorageStack          : Kinesis Firehose + S3 + Athena
  - AlertAggregatorStack  : SQS + DynamoDB + Lambda Ingest/Flush (backoff par quartier)
  - IoTCoreStack          : Topic Rules SQL (CRITICAL → SQS, ALL → Firehose)
"""

import aws_cdk as cdk
from constructs import Construct

from stacks.alert_aggregator_stack import AlertAggregatorStack
from stacks.iot_core_stack import IoTCoreStack
from stacks.sns_stack import SnsStack
from stacks.storage_stack import StorageStack


class EcoSenseStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        alert_email: str = "",
        bucket_name: str = "",
        firehose_buffer_seconds: int = 60,
        firehose_buffer_mb: int = 5,
        first_interval_sec: int = 300,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        sns = SnsStack(self, "Sns", alert_email=alert_email)

        storage = StorageStack(
            self,
            "Storage",
            bucket_name=bucket_name,
            firehose_buffer_seconds=firehose_buffer_seconds,
            firehose_buffer_mb=firehose_buffer_mb,
        )

        aggregator = AlertAggregatorStack(
            self,
            "AlertAggregator",
            alert_topic_arn=sns.alert_topic.topic_arn,
            first_interval_sec=first_interval_sec,
        )

        iot = IoTCoreStack(
            self,
            "IoTCore",
            alert_queue_url=aggregator.alerts_queue.queue_url,
            delivery_stream_name=storage.delivery_stream.ref,
        )
