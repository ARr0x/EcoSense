#!/usr/bin/env python3
"""
provision_certs.py

Génère les certificats X.509 pour le simulateur MQTT EcoSense.
Lit la configuration depuis .env à la racine du dépôt.

Usage:
    python simulator/provision_certs.py us-east-1
    python simulator/provision_certs.py us-east-2 --force
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import find_dotenv, load_dotenv

AWS_IOT_ROOT_CA_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
POLICY_NAME_TEMPLATE = "EcoSenseSimulatorPolicy-{region}"
SUPPORTED_REGIONS = ["us-east-1", "us-east-2"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def load_env() -> dict:
    env_path = find_dotenv(usecwd=True)

    if not env_path:
        log.error("Fichier .env introuvable")
        log.error("   Copier .env.example vers .env et remplir les variables")
        sys.exit(1)

    load_dotenv(env_path)
    log.info(f"Configuration chargée depuis {env_path}")

    config = {
        "client_id": os.getenv("MQTT_CLIENT_ID", "ecosense-simulator"),
        "aws_account_id": os.getenv("AWS_ACCOUNT_ID", ""),
    }

    if not config["client_id"]:
        log.error("MQTT_CLIENT_ID manquant dans .env")
        sys.exit(1)

    return config


def get_repo_root() -> Path:
    env_path = find_dotenv(usecwd=True)
    return Path(env_path).parent if env_path else Path.cwd()


def create_directory(cert_dir: Path) -> None:
    cert_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Répertoire prêt : {cert_dir}")


def check_existing_cert(cert_dir: Path) -> bool:
    return (cert_dir / "client.crt").exists()


def download_ca_certificate(cert_dir: Path) -> None:
    ca_file = cert_dir / "AmazonRootCA1.pem"

    try:
        log.info("Téléchargement du CA racine AWS IoT...")
        response = requests.get(AWS_IOT_ROOT_CA_URL, timeout=10)
        response.raise_for_status()
        ca_file.write_text(response.text)
        log.info(f"CA téléchargé : {ca_file}")
    except requests.RequestException as e:
        log.error(f"Échec téléchargement CA : {e}")
        raise


def create_certificate_and_keys(iot_client) -> dict:
    try:
        log.info("Création du certificat X.509...")
        response = iot_client.create_keys_and_certificate(setAsActive=True)
        log.info(f"Certificat créé : {response['certificateId'][:16]}...")

        return {
            "certificate_id": response["certificateId"],
            "certificate_arn": response["certificateArn"],
            "certificate_pem": response["certificatePem"],
            "private_key": response["keyPair"]["PrivateKey"],
        }
    except ClientError as e:
        log.error(f"Échec création certificat : {e}")
        raise


def create_iot_policy(iot_client, region: str, client_id: str) -> str:
    policy_name = POLICY_NAME_TEMPLATE.format(region=region)

    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "iot:Connect",
                "Resource": f"arn:aws:iot:{region}:*:client/{client_id}",
            },
            {
                "Effect": "Allow",
                "Action": "iot:Publish",
                "Resource": f"arn:aws:iot:{region}:*:topic/metropole/*",
            },
            {
                "Effect": "Allow",
                "Action": "iot:Subscribe",
                "Resource": f"arn:aws:iot:{region}:*:topicfilter/$aws/certificates/describe/*",
            },
        ],
    }

    try:
        iot_client.create_policy(
            policyName=policy_name,
            policyDocument=json.dumps(policy_document),
        )
        log.info(f"Policy créée : {policy_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceAlreadyExistsException":
            log.warning(f"Policy {policy_name} existe déjà (skip création)")
        else:
            log.error(f"Échec création policy : {e}")
            raise

    return policy_name


def attach_policy_to_certificate(iot_client, policy_name: str, cert_arn: str) -> None:
    try:
        iot_client.attach_policy(policyName=policy_name, target=cert_arn)
        log.info("Policy attachée au certificat")
    except ClientError as e:
        log.error(f"Échec attachement : {e}")
        raise


def save_certificate_files(cert_dir: Path, cert_data: dict) -> None:
    cert_file = cert_dir / "client.crt"
    cert_file.write_text(cert_data["certificate_pem"])
    cert_file.chmod(0o644)
    log.info(f"Certificat : {cert_file} (mode 644)")

    key_file = cert_dir / "private.key"
    key_file.write_text(cert_data["private_key"])
    key_file.chmod(0o400)
    log.info(f"Clé privée : {key_file} (mode 400)")


def save_metadata(
    cert_dir: Path, cert_data: dict, policy_name: str, region: str, client_id: str
) -> None:
    metadata = {
        "certificate_id": cert_data["certificate_id"],
        "certificate_arn": cert_data["certificate_arn"],
        "region": region,
        "policy_name": policy_name,
        "client_id": client_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    metadata_file = cert_dir / "metadata.json"
    metadata_file.write_text(json.dumps(metadata, indent=2))
    log.info(f"Métadonnées : {metadata_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Génère les certificats X.509 pour le simulateur MQTT EcoSense",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python simulator/provision_certs.py us-east-1
  python simulator/provision_certs.py us-east-2 --force
  python simulator/provision_certs.py us-east-1 -v
        """,
    )
    parser.add_argument(
        "region",
        choices=SUPPORTED_REGIONS,
        help="Région AWS (us-east-1 ou us-east-2)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Créer un nouveau cert même si un existe déjà",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Logs DEBUG",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        config = load_env()

        repo_root = get_repo_root()
        cert_dir = repo_root / "simulator" / "certs" / args.region
        create_directory(cert_dir)

        if check_existing_cert(cert_dir) and not args.force:
            log.warning(f"Un certificat existe déjà dans {cert_dir}")
            log.warning("   Utilise --force pour en créer un nouveau")
            return 0

        try:
            iot_client = boto3.client("iot", region_name=args.region)
        except NoCredentialsError:
            log.error("Credentials AWS introuvables")
            log.error("   Vérifier ~/.aws/credentials ou variables d'env")
            return 1

        download_ca_certificate(cert_dir)
        cert_data = create_certificate_and_keys(iot_client)

        policy_name = create_iot_policy(iot_client, args.region, config["client_id"])
        attach_policy_to_certificate(
            iot_client, policy_name, cert_data["certificate_arn"]
        )

        save_certificate_files(cert_dir, cert_data)
        save_metadata(
            cert_dir, cert_data, policy_name, args.region, config["client_id"]
        )

        log.info("")
        log.info("=" * 60)
        log.info(f"Certificats générés pour {args.region}")
        log.info("=" * 60)
        log.info(f"  Cert ID   : {cert_data['certificate_id']}")
        log.info(f"  Policy    : {policy_name}")
        log.info(f"  Client ID : {config['client_id']}")
        log.info(f"  Dir       : {cert_dir}")
        log.info("")
        log.info("Prochaines étapes :")
        log.info("  → Générer pour l'autre région")
        log.info("  → Mettre à jour IOT_ENDPOINT_* dans .env après cdk deploy")
        log.info("  → Tester : python simulator/simulator_mqtt.py --check")
        log.info("")

        return 0

    except KeyboardInterrupt:
        log.info("\nInterrompu")
        return 130
    except Exception as e:
        log.error(f"Erreur fatale : {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
