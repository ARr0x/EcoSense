# EcoSense

Pipeline de télémétrie IoT multi-région sur AWS. Les capteurs publient des mesures environnementales via MQTT/mTLS vers IoT Core ; les Topic Rules routent le flux en deux chemins : alertes CRITICAL vers une chaîne d'agrégation par quartier avec backoff exponentiel, et 100% du flux vers Kinesis Firehose pour archivage dans S3 et interrogation via Athena. Deux régions indépendantes (`us-east-1` / `us-east-2`) avec failover côté client.

Le `Makefile` est l'interface principale. Toutes les commandes s'exécutent depuis la racine du dépôt.

## Architecture

```mermaid
flowchart TD
     SIM[Simulateur MQTT] -->|metropole/quartier/sensor/telemetry| IOT[IoT Core]

     IOT -->|WHERE status = CRITICAL\ntopic2 AS quartier| SQS[SQS AlertsQueue]
     IOT -->|ALL messages| FH[Kinesis Firehose]
     SQS -->|messages échoués x3| DLQ[SQS DLQ]

     SQS --> INGEST[Lambda Ingest]

     INGEST -->|stocke alerte| PA[(DynamoDB\nPendingAlerts)]
     INGEST -->|write atomique\nConditionExpression| QS[(DynamoDB\nQuartierState)]
     INGEST -->|premier mail\npar quartier| SNS[SNS Topic]
     INGEST -->|at t+Ns| EB[EventBridge Scheduler]

     EB -->|quartier| FLUSH[Lambda Flush]
     FLUSH -->|lit alertes depuis\nlast_sent_at| PA
     FLUSH -->|lit état| QS

     FLUSH -->|alertes présentes\nmail groupé| SNS
     FLUSH -->|double interval\nx2 cap 12h| EB
     FLUSH -->|aucune alerte\nreset cycle| QS

     SNS -->|email| EMAIL[Destinataire]

     FH -->|partitionné YYYY-MM-DD-HH| S3[S3 Bucket]
     S3 --> ATH[Athena / Glue]

     style DLQ fill:#ff6b6b,color:#fff
     style FLUSH fill:#4ecdc4,color:#fff
     style INGEST fill:#4ecdc4,color:#fff
     style SNS fill:#f9ca24,color:#000
     style EB fill:#6c5ce7,color:#fff
```

## Prérequis

- Python 3.11+
- AWS CLI configuré avec des credentials valides
- Rôle IAM `LabRole` existant dans le compte (environnement AWS Academy)
- `.env` configuré depuis `.env.example`

## Quick start

Copier `.env.example` vers `.env` et renseigner `AWS_ACCOUNT_ID` et `ALERT_EMAIL` avant de commencer.

| Cible | Description |
|---|---|
| `make install` | Crée le venv et installe les dépendances Python |
| `make deploy` | Déploie les stacks CDK (Primary + Secondary si `MULTI_REGION=true`) |
| `make iot-endpoint` | Affiche les endpoints IoT à copier dans `.env` |
| `make certs` | Provisionne les certificats X.509 pour le simulateur |
| `make check` | Teste la connexion MQTT mTLS (exit 0 = OK) |
| `make run` | Lance le simulateur en mode publication |

Consulter `make help` pour la liste complète des cibles disponibles.

## Documentation

- [Infrastructure](docs/infrastructure.md) — composants AWS, flux de données, ressources déployées
- [Scripts et commandes](docs/usage.md) — CDK, provision_certs.py, simulator_mqtt.py, AWS CLI
