from diagrams import Cluster, Diagram, Edge
from diagrams.aws.analytics import Athena, Glue, KinesisDataFirehose
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import EventbridgeScheduler
from diagrams.aws.integration import SimpleNotificationServiceSnsTopic as SNS
from diagrams.aws.integration import SimpleQueueServiceSqsQueue as SQS
from diagrams.aws.iot import IotCore, IotSimulator
from diagrams.aws.storage import SimpleStorageServiceS3Bucket as S3
from diagrams.onprem.client import User

graph_attr = {"pad": "0.6", "fontsize": "18"}

with Diagram(
    "EcoSense Pipeline",
    show=False,
    direction="LR",
    filename="ecosense_archi",
    graph_attr=graph_attr,
):
    sim = IotSimulator("Simulateur\nMQTT")
    abonnes = User("Email\nAbonnés")

    with Cluster("AWS — Primary (us-east-1)  ·  Failover (us-east-2)"):
        iot = IotCore("IoT Core\nTopic Rules SQL")

        with Cluster("Chemin Alertes  [status = CRITICAL]"):
            sqs = SQS("AlertsQueue")
            dlq = SQS("DLQ\n(14 jours)")

            with Cluster("Agrégateur — backoff exponentiel par quartier"):
                ing = Lambda("Ingest")
                pa = Dynamodb("PendingAlerts\n(TTL 24h)")
                qs = Dynamodb("QuartierState")
                eb = EventbridgeScheduler("Scheduler\nt+5min → ×2 → cap 12h")
                fls = Lambda("Flush")

            sns = SNS("SNS Topic")

        with Cluster("Chemin Archivage  [100% du flux]"):
            fh = KinesisDataFirehose("Firehose")
            s3 = S3("S3\nraw/year/month/day/hour")
            glue = Glue("Glue Table\nPartition Projection")
            ath = Athena("Athena")

    sim >> iot
    iot >> Edge(color="firebrick", label="CRITICAL") >> sqs
    iot >> Edge(color="steelblue", label="ALL") >> fh

    sqs >> Edge(style="dashed", color="gray", label="×3 échecs") >> dlq
    sqs >> ing
    ing >> [pa, qs]
    ing >> Edge(color="firebrick", label="1ère alerte\nimmédiate") >> sns
    ing >> eb
    eb >> fls
    fls >> [pa, qs]
    fls >> sns
    fls >> Edge(style="dashed", label="reschedule") >> eb
    sns >> abonnes

    fh >> s3 >> glue >> ath
