# Sujet — Architecture asynchrone, ingestion de masse et découplage

**Groupe 4** — Frédéric Chapot, Arthur Lacombe, Matéo Nicoud

---

## Scénario métier

La métropole connectée **EcoSense** déploie des milliers de capteurs environnementaux IoT pour mesurer en temps réel la qualité de l'air, l'indice sonore et le taux d'humidité de ses quartiers. Ces capteurs envoient un flux ininterrompu de données métriques. Le défi est double :

1. Les données **critiques** (pic de pollution, incendie suspect) doivent être détectées et traitées **instantanément** pour alerter les services d'urgence.
2. L'intégralité des données brutes doit être **stockée de façon asynchrone** pour permettre aux équipes data science de générer des rapports statistiques.

L'infrastructure doit être **hautement résiliente**, capable d'absorber des pics d'envoi massifs sans saturer les systèmes de calcul ni perdre un seul message, même si une région AWS complète devient indisponible.

---

## Architecture implémentée

### Couche d'ingestion

Les capteurs sont simulés par `simulator/simulator_mqtt.py` qui publie des payloads JSON via **MQTT/mTLS** vers **AWS IoT Core**. Ce choix remplace le SNS Topic prescrit par le sujet car IoT Core est le protocole natif des objets connectés — il gère l'authentification par certificat X.509, le QoS MQTT et la terminaison TLS sans infrastructure supplémentaire.

Payload type :
```json
{
  "sensor_id": "S-042",
  "metric": "CO2",
  "value": 1250,
  "unit": "ppm",
  "status": "CRITICAL",
  "region": "us-east-1",
  "quartier": "centre",
  "timestamp": 1748952000
}
```

### Fan-out via Topic Rules IoT Core

Deux **Topic Rules SQL** remplacent le bus SNS→SQS du sujet et implémentent le même patron de découplage :

| Règle | Filtre SQL | Destination | Équivalent sujet |
|---|---|---|---|
| `ecosense_alert_rule` | `WHERE status = 'CRITICAL'` | **SNS** → Email | Alert-Queue → Lambda Alert-Processor |
| `ecosense_archive_rule` | _(aucun filtre — 100 % du flux)_ | **Kinesis Firehose** → S3 | Archiving-Queue → Lambda Archiving-Processor |

### Couche de traitement et stockage

- **Alertes CRITICAL** : SNS publie directement sur le topic `ecosense-alert-{region}`, qui envoie un email aux équipes d'intervention. Une Lambda d'agrégation (`ecosense-ingest`) regroupe les alertes par quartier avec un backoff exponentiel pour éviter les doublons.
- **Archivage** : Kinesis Firehose bufferise les messages (60 s / 5 MB) et écrit en S3 avec partitionnement Hive `raw/year=YYYY/month=MM/day=DD/hour=HH/`. Une table Glue avec **partition projection** permet des requêtes Athena sans crawler.

### Défi multi-région

| Aspect | Implémentation |
|---|---|
| IaC multi-région | `MULTI_REGION=true` dans `.env` — CDK déploie les stacks identiques en `us-east-1` et `us-west-2` |
| Failover proactif | Route 53 health checks HTTPS sur Lambda Function URLs — le simulateur interroge l'API `GetHealthCheckStatus` toutes les 5 s et bascule avant la panne MQTT |
| Failover réactif | Détection de déconnexion MQTT → reconnexion automatique sur la région secondaire |
| Réplication des archives | Deux buckets S3 indépendants (contrainte Learner Lab : S3 CRR bloqué par `LabRole`) |

### Déviation par rapport au sujet prescrit

Le sujet prescrit le patron **SNS → SQS → Lambda**. EcoSense utilise **IoT Core → Topic Rules → (SNS / Kinesis Firehose)** pour les raisons suivantes :

- IoT Core est le service AWS dédié aux objets connectés (MQTT, certificats X.509, policies par capteur)
- Kinesis Firehose remplace Lambda Archiving-Processor + S3 : il est conçu pour l'ingestion de masse avec buffer et compression natifs, sans code à maintenir
- Le fan-out par Topic Rules est strictement équivalent au filtre SNS Subscription Filter Policy du sujet

Les décisions d'architecture sont documentées dans [`docs/adr.md`](docs/adr.md).

---

## Scénarios de crash-test

### Test 1 — Lissage de charge et filtrage

```bash
# Lancer le simulateur à pleine puissance avec 30 % de messages CRITICAL
python simulator/simulator_mqtt.py --burst-size 50 --critical-rate 0.3

# Vérifier dans CloudWatch que :
# - ecosense_archive_rule : TopicMatch = 100 % des messages
# - ecosense_alert_rule   : TopicMatch = 30 % uniquement (filtre CRITICAL)
```

Les messages CRITICAL déclenchent l'envoi d'email via SNS. Les messages NORMAL partent uniquement vers Firehose → S3. Aucun message n'est perdu grâce au QoS=1 MQTT et au buffer Firehose.

### Test 2 — Rupture régionale

```bash
# Inverser le health check Route 53 primaire → simulateur détecte UNHEALTHY en ~5s
aws route53 update-health-check --health-check-id $HC_ID_PRIMARY --inverted

# Le simulateur bascule automatiquement sur us-west-2 :
# [WARNING] [Route53] us-east-1 signalé UNHEALTHY — basculement proactif
# [WARNING] Basculement vers us-west-2
# [INFO] Connecté à IoT Core (us-west-2)
```

Voir [`docs/runbook.md`](docs/runbook.md) pour la procédure complète de basculement.

---

## Structure du dépôt

```
EcoSense/
├── iac/                    # Infrastructure as Code (AWS CDK Python)
│   ├── app.py              # Orchestrateur CDK — 2 régions
│   └── stacks/             # SnsStack, StorageStack, IoTCoreStack, HealthStack, Route53Stack
├── simulator/              # Simulateur MQTT (500 capteurs)
│   ├── simulator_mqtt.py   # Publication MQTT/mTLS + failover multi-région
│   └── provision_certs.py  # Provisionnement certificats X.509 IoT Core
├── lambdas/                # Fonctions Lambda
│   ├── health/             # Health check endpoint (Route 53)
│   └── ...
├── docs/
│   ├── infrastructure.md           # Composants AWS déployés
│   ├── architecture-cible.md       # Architecture production cible
│   ├── adr.md                      # Architecture Decision Records
│   ├── runbook.md                  # Runbooks opérationnels (pannes critiques)
│   ├── usage.md                    # Référence commandes CDK / simulateur
│   └── limitations-et-ameliorations.md
├── Sujet.md                # Ce document
├── README.md               # Déploiement from scratch
└── PlaningEquipe.md        # Journal de bord quotidien
```
