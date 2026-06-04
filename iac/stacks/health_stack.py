"""
health_stack.py — EcoSense Health Check Stack

Expose un endpoint HTTPS par région permettant à Route 53 de surveiller
la disponibilité de la région via un health check HTTP.

Produit :
  - self.health_url : str (Function URL HTTPS, aucune auth)
"""

import os

import aws_cdk as cdk
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class HealthStack(Construct):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        stack = cdk.Stack.of(self)
        lab_role = iam.Role.from_role_arn(
            self, "LabRole",
            f"arn:aws:iam::{stack.account}:role/LabRole",
            mutable=False,
        )

        health_fn = lambda_.Function(
            self,
            "HealthFn",
            function_name=f"ecosense-health-{stack.region}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(os.path.join(_REPO_ROOT, "lambdas", "health")),
            role=lab_role,
            timeout=cdk.Duration.seconds(10),
            memory_size=128,
        )

        fn_url = health_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
        )

        self.health_url = fn_url.url

        # Alarme CloudWatch : déclenche si la Lambda health renvoie des erreurs
        # (crash, timeout, throttle). treat_missing_data=NOT_BREACHING évite les
        # faux positifs quand la Lambda n'est pas appelée (simulateur arrêté).
        cloudwatch.Alarm(
            self,
            "HealthFnErrorAlarm",
            alarm_name=f"ecosense-health-errors-{stack.region}",
            alarm_description=f"Lambda ecosense-health-{stack.region} en erreur",
            metric=cloudwatch.Metric(
                namespace="AWS/Lambda",
                metric_name="Errors",
                dimensions_map={"FunctionName": health_fn.function_name},
                statistic="Sum",
                period=cdk.Duration.minutes(1),
            ),
            threshold=1,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        cdk.CfnOutput(
            self,
            "HealthCheckUrl",
            value=fn_url.url,
            description=f"URL health check Route 53 — région {stack.region}",
        )
