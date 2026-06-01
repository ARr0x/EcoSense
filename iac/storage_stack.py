"""
EcoSense — Storage Stack
=========================
Crée les deux buckets S3 (principal + backup) dans leurs régions respectives
et configure la réplication Cross-Region (CRR) entre eux.

Appelé depuis app.py :
    StorageStack(app, "EcoSens-Storage",
        primary_region="us-east-1",
        backup_region="us-west-2",
        env=cdk.Environment(account=ACCOUNT, region="us-east-1"),
    )
    Bien deployer le bakcup en premier.
cdk deploy EcoSens-Backup   # us-west-2 d'abord
cdk deploy EcoSens-Storage  # us-east-1 ensuite avec CRR
"""

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
    aws_iam as iam,
)
from constructs import Construct


class StorageStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        primary_region: str,
        backup_region: str,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        # ── Noms des buckets ───────────────────────────────────────
        primary_bucket_name = f"ecosens-archive-primary-{primary_region}"
        backup_bucket_name  = f"ecosens-archive-backup-{backup_region}"

        # ══════════════════════════════════════════════════════════
        # Bucket principal (primary_region)
        # ══════════════════════════════════════════════════════════
        self.primary_bucket = s3.Bucket(
            self, "PrimaryBucket",
            bucket_name=primary_bucket_name,

            # Obligatoire pour la CRR
            versioned=True,

            # Sécurité
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,

            # Nettoyage facile en TP
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ══════════════════════════════════════════════════════════
        # Bucket backup (backup_region) — via CfnBucket car cross-region
        # Le bucket backup doit être dans une autre région que cette stack,
        # on le référence via son ARN pour la config CRR.
        # À déployer séparément dans app.py avec BackupBucketStack.
        # ══════════════════════════════════════════════════════════
        backup_bucket_arn = f"arn:aws:s3:::{backup_bucket_name}"

        # ══════════════════════════════════════════════════════════
        # Réplication CRR via CfnBucket (L1) car L2 ne supporte
        # pas la CRR cross-region nativement avec LabRole
        # ══════════════════════════════════════════════════════════
        lab_role_arn = (
            f"arn:aws:iam::{self.account}:role/LabRole"
        )

        cfn_bucket = self.primary_bucket.node.default_child

        cfn_bucket.replication_configuration = (
            s3.CfnBucket.ReplicationConfigurationProperty(
                role=lab_role_arn,
                rules=[
                    s3.CfnBucket.ReplicationRuleProperty(
                        id="EcoSensReplicationRule",
                        status="Enabled",
                        filter=s3.CfnBucket.ReplicationRuleFilterProperty(
                            prefix=""  # Réplique tout le bucket
                        ),
                        destination=s3.CfnBucket.ReplicationDestinationProperty(
                            bucket=backup_bucket_arn,
                            storage_class="STANDARD",
                        ),
                        delete_marker_replication=s3.CfnBucket.DeleteMarkerReplicationProperty(
                            status="Disabled"
                        ),
                    )
                ],
            )
        )

        # ══════════════════════════════════════════════════════════
        # Outputs CloudFormation
        # ══════════════════════════════════════════════════════════
        CfnOutput(self, "PrimaryBucketName",
            value=self.primary_bucket.bucket_name,
            description="Bucket S3 principal",
            export_name="EcoSens-PrimaryBucketName",
        )

        CfnOutput(self, "PrimaryBucketArn",
            value=self.primary_bucket.bucket_arn,
            description="ARN du bucket S3 principal",
            export_name="EcoSens-PrimaryBucketArn",
        )

        CfnOutput(self, "BackupBucketArn",
            value=backup_bucket_arn,
            description="ARN du bucket S3 backup (us-west-2)",
            export_name="EcoSens-BackupBucketArn",
        )


# ══════════════════════════════════════════════════════════════════
# Stack séparée pour le bucket backup — déployée en us-west-2
# ══════════════════════════════════════════════════════════════════

class BackupBucketStack(Stack):
    """
    Déploie uniquement le bucket backup en us-west-2.
    Doit être déployé AVANT StorageStack pour que la CRR puisse pointer dessus.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        backup_region: str,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        backup_bucket_name = f"ecosens-archive-backup-{backup_region}"

        self.backup_bucket = s3.Bucket(
            self, "BackupBucket",
            bucket_name=backup_bucket_name,

            # Obligatoire pour recevoir la CRR
            versioned=True,

            # Sécurité
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,

            # Nettoyage facile en TP
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        CfnOutput(self, "BackupBucketName",
            value=self.backup_bucket.bucket_name,
            description="Bucket S3 backup",
            export_name="EcoSens-BackupBucketName",
        )