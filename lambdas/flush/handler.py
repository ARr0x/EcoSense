"""
handler.py — Lambda Flush (déclenchée par EventBridge Scheduler)

Rôle :
  1. Récupère toutes les alertes pendantes d'un quartier depuis last_sent_at
  2. Si alertes présentes → envoie un email groupé via SNS
       → double l'intervalle (cap 43200 sec = 12h)
       → planifie le prochain flush
  3. Si aucune alerte → reset complet (quartier calme, cycle repart à zéro)
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

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

    alertes_resp = alerts_table.query(
        KeyConditionExpression=Key("quartier").eq(quartier) & Key("alert_ts").gt(last_sent_at)
    )
    alertes = alertes_resp.get("Items", [])

    if not alertes:
        state_table.delete_item(Key={"quartier": quartier})
        logger.info("Quartier %s calme, état réinitialisé", quartier)
        return

    _envoyer_mail_groupe(quartier, alertes, current_interval, now)

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
    logger.info("Quartier %s : %d alertes envoyées, prochain flush dans %ds", quartier, len(alertes), next_interval)


def _duree_str(secondes: int) -> str:
    if secondes < 60:
        return f"{secondes} sec"
    if secondes < 3600:
        return f"{secondes // 60} min"
    return f"{secondes // 3600}h"


def _envoyer_mail_groupe(quartier: str, alertes: list, intervalle_sec: int, now: datetime):
    alertes_triees = sorted(alertes, key=lambda a: a["alert_ts"])
    nb = len(alertes_triees)
    heure = now.strftime("%H:%M UTC")

    lignes = []
    for a in alertes_triees:
        try:
            p = json.loads(a["payload"])
        except Exception:
            p = {}
        capteur = p.get("sensor_id", p.get("sensor_id_topic", "?"))
        ts_court = a["alert_ts"][11:19]
        champs = ", ".join(
            f"{k}={v}"
            for k, v in p.items()
            if k not in ("quartier", "sensor_id", "sensor_id_topic", "status")
        )
        lignes.append(f"  {ts_court} | Capteur {capteur} | {champs}")

    duree = _duree_str(intervalle_sec)
    prochaine = _duree_str(min(intervalle_sec * 2, INTERVAL_MAX_SEC))

    message = (
        f"RÉSUMÉ ALERTES CRITICAL — {quartier.upper()}\n\n"
        f"{nb} alerte(s) CRITICAL détectée(s) sur les dernières {duree} :\n\n"
        f"{chr(10).join(lignes)}\n\n"
        f"Heure du rapport : {heure}\n"
        f"Prochaine notification dans {prochaine} si les alertes persistent."
    )

    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"[EcoSense] {nb} alertes CRITICAL — {quartier} ({duree})",
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
    logger.info("Schedule créé : %s", schedule_name)
    return schedule_name
