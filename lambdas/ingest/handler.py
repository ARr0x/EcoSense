"""
handler.py — Lambda Ingest (déclenchée par SQS)

Rôle :
  1. Stocke chaque alerte CRITICAL dans DynamoDB (PendingAlerts)
  2. Si le quartier n'a pas d'état actif → envoie un mail immédiat via SNS
     et planifie un flush après FIRST_INTERVAL_SEC secondes
  3. Si un état existe déjà → rien (le flush périodique s'en charge)

Race condition : plusieurs Lambdas peuvent traiter le même quartier simultanément.
  → DynamoDB ConditionExpression garantit qu'un seul envoi de mail par quartier.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sns_client = boto3.client("sns")
scheduler_client = boto3.client("scheduler")

STATE_TABLE = os.environ["STATE_TABLE"]
ALERTS_TABLE = os.environ["ALERTS_TABLE"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
LAMBDA_FLUSH_ARN = os.environ["LAMBDA_FLUSH_ARN"]
SCHEDULER_ROLE_ARN = os.environ["SCHEDULER_ROLE_ARN"]
FIRST_INTERVAL_SEC = int(os.environ.get("FIRST_INTERVAL_SEC", "300"))


def handler(event, context):
    state_table = dynamodb.Table(STATE_TABLE)
    alerts_table = dynamodb.Table(ALERTS_TABLE)

    for record in event["Records"]:
        try:
            _traiter_alerte(record, state_table, alerts_table)
        except Exception:
            logger.exception("Erreur traitement alerte : %s", record.get("messageId"))
            raise


def _traiter_alerte(record, state_table, alerts_table):
    body = json.loads(record["body"])

    if body.get("status") != "CRITICAL":
        logger.warning("Message non-CRITICAL ignoré (status=%s)", body.get("status"))
        return

    quartier = body.get("quartier", "inconnu")
    now = datetime.now(timezone.utc)
    ts = now.isoformat()

    alerts_table.put_item(
        Item={
            "quartier": quartier,
            "alert_ts": ts,
            "payload": json.dumps(body),
            "ttl": int(now.timestamp()) + 86400,
        }
    )

    try:
        state_table.put_item(
            Item={
                "quartier": quartier,
                "last_sent_at": ts,
                "current_interval_sec": FIRST_INTERVAL_SEC,
                "schedule_name": "",
            },
            ConditionExpression="attribute_not_exists(quartier)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info("Quartier %s déjà en alerte, alerte uniquement buffurisée", quartier)
            return
        raise

    _envoyer_mail_immediat(quartier, body, now)

    next_time = now + timedelta(seconds=FIRST_INTERVAL_SEC)
    schedule_name = _creer_schedule(quartier, next_time)

    state_table.update_item(
        Key={"quartier": quartier},
        UpdateExpression="SET schedule_name = :s",
        ExpressionAttributeValues={":s": schedule_name},
    )
    logger.info("Quartier %s : premier mail envoyé, flush dans %ds", quartier, FIRST_INTERVAL_SEC)


def _envoyer_mail_immediat(quartier: str, payload: dict, now: datetime):
    capteur = payload.get("sensor_id", payload.get("sensor_id_topic", "inconnu"))
    heure = now.strftime("%H:%M:%S UTC")

    champs = [
        f"  {k}: {v}"
        for k, v in payload.items()
        if k not in ("quartier", "sensor_id", "sensor_id_topic", "status")
    ]
    details = "\n".join(champs)

    message = (
        f"ALERTE CRITICAL — {quartier.upper()}\n\n"
        f"Capteur  : {capteur}\n"
        f"Heure    : {heure}\n\n"
        f"Données  :\n{details}\n\n"
        "Les prochaines alertes de ce quartier seront regroupées."
    )

    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"[EcoSense] CRITICAL — {quartier.upper()}",
        Message=message,
    )


def _creer_schedule(quartier: str, fire_at: datetime) -> str:
    schedule_name = f"ecosense-flush-{quartier}-{int(fire_at.timestamp())}"
    expr = f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})"

    scheduler_client.create_schedule(
        Name=schedule_name,
        ScheduleExpression=expr,
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": LAMBDA_FLUSH_ARN,
            "RoleArn": SCHEDULER_ROLE_ARN,
            "Input": json.dumps({"quartier": quartier}),
        },
        ActionAfterCompletion="DELETE",
    )
    logger.info("Schedule créé : %s à %s", schedule_name, expr)
    return schedule_name
