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

graph_attr = {
    "pad": "1.0",
    "fontsize": "17",
    "splines": "ortho",
    "nodesep": "0.9",
    "ranksep": "1.2",
    "bgcolor": "white",
}

node_attr = {
    "fontsize": "13",
    "width": "1.6",
}

with Diagram(
    "EcoSense — Pipeline IoT",
    show=False,
    direction="LR",
    filename="ecosense_archi",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    sim = IotSimulator("Simulateur\nMQTT (mTLS)")
    abonnes = User("Abonnés\n(thread Gmail)")

    with Cluster("AWS  ·  us-east-1 Primary  /  us-east-2 Failover"):
        iot = IotCore("IoT Core\nTopic Rules SQL")

        # ── Chemin Alertes ───────────────────────────────────────────────
        with Cluster("① Chemin Alertes  [WHERE status = CRITICAL]"):

            sqs = SQS("AlertsQueue\nbatch=10  window=5s")

            with Cluster("Agrégateur par quartier — backoff exponentiel"):

                with Cluster("Traitement"):
                    ing = Lambda("Lambda Ingest\ntimeout 60s")

                with Cluster("État (DynamoDB)"):
                    pa = Dynamodb("PendingAlerts\nTTL 24h")
                    qs = Dynamodb("QuartierState\nlast_sent_at · interval")

                with Cluster("Planification"):
                    eb = EventbridgeScheduler("EventBridge Scheduler\nT+5min → ×2 → cap 12h")
                    fls = Lambda("Lambda Flush\ntimeout 60s")

            sns = SNS("SNS Topic")

            with Cluster("Résilience"):
                dlq_msg = SQS("DLQ Messages\n×3 échecs · 14j")
                dlq_sched = SQS("DLQ Scheduler\néchec invocation · 14j")

        # ── Chemin Archivage ─────────────────────────────────────────────
        with Cluster("② Chemin Archivage  [100 % du flux]"):
            fh = KinesisDataFirehose("Firehose\nGZIP · buffer 60s / 5MB")
            s3 = S3("S3\nyear/month/day/hour")
            glue = Glue("Glue\nPartition Projection")
            ath = Athena("Athena")

    # ── Arêtes ───────────────────────────────────────────────────────────
    # Rouge   = chemin première alerte (mail immédiat + scheduler)
    # Orange  = chemin alertes suivantes (buffer uniquement)
    # Violet  = cycle Flush (reschedule + mail feed)
    # Bleu    = archivage
    # Gris    = chemins d'erreur / DLQ

    sim >> iot
    iot >> Edge(color="firebrick", style="bold", label="CRITICAL") >> sqs
    iot >> Edge(color="steelblue", style="bold", label="ALL") >> fh

    # Ingest — commun aux deux cas
    sqs >> Edge(style="dashed", color="gray", label="×3 échecs") >> dlq_msg
    sqs >> ing

    # Cas 1 — Première alerte : rouge
    ing >> Edge(color="firebrick", style="bold",
                label="① 1ère alerte\nConditionExpr OK") >> qs
    ing >> Edge(color="firebrick", style="bold",
                label="mail immédiat") >> sns
    ing >> Edge(color="firebrick", style="bold",
                label="crée scheduler\nat(T+FIRST_INTERVAL)") >> eb

    # Cas 2 — Alertes suivantes : orange (buffer uniquement)
    ing >> Edge(color="darkorange", style="bold",
                label="② alertes suivantes\nbuffer uniquement") >> pa

    # Cycle Flush — violet
    eb >> fls
    eb >> Edge(style="dashed", color="gray", label="échec\ninvocation") >> dlq_sched
    fls >> Edge(color="mediumpurple", label="lit toutes\nles alertes") >> pa
    fls >> Edge(color="mediumpurple", label="met à jour\nlast_sent_at") >> qs
    fls >> Edge(color="mediumpurple", style="bold",
                label="feed NOUVELLES\n+ HISTORIQUE") >> sns
    fls >> Edge(color="mediumpurple", style="dashed",
                label="reschedule\n×2 interval") >> eb

    # SNS → abonnés
    sns >> abonnes

    # Archivage
    fh >> s3 >> glue >> ath
