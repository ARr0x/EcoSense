"""
storage_stack.py — EcoSense Storage Stack

Crée l'infrastructure de stockage et d'archivage :
  - S3 Bucket      : stockage partitionné year=/month=/day=/hour=
  - Kinesis Firehose : buffer et écriture en S3 (depuis IoT Core)
  - Athena          : table externe + partition projection pour requêtes SQL

Utilise LabRole pour les permissions Firehose → S3.

Produit :
  - self.bucket           : s3.Bucket (référence interne)
  - self.delivery_stream  : firehose.CfnDeliveryStream (passé à iot_core_stack)
"""

import aws_cdk as cdk
from aws_cdk import aws_athena as athena
from aws_cdk import aws_glue as glue
from aws_cdk import aws_kinesisfirehose as firehose
from aws_cdk import aws_s3 as s3
from constructs import Construct


class StorageStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        bucket_name: str,
        firehose_buffer_seconds: int = 60,
        firehose_buffer_mb: int = 5,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # LabRole — utilisé par Firehose pour écrire en S3
        lab_role_arn = f"arn:aws:iam::{self.account}:role/LabRole"

        # =====================================================================
        # S3 Bucket — archivage partitionné
        # =====================================================================
        self.bucket = s3.Bucket(
            self,
            "ArchiveBucket",
            bucket_name=bucket_name,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,  # ne pas supprimer sur cdk destroy
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireOldData",
                    enabled=True,
                    expiration=cdk.Duration.days(30),  # garder 30 jours
                )
            ],
        )

        # =====================================================================
        # Kinesis Firehose — buffer et écriture en S3
        # Partitionnement : raw/year=YYYY/month=MM/day=DD/hour=HH/
        # =====================================================================
        self.delivery_stream = firehose.CfnDeliveryStream(
            self,
            "DeliveryStream",
            delivery_stream_name=f"ecosense-delivery-{self.region}",
            delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=self.bucket.bucket_arn,
                role_arn=lab_role_arn,
                prefix="raw/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/",
                error_output_prefix="errors/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/!{firehose:error-output-type}/",
                buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                    interval_in_seconds=firehose_buffer_seconds,
                    size_in_m_bs=firehose_buffer_mb,
                ),
                compression_format="UNCOMPRESSED",  # JSON lisible directement
                cloud_watch_logging_options=firehose.CfnDeliveryStream.CloudWatchLoggingOptionsProperty(
                    enabled=True,
                    log_group_name=f"/ecosense/firehose/{self.region}",
                    log_stream_name="S3Delivery",
                ),
            ),
        )

        # =====================================================================
        # Glue Database — nécessaire pour Athena
        # =====================================================================
        glue_database = glue.CfnDatabase(
            self,
            "GlueDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name="ecosense_db",
                description="EcoSense IoT telemetry database",
            ),
        )

        # =====================================================================
        # Glue Table externe — partition projection (pas de crawler)
        # Schema aligné avec le payload du simulateur
        # =====================================================================
        glue_table = glue.CfnTable(
            self,
            "GlueTable",
            catalog_id=self.account,
            database_name="ecosense_db",
            table_input=glue.CfnTable.TableInputProperty(
                name="telemetry",
                description="EcoSense IoT telemetry — partitioned by year/month/day/hour",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "json",
                    "projection.enabled": "true",
                    # Partition projection — pas besoin de MSCK REPAIR
                    "projection.year.type": "integer",
                    "projection.year.range": "2026,2030",
                    "projection.month.type": "integer",
                    "projection.month.range": "1,12",
                    "projection.month.digits": "2",
                    "projection.day.type": "integer",
                    "projection.day.range": "1,31",
                    "projection.day.digits": "2",
                    "projection.hour.type": "integer",
                    "projection.hour.range": "0,23",
                    "projection.hour.digits": "2",
                    "storage.location.template": (
                        f"s3://{bucket_name}/raw/"
                        "year=${year}/month=${month}/day=${day}/hour=${hour}/"
                    ),
                },
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name="year", type="int"),
                    glue.CfnTable.ColumnProperty(name="month", type="int"),
                    glue.CfnTable.ColumnProperty(name="day", type="int"),
                    glue.CfnTable.ColumnProperty(name="hour", type="int"),
                ],
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=f"s3://{bucket_name}/raw/",
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.openx.data.jsonserde.JsonSerDe",
                        parameters={"ignore.malformed.json": "true"},
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name="sensor_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="metric", type="string"),
                        glue.CfnTable.ColumnProperty(name="value", type="double"),
                        glue.CfnTable.ColumnProperty(name="unit", type="string"),
                        glue.CfnTable.ColumnProperty(name="status", type="string"),
                        glue.CfnTable.ColumnProperty(name="region", type="string"),
                        glue.CfnTable.ColumnProperty(name="timestamp", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="ingested_at", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="quartier", type="string"),
                        glue.CfnTable.ColumnProperty(
                            name="sensor_id_topic", type="string"
                        ),
                    ],
                ),
            ),
        )

        # Glue table dépend de la database
        glue_table.add_dependency(glue_database)

        # =====================================================================
        # Athena Workgroup — résultats dans S3
        # =====================================================================
        athena.CfnWorkGroup(
            self,
            "AthenaWorkgroup",
            name=f"ecosense-{self.region}",
            description="EcoSense Athena workgroup",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{bucket_name}/athena-results/",
                ),
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=False,
            ),
        )

        # =====================================================================
        # Outputs
        # =====================================================================
        cdk.CfnOutput(
            self,
            "BucketName",
            value=self.bucket.bucket_name,
            description="Nom du bucket S3",
            export_name=f"EcoSenseBucketName-{self.region}",
        )

        cdk.CfnOutput(
            self,
            "BucketArn",
            value=self.bucket.bucket_arn,
            description="ARN du bucket S3",
        )

        cdk.CfnOutput(
            self,
            "FirehoseName",
            value=self.delivery_stream.ref,
            description="Nom du Firehose delivery stream",
            export_name=f"EcoSenseFirehoseName-{self.region}",
        )

        cdk.CfnOutput(
            self,
            "AthenaQuery",
            value=(
                "SELECT sensor_id, metric, value, status, "
                "FROM_UNIXTIME(timestamp) AS ts "
                "FROM ecosense_db.telemetry "
                "WHERE year=2026 AND month=6 AND day=1 "
                "LIMIT 100;"
            ),
            description="Exemple de requête Athena",
        )
