"""
alert_aggregator_stack.py — EcoSense Agrégateur d'alertes

Agrège les alertes CRITICAL par quartier avec backoff exponentiel :
  - 1re alerte       → mail immédiat
  - 5 min persistant → mail groupé
  - 10 min           → mail groupé  (x2 jusqu'à 12h max)
  - ...              → cap à 720 min

Composants :
  - SQS AlertsQueue + DLQ
  - DynamoDB QuartierState  (état par quartier : interval, last_sent_at)
  - DynamoDB PendingAlerts  (buffer des alertes non envoyées, TTL 24h)
  - Lambda Ingest           (déclenché par SQS)
  - Lambda Flush            (déclenché par EventBridge Scheduler)

Produit :
  - self.alerts_queue : sqs.Queue (passé à IoTCoreStack)
"""

import os

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as lambda_events
from aws_cdk import aws_sqs as sqs
from constructs import Construct

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AlertAggregatorStack(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        alert_topic_arn: str,
        first_interval_sec: int = 300,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stack = cdk.Stack.of(self)
        lab_role = iam.Role.from_role_arn(
            self, "LabRole",
            f"arn:aws:iam::{stack.account}:role/LabRole",
            mutable=False,
        )

        # =====================================================================
        # SQS — file d'attente des alertes CRITICAL + DLQ
        # =====================================================================
        dlq = sqs.Queue(
            self,
            "AlertsDLQ",
            queue_name=f"ecosense-alerts-dlq-{stack.region}",
            retention_period=cdk.Duration.days(14),
        )

        self.alerts_queue = sqs.Queue(
            self,
            "AlertsQueue",
            queue_name=f"ecosense-alerts-{stack.region}",
            visibility_timeout=cdk.Duration.seconds(90),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=dlq,
            ),
        )

        # =====================================================================
        # DynamoDB — état par quartier
        # =====================================================================
        state_table = dynamodb.Table(
            self,
            "QuartierState",
            table_name=f"ecosense-quartier-state-{stack.region}",
            partition_key=dynamodb.Attribute(name="quartier", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # =====================================================================
        # DynamoDB — buffer des alertes pendantes (TTL 24h)
        # =====================================================================
        alerts_table = dynamodb.Table(
            self,
            "PendingAlerts",
            table_name=f"ecosense-pending-alerts-{stack.region}",
            partition_key=dynamodb.Attribute(name="quartier", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="alert_ts", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # DLQ pour les invocations EventBridge Scheduler → Lambda Flush échouées
        flush_scheduler_dlq = sqs.Queue(
            self,
            "FlushSchedulerDLQ",
            queue_name=f"ecosense-flush-scheduler-dlq-{stack.region}",
            retention_period=cdk.Duration.days(14),
        )

        # ARN de flush_fn construit depuis son nom — évite la dépendance circulaire
        # que créerait flush_fn.function_arn (CDK ajouterait un DependsOn implicite
        # IngestFn → FlushFn → SqsEventSource → IngestFn).
        flush_fn_name = f"ecosense-flush-{stack.region}"
        flush_fn_arn = stack.format_arn(
            service="lambda",
            resource="function",
            resource_name=flush_fn_name,
            arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME,
        )

        # =====================================================================
        # Lambda Flush — déclenchée par EventBridge Scheduler
        # =====================================================================
        flush_fn = lambda_.Function(
            self,
            "FlushFn",
            function_name=flush_fn_name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(os.path.join(_REPO_ROOT, "lambdas", "flush")),
            role=lab_role,
            timeout=cdk.Duration.seconds(60),
            memory_size=128,
            environment={
                "STATE_TABLE": state_table.table_name,
                "ALERTS_TABLE": alerts_table.table_name,
                "SNS_TOPIC_ARN": alert_topic_arn,
                "LAMBDA_FLUSH_ARN": flush_fn_arn,
                "SCHEDULER_ROLE_ARN": f"arn:aws:iam::{stack.account}:role/LabRole",
                "SCHEDULER_DLQ_ARN": flush_scheduler_dlq.queue_arn,
                "FIRST_INTERVAL_SEC": str(first_interval_sec),
            },
        )

        # =====================================================================
        # Lambda Ingest — déclenchée par SQS
        # =====================================================================
        ingest_fn = lambda_.Function(
            self,
            "IngestFn",
            function_name=f"ecosense-ingest-{stack.region}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(os.path.join(_REPO_ROOT, "lambdas", "ingest")),
            role=lab_role,
            timeout=cdk.Duration.seconds(60),
            memory_size=128,
            environment={
                "STATE_TABLE": state_table.table_name,
                "ALERTS_TABLE": alerts_table.table_name,
                "SNS_TOPIC_ARN": alert_topic_arn,
                "LAMBDA_FLUSH_ARN": flush_fn_arn,
                "SCHEDULER_ROLE_ARN": f"arn:aws:iam::{stack.account}:role/LabRole",
                "SCHEDULER_DLQ_ARN": flush_scheduler_dlq.queue_arn,
                "FIRST_INTERVAL_SEC": str(first_interval_sec),
            },
        )

        ingest_fn.add_event_source(
            lambda_events.SqsEventSource(
                self.alerts_queue,
                batch_size=10,
                max_batching_window=cdk.Duration.seconds(5),
            )
        )

        # =====================================================================
        # Outputs
        # =====================================================================
        cdk.CfnOutput(
            self,
            "AlertsQueueUrl",
            value=self.alerts_queue.queue_url,
            description="URL SQS queue alertes CRITICAL",
        )

        cdk.CfnOutput(
            self,
            "AlertsDLQUrl",
            value=dlq.queue_url,
            description="URL DLQ alertes (messages en erreur)",
        )

        cdk.CfnOutput(
            self,
            "IngestFnName",
            value=ingest_fn.function_name,
            description="Nom Lambda Ingest",
        )

        cdk.CfnOutput(
            self,
            "FlushFnName",
            value=flush_fn.function_name,
            description="Nom Lambda Flush",
        )

        cdk.CfnOutput(
            self,
            "FlushSchedulerDLQUrl",
            value=flush_scheduler_dlq.queue_url,
            description="URL DLQ invocations Scheduler→Flush échouées",
        )
