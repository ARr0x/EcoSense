#!/usr/bin/env python3
"""
simulator_mqtt.py — EcoSense IoT MQTT Simulator

Génère et publie des télémétries de capteurs vers AWS IoT Core via MQTT mTLS.
Lit la configuration depuis .env à la racine du dépôt.
Supporte le failover régional automatique us-east-1 ↔ us-west-2.
"""

import argparse
import json
import logging
import os
import random
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import boto3
import paho.mqtt.client as mqtt
from dotenv import find_dotenv, load_dotenv

log = logging.getLogger("ecosense-sim")

METRICS = {
    "CO2": {"unit": "ppm", "normal": (400, 1000), "critical_mult": 1.2},
    "NO2": {"unit": "µg/m³", "normal": (0, 200), "critical_mult": 1.2},
    "PM25": {"unit": "µg/m³", "normal": (0, 75), "critical_mult": 1.3},
    "NOISE": {"unit": "dB", "normal": (30, 85), "critical_mult": 1.15},
    "HUMIDITY": {"unit": "%", "normal": (20, 95), "critical_mult": 1.1},
}

QUARTIERS = ["centre", "nord", "sud", "est", "ouest"]


@dataclass
class Config:
    iot_endpoint_primary: str
    iot_endpoint_secondary: str
    primary_region: str
    secondary_region: str
    client_id: str
    sensor_count: int
    burst_size: int
    burst_interval: float
    critical_rate: float
    multi_region: bool = True
    forced_region: Optional[str] = None
    verbose: bool = False
    mqtt_debug: bool = False
    # Route 53 health check IDs (outputs du stack EcoSense-Route53)
    # Si vides, le polling Route 53 est désactivé (failover réactif uniquement).
    hc_id_primary: str = ""
    hc_id_secondary: str = ""
    health_poll_bursts: int = 30  # vérifier Route 53 toutes les N salves (~30s)

    @property
    def active_region(self) -> str:
        return self.forced_region or self.primary_region

    @property
    def active_endpoint(self) -> str:
        if self.active_region == self.primary_region:
            return self.iot_endpoint_primary
        return self.iot_endpoint_secondary

    @property
    def route53_enabled(self) -> bool:
        return self.multi_region and bool(self.hc_id_primary or self.hc_id_secondary)


# =============================================================================
# CONFIG
# =============================================================================


def _validated_rate(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        log.error("CRITICAL_RATE doit être entre 0.0 et 1.0 (valeur reçue : %s)", value)
        sys.exit(1)
    return value


def _validated_sensor_count(value: int) -> int:
    if not 1 <= value <= 10000:
        log.error("SENSOR_COUNT doit être entre 1 et 10000 (valeur reçue : %s)", value)
        sys.exit(1)
    return value


def get_repo_root() -> Path:
    env_path = find_dotenv(usecwd=True)
    return Path(env_path).parent if env_path else Path.cwd()


def load_config(args: argparse.Namespace) -> Config:
    env_path = find_dotenv(usecwd=True)
    if not env_path:
        log.error(".env introuvable à la racine du dépôt")
        log.error("   Copier .env.example vers .env et remplir les variables")
        sys.exit(1)

    load_dotenv(env_path)
    log.debug(f"Config chargée depuis {env_path}")

    primary_endpoint = os.getenv("IOT_ENDPOINT_PRIMARY")
    if not primary_endpoint:
        log.error("IOT_ENDPOINT_PRIMARY manquant dans .env")
        log.error("   Récupérer la valeur après : cdk deploy")
        sys.exit(1)

    return Config(
        iot_endpoint_primary=primary_endpoint,
        iot_endpoint_secondary=os.getenv("IOT_ENDPOINT_SECONDARY", ""),
        primary_region=os.getenv("CDK_DEFAULT_REGION", "us-east-1"),
        secondary_region=os.getenv("SECONDARY_REGION", "us-west-2"),
        client_id=os.getenv("MQTT_CLIENT_ID", "ecosense-simulator"),
        sensor_count=_validated_sensor_count(args.sensor_count or int(os.getenv("SENSOR_COUNT", "500"))),
        burst_size=args.burst_size or int(os.getenv("BURST_SIZE", "50")),
        burst_interval=args.burst_interval or float(os.getenv("BURST_INTERVAL", "1.0")),
        critical_rate=_validated_rate(args.critical_rate or float(os.getenv("CRITICAL_RATE", "0.1"))),
        multi_region=os.getenv("MULTI_REGION", "true").lower() == "true",
        forced_region=args.region,
        verbose=args.verbose,
        mqtt_debug=args.mqtt_debug,
        hc_id_primary=os.getenv("ROUTE53_HC_ID_PRIMARY", ""),
        hc_id_secondary=os.getenv("ROUTE53_HC_ID_SECONDARY", ""),
        health_poll_bursts=int(os.getenv("HEALTH_POLL_BURSTS", "30")),
    )


def setup_logging(verbose: bool, mqtt_debug: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    logging.getLogger("paho.mqtt").setLevel(
        logging.DEBUG if mqtt_debug else logging.WARNING
    )


# =============================================================================
# MQTT
# =============================================================================


def verify_certificates(config: Config) -> bool:
    repo_root = get_repo_root()
    cert_dir = repo_root / "simulator" / "certs" / config.active_region

    required = {
        "AmazonRootCA1.pem": "CA racine",
        "client.crt": "Certificat client",
        "private.key": "Clé privée",
    }

    all_found = True
    for filename, description in required.items():
        path = cert_dir / filename
        if path.exists():
            log.debug(f"{description} : {path}")
        else:
            log.error(f"{description} manquant : {path}")
            all_found = False

    if not all_found:
        log.error(f"   Fix: python simulator/provision_certs.py {config.active_region}")

    return all_found


def build_mqtt_client(
    config: Config, on_connect_cb=None, on_disconnect_cb=None
) -> mqtt.Client:
    repo_root = get_repo_root()
    cert_dir = repo_root / "simulator" / "certs" / config.active_region

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.client_id,
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )

    if on_connect_cb:
        client.on_connect = on_connect_cb
    if on_disconnect_cb:
        client.on_disconnect = on_disconnect_cb

    client.tls_set(
        ca_certs=str(cert_dir / "AmazonRootCA1.pem"),
        certfile=str(cert_dir / "client.crt"),
        keyfile=str(cert_dir / "private.key"),
        tls_version=ssl.PROTOCOL_TLSv1_2,
    )

    return client


def mqtt_error_message(rc: int) -> str:
    messages = {
        0: "Connexion réussie",
        1: "Protocol version refused",
        2: "Client ID rejected",
        3: "Server unavailable",
        4: "Bad credentials — vérifier que la IoT Policy est attachée au cert",
        5: "Not authorized — vérifier que client_id matche la policy",
    }
    return messages.get(rc, f"Unknown error rc={rc}")


def connect_mqtt(client: mqtt.Client, endpoint: str, timeout: int = 10) -> bool:
    """Connexion MQTT avec timeout. Retourne True si succès."""
    connected = {"ok": False}
    original_cb = client.on_connect

    def _wrapped(c, userdata, flags, reason_code, properties):
        connected["ok"] = reason_code.value == 0
        if original_cb:
            original_cb(c, userdata, flags, reason_code, properties)

    client.on_connect = _wrapped
    client.connect_async(endpoint, port=8883, keepalive=60)
    client.loop_start()

    for _ in range(timeout * 10):
        if connected["ok"]:
            return True
        time.sleep(0.1)

    return False


# =============================================================================
# PAYLOAD
# =============================================================================


def generate_payload(sensor_id: int, region: str, critical_rate: float) -> dict:
    metric_name = random.choice(list(METRICS.keys()))
    metric = METRICS[metric_name]
    low, high = metric["normal"]

    if random.random() < critical_rate:
        value = round(high * metric["critical_mult"] * random.uniform(1.0, 1.2), 2)
        status = "CRITICAL"
    else:
        value = round(random.uniform(low, high * 0.9), 2)
        status = "NORMAL"

    return {
        "sensor_id": f"S-{sensor_id:03d}",
        "metric": metric_name,
        "value": value,
        "unit": metric["unit"],
        "status": status,
        "region": region,
        "quartier": random.choice(QUARTIERS),
        "timestamp": int(time.time()),
    }


# =============================================================================
# ROUTE 53 HEALTH CHECK
# =============================================================================


def is_region_healthy(hc_id: str) -> bool:
    """Interroge Route 53 et retourne True si la région est healthy.

    Critère : moins de la moitié des health checkers rapportent un échec.
    Retourne True si hc_id est vide ou en cas d'erreur boto3 (fail-open).
    """
    if not hc_id:
        return True
    try:
        r53 = boto3.client("route53", region_name="us-east-1")
        inverted = r53.get_health_check(HealthCheckId=hc_id)[
            "HealthCheck"
        ]["HealthCheckConfig"].get("Inverted", False)
        resp = r53.get_health_check_status(HealthCheckId=hc_id)
        observations = resp.get("HealthCheckObservations", [])
        if not observations:
            return True
        failures = sum(
            1 for obs in observations
            if obs.get("StatusReport", {}).get("Status", "").startswith("Failure")
        )
        healthy = failures < len(observations) / 2
        if inverted:
            healthy = not healthy
        log.debug(
            f"[Route53] hc={hc_id[:8]}… {len(observations) - failures}/{len(observations)} OK"
            f"{' (INVERSÉ)' if inverted else ''}"
            f" → {'healthy' if healthy else 'UNHEALTHY'}"
        )
        return healthy
    except Exception as exc:
        log.debug(f"[Route53] Impossible de vérifier {hc_id[:8]}…: {exc}")
        return True  # fail-open : on ne bascule pas sur une erreur boto3


# =============================================================================
# COMMANDES
# =============================================================================


def cmd_check(config: Config) -> int:
    """Mode --check : teste la connexion MQTT et exit 0/1."""
    log.info("=" * 60)
    log.info("Mode CHECK — test de connexion")
    log.info(f"  Région    : {config.active_region}")
    log.info(f"  Endpoint  : {config.active_endpoint}")
    log.info(f"  Client ID : {config.client_id}")
    log.info("=" * 60)

    if not verify_certificates(config):
        return 1

    connection_state = {"rc": None}

    def on_connect(client, userdata, flags, reason_code, properties):
        connection_state["rc"] = reason_code.value
        if reason_code.value == 0:
            log.info("Connecté à IoT Core (rc=0)")
        else:
            log.error(f"Connexion refusée : {mqtt_error_message(reason_code.value)}")

    client = build_mqtt_client(config, on_connect_cb=on_connect)

    try:
        log.info(f"Connecting to {config.active_endpoint}:8883...")
        ok = connect_mqtt(client, config.active_endpoint)
        client.loop_stop()
        client.disconnect()

        if ok:
            log.info("TLS handshake OK — déconnexion propre")
            log.info("=" * 60)
            return 0
        else:
            if connection_state["rc"] is None:
                log.error("Timeout — IoT Core n'a pas répondu en 10s")
                log.error("   Vérifier IOT_ENDPOINT_PRIMARY dans .env")
            log.error("=" * 60)
            return 1

    except Exception as e:
        log.error(f"Exception : {e}")
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        return 1


def cmd_dry_run(config: Config) -> int:
    """Mode --dry-run : génère des payloads sans publier."""
    log.info("=" * 60)
    log.info("Mode DRY-RUN — génération sans publication")
    log.info(f"  Capteurs  : {config.sensor_count}")
    log.info(f"  Burst     : {config.burst_size} msgs / {config.burst_interval}s")
    log.info(f"  Critical  : {config.critical_rate * 100:.0f}%")
    log.info("=" * 60)

    burst_id = 0
    try:
        while True:
            burst_id += 1
            sensor_ids = random.sample(
                range(1, config.sensor_count + 1),
                min(config.burst_size, config.sensor_count),
            )
            critical_count = 0
            for sid in sensor_ids:
                payload = generate_payload(
                    sid, config.active_region, config.critical_rate
                )
                if payload["status"] == "CRITICAL":
                    critical_count += 1
                    log.warning(
                        f"[DRY][CRITICAL] {payload['sensor_id']} | "
                        f"{payload['metric']}={payload['value']}{payload['unit']}"
                    )
                else:
                    log.debug(
                        f"[DRY] {payload['sensor_id']} | "
                        f"{payload['metric']}={payload['value']}{payload['unit']}"
                    )

            log.info(
                f"Salve #{burst_id:03d} | "
                f"{config.burst_size} msgs générés | "
                f"CRITICAL={critical_count} | [DRY — rien publié]"
            )
            time.sleep(config.burst_interval)

    except KeyboardInterrupt:
        log.info(f"\nArrêt après {burst_id} salves")
        return 0


def cmd_run(config: Config) -> int:
    """Mode normal : génère et publie vers IoT Core."""
    log.info("=" * 60)
    log.info("Mode RUN — publication vers IoT Core")
    log.info(f"  Région    : {config.active_region}")
    log.info(f"  Endpoint  : {config.active_endpoint}")
    log.info(f"  Capteurs  : {config.sensor_count}")
    log.info(f"  Burst     : {config.burst_size} msgs / {config.burst_interval}s")
    log.info(f"  Critical  : {config.critical_rate * 100:.0f}%")
    log.info(f"  Route 53  : {'activé (poll /' + str(config.health_poll_bursts) + ' salves)' if config.route53_enabled else 'désactivé (ROUTE53_HC_ID_* non configurés)'}")
    log.info("=" * 60)

    if not verify_certificates(config):
        return 1

    # --- Sélection initiale de la région via Route 53 ---
    initial_region = config.active_region
    if config.route53_enabled:
        log.info("[Route53] Vérification de la santé des régions au démarrage...")
        primary_healthy = is_region_healthy(config.hc_id_primary)
        secondary_healthy = is_region_healthy(config.hc_id_secondary)
        log.info(f"[Route53] Primary ({config.primary_region}) : {'✓ healthy' if primary_healthy else '✗ UNHEALTHY'}")
        log.info(f"[Route53] Secondary ({config.secondary_region}) : {'✓ healthy' if secondary_healthy else '✗ UNHEALTHY'}")
        if not primary_healthy and secondary_healthy:
            log.warning(f"[Route53] Primaire indisponible → démarrage sur {config.secondary_region}")
            initial_region = config.secondary_region

    state = {
        "connected": False,
        "active_region": initial_region,
        "published": 0,
        "failed": 0,
    }

    def on_connect(client, userdata, flags, reason_code, properties):
        state["connected"] = reason_code.value == 0
        if reason_code.value == 0:
            log.info(f"Connecté à IoT Core ({state['active_region']})")
        else:
            log.error(f"Connexion refusée : {mqtt_error_message(reason_code.value)}")

    def on_disconnect(client, userdata, flags, reason_code, properties):
        state["connected"] = False
        if reason_code.value != 0:
            log.warning(f"Déconnexion inattendue (rc={reason_code.value})")

    client = build_mqtt_client(
        config, on_connect_cb=on_connect, on_disconnect_cb=on_disconnect
    )

    initial_endpoint = (
        config.iot_endpoint_secondary
        if initial_region == config.secondary_region
        else config.iot_endpoint_primary
    )
    log.info(f"Connexion à {initial_endpoint}:8883...")
    if not connect_mqtt(client, initial_endpoint):
        log.error("Impossible de se connecter")
        return 1

    burst_id = 0
    try:
        while True:
            burst_id += 1

            # --- Failover réactif : MQTT déconnecté ---
            if not state["connected"]:
                client.loop_stop()

                if config.multi_region:
                    other_region = (
                        config.secondary_region
                        if state["active_region"] == config.primary_region
                        else config.primary_region
                    )
                    other_endpoint = (
                        config.iot_endpoint_secondary
                        if other_region == config.secondary_region
                        else config.iot_endpoint_primary
                    )
                    log.error("Connexion perdue — basculement multi-région...")
                    log.warning(f"Basculement vers {other_region}")
                    state["active_region"] = other_region
                    config.forced_region = other_region
                    reconnect_endpoint = other_endpoint
                else:
                    log.error("Connexion perdue — reconnexion (multi-région désactivé)...")
                    reconnect_endpoint = config.iot_endpoint_primary

                client = build_mqtt_client(
                    config, on_connect_cb=on_connect, on_disconnect_cb=on_disconnect
                )
                if not connect_mqtt(client, reconnect_endpoint):
                    backoff = getattr(cmd_run, "_backoff", 1)
                    log.error("Reconnexion échouée — pause %ds", backoff)
                    time.sleep(backoff)
                    cmd_run._backoff = min(backoff * 2, 120)
                    continue
                cmd_run._backoff = 1

            # --- Failover proactif : polling Route 53 toutes les N salves ---
            if config.route53_enabled and burst_id % config.health_poll_bursts == 0:
                active_hc = (
                    config.hc_id_primary
                    if state["active_region"] == config.primary_region
                    else config.hc_id_secondary
                )
                if not is_region_healthy(active_hc):
                    log.warning(
                        f"[Route53] {state['active_region']} signalé UNHEALTHY"
                        f" — basculement proactif avant déconnexion MQTT"
                    )
                    client.loop_stop()
                    client.disconnect()
                    state["connected"] = False
                    continue

            sensor_ids = random.sample(
                range(1, config.sensor_count + 1),
                min(config.burst_size, config.sensor_count),
            )
            ok = err = critical = 0

            for sid in sensor_ids:
                payload = generate_payload(
                    sid, state["active_region"], config.critical_rate
                )
                topic = f"metropole/{payload['quartier']}/{payload['sensor_id']}/telemetry"
                log.info(f"→ PUBLISH topic: {topic}")

                result = client.publish(topic, json.dumps(payload), qos=1)

                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    ok += 1
                    state["published"] += 1
                    if payload["status"] == "CRITICAL":
                        critical += 1
                        log.warning(
                            f"CRITICAL | {payload['sensor_id']} | "
                            f"{payload['metric']}={payload['value']}{payload['unit']}"
                        )
                else:
                    err += 1
                    state["failed"] += 1

            log.info(
                f"Salve #{burst_id:03d} | "
                f"OK={ok} CRITICAL={critical} ERR={err} | "
                f"Région={state['active_region']} | "
                f"Total={state['published']}/{state['published'] + state['failed']}"
            )

            time.sleep(config.burst_interval)

    except KeyboardInterrupt:
        log.info(f"\nArrêt")
        log.info(f"  Publiés : {state['published']}")
        log.info(f"  Erreurs : {state['failed']}")
        client.loop_stop()
        client.disconnect()
        return 0


# =============================================================================
# MAIN
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="simulator_mqtt",
        description="EcoSense IoT MQTT Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python simulator/simulator_mqtt.py --check
  python simulator/simulator_mqtt.py --dry-run -v
  python simulator/simulator_mqtt.py --burst-size 10
  python simulator/simulator_mqtt.py --region us-west-2

Hiérarchie de configuration:
  CLI flags > .env > valeurs par défaut
        """,
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--check",
        action="store_true",
        help="Test connection only and exit (0=ok, 1=fail)",
    )
    mode_group.add_argument(
        "--dry-run", action="store_true", help="Generate payloads but don't publish"
    )

    parser.add_argument(
        "--region", choices=["us-east-1", "us-west-2"], help="Force a specific region"
    )
    parser.add_argument("--burst-size", type=int, help="Override BURST_SIZE from .env")
    parser.add_argument(
        "--burst-interval", type=float, help="Override BURST_INTERVAL from .env"
    )
    parser.add_argument(
        "--sensor-count", type=int, help="Override SENSOR_COUNT from .env"
    )
    parser.add_argument(
        "--critical-rate",
        type=float,
        help="Override CRITICAL_RATE from .env (0.0 to 1.0)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable DEBUG logging"
    )
    parser.add_argument(
        "--mqtt-debug", action="store_true", help="Enable paho-mqtt internal logging"
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose, args.mqtt_debug)
    config = load_config(args)

    try:
        if args.check:
            return cmd_check(config)
        elif args.dry_run:
            return cmd_dry_run(config)
        else:
            return cmd_run(config)
    except KeyboardInterrupt:
        log.info("\nInterrompu")
        return 130


if __name__ == "__main__":
    sys.exit(main())
