"""
route53_stack.py — EcoSense Route 53 Health Checks

Crée deux Route 53 health checks HTTPS pointant vers les Lambda Function URLs
de chaque région. Permet de surveiller la disponibilité de l'infrastructure
et de déclencher un failover côté simulateur quand une région est unhealthy.

Déploiement séparé (après les stacks régionaux) :
  make deploy-route53

Paramètres (lus depuis l'environnement par app.py) :
  HEALTH_URL_PRIMARY   — Function URL Lambda us-east-1 (output EcoSense-Primary)
  HEALTH_URL_SECONDARY — Function URL Lambda us-west-2 (output EcoSense-Secondary)

Produit :
  - self.primary_hc_id   : str  (ID health check primaire)
  - self.secondary_hc_id : str  (ID health check secondaire)
"""

from urllib.parse import urlparse

import aws_cdk as cdk
from aws_cdk import aws_route53 as route53
from constructs import Construct


class Route53Stack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        primary_health_url: str,
        secondary_health_url: str,
        primary_region: str = "us-east-1",
        secondary_region: str = "us-west-2",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        fqdn_primary = urlparse(primary_health_url).netloc
        fqdn_secondary = urlparse(secondary_health_url).netloc

        # =====================================================================
        # Health check — région primaire
        # =====================================================================
        primary_hc = route53.CfnHealthCheck(
            self,
            "PrimaryHealthCheck",
            health_check_config=route53.CfnHealthCheck.HealthCheckConfigProperty(
                type="HTTPS",
                fully_qualified_domain_name=fqdn_primary,
                port=443,
                resource_path="/",
                request_interval=10,
                failure_threshold=1,
                enable_sni=True,
            ),
            health_check_tags=[
                route53.CfnHealthCheck.HealthCheckTagProperty(
                    key="Name", value=f"ecosense-primary-{primary_region}"
                ),
                route53.CfnHealthCheck.HealthCheckTagProperty(
                    key="Project", value="EcoSense"
                ),
            ],
        )

        # =====================================================================
        # Health check — région secondaire
        # =====================================================================
        secondary_hc = route53.CfnHealthCheck(
            self,
            "SecondaryHealthCheck",
            health_check_config=route53.CfnHealthCheck.HealthCheckConfigProperty(
                type="HTTPS",
                fully_qualified_domain_name=fqdn_secondary,
                port=443,
                resource_path="/",
                request_interval=10,
                failure_threshold=1,
                enable_sni=True,
            ),
            health_check_tags=[
                route53.CfnHealthCheck.HealthCheckTagProperty(
                    key="Name", value=f"ecosense-secondary-{secondary_region}"
                ),
                route53.CfnHealthCheck.HealthCheckTagProperty(
                    key="Project", value="EcoSense"
                ),
            ],
        )

        self.primary_hc_id = primary_hc.ref
        self.secondary_hc_id = secondary_hc.ref

        # =====================================================================
        # Outputs
        # =====================================================================
        cdk.CfnOutput(
            self,
            "PrimaryHealthCheckId",
            value=primary_hc.ref,
            description=f"ID health check Route 53 — {primary_region} (primaire)",
        )
        cdk.CfnOutput(
            self,
            "SecondaryHealthCheckId",
            value=secondary_hc.ref,
            description=f"ID health check Route 53 — {secondary_region} (secondaire)",
        )
        cdk.CfnOutput(
            self,
            "PrimaryFqdn",
            value=fqdn_primary,
            description=f"FQDN surveillé — {primary_region}",
        )
        cdk.CfnOutput(
            self,
            "SecondaryFqdn",
            value=fqdn_secondary,
            description=f"FQDN surveillé — {secondary_region}",
        )
