"""
handler.py — Lambda Flush (déclenchée par EventBridge Scheduler)

Rôle :
  1. Récupère toutes les alertes du quartier depuis DynamoDB
  2. Si de nouvelles alertes depuis le dernier envoi → envoie un email "feed"
       avec les nouvelles alertes + l'historique complet du quartier
       → double l'intervalle (cap 43200 sec = 12h)
       → planifie le prochain flush
  3. Si aucune nouvelle alerte → reset complet (quartier calme, cycle repart à zéro)
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
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
SCHEDULER_DLQ_ARN = os.environ.get("SCHEDULER_DLQ_ARN", "")

INTERVAL_MAX_SEC = 43200  # 12h


def handler(event, context):
    quartier = event.get("quartier")
    if not quartier:
        logger.error("Événement sans quartier : %s", event)
        return

    now = datetime.now(timezone.utc)
    state_table = dynamodb.Table(STATE_TABLE)
    alerts_table = dynamodb.Table(ALERTS_TABLE)

    state_resp = state_table.get_item(Key={"quartier": quartier})
    if "Item" not in state_resp:
        logger.info("Quartier %s : aucun état, flush ignoré", quartier)
        return

    state = state_resp["Item"]
    last_sent_at = state["last_sent_at"]
    current_interval = int(state["current_interval_sec"])

    toutes_resp = alerts_table.query(
        KeyConditionExpression=Key("quartier").eq(quartier)
    )
    toutes_alertes = sorted(toutes_resp.get("Items", []), key=lambda a: a["alert_ts"])
    nouvelles = [a for a in toutes_alertes if a["alert_ts"] > last_sent_at]

    if not nouvelles:
        state_table.delete_item(Key={"quartier": quartier})
        logger.info("Quartier %s calme, état réinitialisé", quartier)
        return

    _envoyer_mail_feed(quartier, nouvelles, toutes_alertes, current_interval, now)

    next_interval = min(current_interval * 2, INTERVAL_MAX_SEC)
    next_time = now + timedelta(seconds=next_interval)
    schedule_name = _creer_schedule(quartier, next_time)

    state_table.update_item(
        Key={"quartier": quartier},
        UpdateExpression="SET last_sent_at = :ts, current_interval_sec = :interval, schedule_name = :sched",
        ExpressionAttributeValues={
            ":ts": now.isoformat(),
            ":interval": next_interval,
            ":sched": schedule_name,
        },
    )
    logger.info(
        "Quartier %s : %d nouvelles alertes envoyées, prochain flush dans %ds",
        quartier, len(nouvelles), next_interval,
    )


def _duree_str(secondes: int) -> str:
    if secondes < 60:
        return f"{secondes} sec"
    if secondes < 3600:
        return f"{secondes // 60} min"
    return f"{secondes // 3600}h"


def _format_ligne(a: dict) -> str:
    try:
        p = json.loads(a["payload"])
    except Exception:
        p = {}
    capteur = p.get("sensor_id", p.get("sensor_id_topic", "?"))
    ts = a["alert_ts"][11:19]  # HH:MM:SS
    champs = ", ".join(
        f"{k}={v}"
        for k, v in p.items()
        if k not in ("quartier", "sensor_id", "sensor_id_topic", "status")
    )
    return f"  {ts} | Capteur {capteur} | {champs}"


def _envoyer_mail_feed(
    quartier: str,
    nouvelles: list,
    toutes: list,
    intervalle_sec: int,
    now: datetime,
):
    precedentes = [a for a in toutes if a not in nouvelles]
    heure = now.strftime("%H:%M UTC")
    prochaine = _duree_str(min(intervalle_sec * 2, INTERVAL_MAX_SEC))

    lignes = [f"=== NOUVELLES ({len(nouvelles)}) ==="]
    lignes += [_format_ligne(a) for a in nouvelles]

    if precedentes:
        lignes.append(f"\n=== HISTORIQUE ({len(precedentes)}) ===")
        lignes += [_format_ligne(a) for a in precedentes]

    lignes += [
        "",
        f"Rapport généré à {heure}",
        f"Prochaine notification dans {prochaine} si les alertes persistent.",
    ]

    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"[EcoSense] CRITICAL — {quartier.upper()}",
        Message="\n".join(lignes),
    )


def _creer_schedule(quartier: str, fire_at: datetime) -> str:
    schedule_name = f"ecosense-flush-{quartier}-{int(fire_at.timestamp() * 1000)}"
    expr = f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})"

    target = {
        "Arn": LAMBDA_FLUSH_ARN,
        "RoleArn": SCHEDULER_ROLE_ARN,
        "Input": json.dumps({"quartier": quartier}),
    }
    if SCHEDULER_DLQ_ARN:
        target["DeadLetterConfig"] = {"Arn": SCHEDULER_DLQ_ARN}

    try:
        scheduler_client.create_schedule(
            Name=schedule_name,
            ScheduleExpression=expr,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target=target,
            ActionAfterCompletion="DELETE",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            logger.info("Schedule %s déjà existant, réutilisé", schedule_name)
        else:
            raise

    logger.info("Schedule créé : %s", schedule_name)
    return schedule_name
