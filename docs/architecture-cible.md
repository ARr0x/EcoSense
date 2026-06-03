# Architecture cible — EcoSense
## Plateforme de Monitoring Environnemental Intelligent

**Version:** 1.1  
**Date:** Juin 2026  
**Propriétaire:** DevOps / Infrastructure  
**Statut:** En révision

---

## Table des matières
1. [Vue d'ensemble](#vue-densemble)
2. [Contexte et objectifs](#contexte-et-objectifs)
3. [Principes architecturaux](#principes-architecturaux)
4. [Architecture générale](#architecture-générale)
5. [Description des composants](#description-des-composants)
6. [Flux de données](#flux-de-données)
7. [Considérations non-fonctionnelles](#considérations-non-fonctionnelles)
8. [Architecture de sécurité](#architecture-de-sécurité)

---

## Vue d'ensemble

EcoSense est une plateforme d'monitoring environnemental en temps réel déployée à l'échelle métropolitaine. Elle ingère des milliers de capteurs IoT mesurant en continu la qualité de l'air, les niveaux sonores et l'humidité, avec une exigence critique : **zéro perte de données** associée à une **résilience cross-région**.

L'architecture répond à deux flux parallèles et complémentaires :
- **Chemin critique (hot path)** : détection et alerting instantanés des anomalies
- **Chemin analytique (cold path)** : stockage exhaustif pour analyses statistiques

---

## Contexte et objectifs

### Défi métier
- Ingestion d'un flux continu et massif de métriques depuis des milliers de capteurs
- Détection en temps réel (< 1min) des événements critiques (pollution extrême, incidents suspects)
- Stockage exhaustif des données brutes pour analyses hebdomadaires
- Absence totale de perte de données, même en cas de défaillance régionale AWS complète

### Objectifs techniques
- **Résilience** : tolérance aux pannes région AWS et saturation momentanée
- **Latence critique** : alertes < 1 minute
- **Capacité** : absorption de pics masifs sans perte
- **Coût** : optimisation du stockage et du traitement asynchrone
- **Observabilité** : traçabilité complète des flux

---

## Principes architecturaux

| Principe | Justification |
|----------|---------------|
| **Séparation des chemins** | Les alertes critiques (temps réel) ne doivent pas être bloquées par le traitement analytique |
| **Store-and-forward** | Firehose buffering garantit qu'aucun message n'est perdu en cas de pic |
| **Décentralisation régionale** | Route 53 + health checks masquent les défaillances régionales |
| **Immutabilité des données brutes** | S3 = source de vérité ; transformations appliquées uniquement en lecture (Athena/Glue) |
| **Infrastructure as Code** | Tous les composants doivent être reproductibles et versionés |

---

## Architecture générale

```
┌─────────────────────────────────────────────────────────────────┐
│                     CAPTEURS IoT (Terrain)                       │
│                  - Certificats MQTT uniques                      │
│                  - Protocole MQTT TLS 1.2+                       │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ↓ MQTT over TLS
┌──────────────────────────────────────────────────────────────────┐
│                      Route 53 (DNS Global)                        │
│     Health Checks → Basculement endpoint                          │
│     (Region 1 ↔ us-east-2 failover automatique)                  │
└──────────────────────────────────────────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    ↓                     ↓
         ┌──────────────────────┐ ┌──────────────────────┐
         │  REGION 1            │ │  us-east-2           │
         │  ─────────────────── │ │  ─────────────────── │
         │  IoT Core + Rules    │ │  IoT Core + Rules    │
         │  SNS + Firehose      │ │  SNS + Firehose      │
         │  S3 (Primary)        │ │  S3 (Replica)        │
         └──────┬───────────────┘ └──────┬───────────────┘
                │                        │
        ┌───────┴──────┐         ┌───────┴──────┐
        ↓              ↓         ↓              ↓
    ┌────────┐  ┌──────────┐ ┌────────┐  ┌──────────┐
    │  SNS   │  │Firehose  │ │  SNS   │  │Firehose  │
    │alerts  │  │(buffering)│ │alerts  │  │(buffering)│
    └─┬──────┘  └────┬─────┘ └────┬───┘  └────┬─────┘
      │              │            │            │
      ↓              ↓            ↓            ↓
   [Email]    ┌────────────────────────────────┐
              │    S3 (Cross-Region Replica)   │
              │  - Primary (Region 1)          │
              │  - Replica → us-east-2 (auto)  │
              │  - Partitioning: YYYY/MM/DD/HH │
              │  - Retention: 30 jours         │
              └────────┬─────────────────────┘
                       │
           ┌───────────┴──────────┐
           ↓                      ↓
        ┌──────┐            ┌──────────┐
        │ Glue │            │  Athena  │
        │Catalog│            │ Queries  │
        └───┬──┘            └────┬─────┘
            │                    │
            └──────────┬─────────┘
                       ↓
                ┌──────────────┐
                │ QuickSight   │
                │ (Dashboards) │
                └──────────────┘
```

---

## Description des composants

### 1. Route 53 (DNS + Health Checks)
**Rôle :** Point d'entrée unique avec basculement automatique inter-régional

#### Architecture multi-région
- **Région Primaire** : ex. eu-west-1 (ou votre région principale)
- **Région Secondaire** : us-east-2 (basculement)
- **Enregistrements DNS** : deux A-records pointant sur IoT Core endpoints
  ```
  ecosense-api.example.com → 
    Primary   : iot-core.region1.amazonaws.com
    Secondary : iot-core.us-east-2.amazonaws.com (failover)
  ```

#### Health Checks
- **Cible** : IoT Core endpoint (ou proxy health check)
- **Fréquence** : chaque 30 sec
- **Seuil d'alerte** : 3 checks successifs échoués → basculement
- **Politique de routage** : **Failover** (active-passive)
  - Primary actif tant que healthy
  - Si Primary down → bascule automatique vers Secondary

#### RTO (Recovery Time Objective)
- Détection du down : ~90 secondes (3 × 30s)
- TTL DNS : 60 secondes → clients reprennent dans les 1-2 min
- **RTO total : 2-3 minutes**

**Coûts :**
- Route 53 health check : ~0.50 USD/mois
- Route 53 queries : ~0.40 USD/million queries

---

### 2. AWS IoT Core
**Rôle :** Récepteur MQTT sécurisé et gestionnaire de règles

#### 2.1 Ingestion (MQTT)
- **Protocole** : MQTT 3.1.1 / 5.0 sur TLS 1.2+
- **Certificats X.509** : un certificat par capteur (empêche usurpation)
- **Topics MQTT** :
  ```
  sensors/{sensor_id}/metrics
  sensors/{sensor_id}/critical
  ```
- **Capacité** : ~1M messages/sec par compte AWS (à ajuster si dépassement)

#### 2.2 Règles SQL (2 chemins)
**Règle 1 : Alertes critiques**
```sql
SELECT *, timestamp() as received_at, deviceId 
FROM 'sensors/+/metrics'
WHERE (pollutionIndex > THRESHOLD_HIGH 
   OR soundLevel > 85 
   OR fireDetected = true)
```
→ **Action** : Publier sur SNS topic `critical-alerts`

**Règle 2 : Données complètes**
```sql
SELECT * 
FROM 'sensors/+/metrics'
```
→ **Action** : Envoyer vers Kinesis Data Firehose (buffering)

---

### 3. SNS (Simple Notification Service)
**Rôle :** Dispatching temps réel des alertes critiques

- **Topic** : `ecosense-critical-alerts`
- **Subscription** : Email (expansion future possible : SMS, webhook, Lambda)
- **Format** : JSON structuré (payload préformaté par IoT Core)

**SLA actuel :**
- Latence de livraison : < 60s (SNS garantit < 30s en général)
- Taux de délivrance : 99.5% (fiable pour volumes actuels)
- Charge estimée : faible (peu de capteurs = peu d'alertes critiques)

**Scaling SNS :**
- Pas de besoin actuel (volume d'alertes limité)
- Volume SNS : typiquement < 100 messages/jour
- Coût SNS : ~0.50 USD/mois (minimal)

**Prochains pas (futur) :**
- Si volume alertes grimpe, ajouter agrégation : SQS intermédiaire + Lambda de batch
- Ex : au lieu de 10k emails en 60s, envoyer 1 email "10k alertes détectées"

---

### 4. Kinesis Data Firehose
**Rôle :** Buffer distribué et transformateur de flux vers S3

#### Buffering strategy
- **Taille buffer** : 5 MB (flush dès atteinte)
- **Délai buffer** : 300 secondes = 5 minutes (timeout)
- **Garantie** : "At-least-once" delivery vers S3
- **Comportement** : S3 sera écrit dès que l'une des deux conditions est atteinte (5MB OU 300s)

#### Impact sur la latence analytique
- Données disponibles dans S3 dans les **5-10 minutes max**
- Athena peut lancer requêtes sur la dernière heure complète
- Rapport du matin inclut données jusqu'à 23h55 (délai acceptable pour analytique)

#### Transformations
- **Format de sortie** : Parquet (compression, schéma structuré)
- **Partitioning S3** : 
  ```
  s3://ecosense-data/YYYY/MM/DD/HH/
  2026/01/15/10/2026-01-15-10-23-45-abc123.parquet
  ```
  (Timestamp dans le nom du fichier pour traçabilité)

#### Retry & Error Handling
- **Retry automatique** : 0 à 3600s exponential backoff
- **Destination alternative (DLQ)** : À implémenter
  - Bucket S3 `ecosense-firehose-dlq/` pour messages rejetés
  - CloudWatch alarm si > 100 messages/min en DLQ
  - Investigation manuelle requise

#### Coûts
- Firehose : $0.29 par Go ingéré
- S3 writes : inclus dans Firehose

---

### 5. S3 (Simple Storage Service)
**Rôle :** Data Lake - source de vérité des données brutes avec haute disponibilité

#### Stratégie de stockage
- **Classe** : S3 Standard (30 jours) → **S3 Standard-IA** après 30j (suppression)
- **Versionning** : Désactivé (données immutables par partition temporelle)
- **Lifecycle policy** :
  ```
  Jour 1-30   → S3 Standard (accès chaud Athena)
  Jour 30+    → Suppression automatique
  ```

#### S3 Cross-Region Replication (CRR) ACTIVÉ
- **Source** : Bucket Region 1 (Primary)
- **Destination** : Bucket us-east-2 (Replica)
- **Réplication** : Automatique, temps quasi-réel (~15 sec)
- **Scope** : Tous les objets `YYYY/MM/DD/HH/`
- **Delete marker replication** : Activé (suppressions aussi répliquées)

**Avantages CRR :**
- RPO = ~15 secondes (données synchronisées)
- Disaster recovery : récupération instant en cas panne Region 1
- Analytics distribuées possibles (Athena lit depuis Region 2)
- Coût acceptable : ~200 USD/mois supplémentaires

#### Structure de partitioning (par timestamp)
```
ecosense-raw-data/
├── 2026/                          # Année
│   ├── 01/                        # Mois
│   │   ├── 15/                    # Jour
│   │   │   ├── 10/                # Heure (UTC)
│   │   │   │   ├── 2026-01-15-10-00-abc123.parquet
│   │   │   │   ├── 2026-01-15-10-05-def456.parquet
```

**Coûts mensuels (avec CRR):**
- Stockage S3 Standard (30j) : ~100 USD (Region 1)
- Stockage Replica (30j) : ~100 USD (us-east-2)
- CRR Transfer : ~100 USD (replication cross-region)
- **Total S3 : ~300 USD/mois**

---

### 6. AWS Glue
**Rôle :** Catalogage et transformation des données

#### Crawler Glue
- **Source** : Scanne régulièrement le bucket S3
- **Output** : Métadonnées dans Glue Data Catalog
- **Fréquence** : Hebdomadaire (après rapport)

#### Transformation (optionnel)
- **Nettoyage** : suppression doublons, validation schéma
- **Enrichissement** : rattachement données géographiques, météo externe
- **Agrégation** : statistiques pré-calculées (hourly, daily)

---

### 7. Amazon Athena
**Rôle :** Requêtes SQL interactive sans serveur

- **Source** : Tables Glue Data Catalog (S3 backend)
- **Durée de vie requête** : Typiquement < 2 min (partitioning + Parquet)
- **Coût** : ~$6.25 par To scanné

```sql
-- Exemple : pollution moyenne par quartier (semaine)
SELECT region, AVG(pollutionIndex) as avg_pollution, 
       COUNT(*) as samples
FROM ecosense_metrics
WHERE year=2026 AND month=01 AND day BETWEEN 1 AND 7
GROUP BY region;
```

---

### 8. Amazon QuickSight
**Rôle :** Visualisation et dashboards opérationnels

#### Dashboards suggérés
1. **Real-time Overview**
   - Dernière valeur par région / capteur
   - Tendance 24h (spark lines)
   - Alertes critiques (log temps réel)

2. **Trends & Reports**
   - Évolution pollution mensuelle
   - Corrélation bruit/heure (rush hours)
   - Anomalies (sigma-based flagging)

3. **Operational Health**
   - Taux d'uptime par région
   - Latence ingestion
   - Perte de données (si applicable)

---

## Flux de données

### Scénario 1 : Métrique normale
```
Capteur → IoT Core → [Règle 2: All data] 
       → Firehose [buffer 128MB ou 900s] 
       → S3 /year=X/month=Y/day=Z/hour=H/
```
**Latence end-to-end** : 0-15 min (dépend du buffer)  
**Garantie** : Zéro perte

---

### Scénario 2 : Alerte critique (ex: pic pollution)
```
Capteur → IoT Core → [Règle 1: WHERE pollution > THRESHOLD]
       → SNS Topic 
       → Email subscriber
```
**Latence end-to-end** : < 1 min  
**Garantie** : Livraison "best effort" (99.5%)

---

### Scénario 3 : Rapport analytique (hebdomadaire)
```
Data Scientists → Athena 
              → SELECT AVR(metrics) GROUP BY region, hour
              → QuickSight Dashboard/Export CSV
```
**Latence** : 1-3 min (temps requête + visualisation)  
**Coût** : ~50-100 USD/semaine (dépend volume scans)

---

### Scénario 4 : Défaillance région AWS (avec S3 CRR)
```
[Region 1: IoT Core + Firehose → S3 DOWN ou indisponible]

Route 53 Health Check détecte failure (après ~90 sec)
    ↓
DNS bascule automatiquement vers us-east-2 endpoint
    ↓
Capteurs reconnectent automatiquement à us-east-2 IoT Core
    ↓
Flux reprend : IoT Core (us-east-2) → Firehose (us-east-2) → S3 us-east-2
    ↓
S3 Replica (us-east-2) avait déjà copié 99% des données de Region 1
    ↓
Nouveau flux (post-bascule) continue dans us-east-2
```

**RTO** : ~2-3 minutes (détection Route 53 + reconnexion capteurs)  
**RPO** : ~15 secondes (S3 CRR synchronise quasi-temps réel)  

**Architecture notes :**
- S3 CRR activé : Region 1 → us-east-2 (réplication automatique)
- Données us-east-2 étaient déjà synchronisées (RPO 15s)
- Perte maximale : 15-20 sec de données (le temps du dernier flush Firehose)
- Pas besoin d'intervention manuelle : basculement complètement automatique

**Avantage vs sans CRR :**
- Sans CRR : RPO = 2-3 min, intervention manuelle possible
- Avec CRR : RPO = 15s, zéro perte données en pratique ✅

---

## Considérations non-fonctionnelles

### 1. Sécurité
| Composant | Mesures |
|-----------|---------|
| **MQTT** | TLS 1.2+, certificats X.509 par capteur, revocation (CRL) |
| **IoT Core** | Security groups, VPC isolation (optionnel), KMS encryption en transit |
| **S3** | Encryption at rest (KMS), ACLs privées, versioning (optionnel) |
| **SNS/Athena** | IAM roles fine-grained, VPC endpoints (optionnel) |

### 2. Scalabilité
- **IoT Core** : auto-scaling (1M msg/sec par défaut)
- **Firehose** : "unlimited" (absorption de pics)
- **S3** : partitioning temporel oblige partition pruning
- **Athena** : CCU-based concurrency (typiquement 24 CCU = $30/jour)

### 3. Coûts (estimation mensuelle, architecture double-région avec S3 CRR)
```
PER REGION (Primaire + us-east-2):

  IoT Core (rules ingestion)      : 300 USD × 2 = 600 USD
  Firehose (5MB/300s buffering)   : 150 USD × 2 = 300 USD
  S3 Standard (30j retention)     : 100 USD × 2 = 200 USD
  SNS (alertes emails)            : 25 USD × 2 = 50 USD
  ────────────────────────────────────────────────────
  SUBTOTAL (duplex)                            : 1,150 USD

S3 CROSS-REGION REPLICATION (CRR):
  S3 CRR Transfer (Region 1 → us-east-2)      : 100 USD
  S3 Replica storage (us-east-2)              : 100 USD
  ────────────────────────────────────────────────────
  SUBTOTAL (CRR)                              : 200 USD

SHARED (analytics, 1 instance):
  Athena (requêtes analytique)    : 200 USD
  Glue (crawler + catalog)        : 50 USD
  QuickSight (dashboards)         : 150 USD
  Route 53 (health checks + DNS)  : 30 USD
  ────────────────────────────────
  SUBTOTAL (shared)                           : 430 USD

────────────────────────────────────────────
TOTAL MENSUEL                                 ≈ 1,780 USD

S3 avec CRR (haute dispo) : RPO = 15 sec
Pas de Glacier (rétention 30j seulement)
Data transfer inter-région : ~50-100 USD/mois (estimé, non inclus)
Peu d'alertes SNS → coût minimal
```

### 4. Monitoring & Alerting
**CloudWatch Metrics à tracker :**
- `IoT.PublishIn.Success` (taux ingestion)
- `Firehose.DeliveryToS3.Records` (débit S3)
- `Firehose.DeliveryToS3.DataFreshness` (latence)
- `SNS.NumberOfMessagesPublished` (alertes)
- `SNS.NumberOfNotificationsFailed` (erreurs)

**Alarms critiques :**
- `Firehose.DeliveryToS3.DataFreshness > 1800s` → escalade ops
- `IoT.PublishIn.Failure > 5%` → incident
- `SNS.NumberOfNotificationsFailed > 100/min` → investigation

---


---

## Architecture de sécurité

Cette section couvre l'ensemble des mesures de sécurité applicables à EcoSense, organisées selon les grandes lignes directrices : **moindre privilège**, **défense en profondeur**, **chiffrement systématique**, **traçabilité et audit**, et **gestion des identités**.

---

### 1. Principe de moindre privilège (Least Privilege)

Le principe fondateur de cette section : **chaque composant, utilisateur ou service ne doit avoir accès qu'aux ressources strictement nécessaires à son rôle**, ni plus.

#### 1.1 IAM Policies — Règle du besoin d'en savoir

Chaque service AWS doit disposer d'un rôle IAM dédié avec des permissions minimales explicites. Aucun rôle `*` (wildcard) en production.

| Service | Permissions autorisées | Permissions explicitement refusées |
|---------|------------------------|-------------------------------------|
| **IoT Core Rules** | `firehose:PutRecord`, `sns:Publish` (topics ciblés) | Tout le reste AWS |
| **Firehose** | `s3:PutObject` (bucket `ecosense-raw-data/` uniquement) | `s3:GetObject`, `s3:DeleteObject` |
| **Glue Crawler** | `s3:GetObject`, `s3:ListBucket` (read-only) | `s3:PutObject`, `s3:DeleteObject` |
| **Athena** | `s3:GetObject` sur `ecosense-raw-data/`, `s3:PutObject` sur bucket résultats | Toute autre ressource S3 |
| **QuickSight** | `athena:StartQueryExecution`, accès résultats Athena uniquement | Accès direct S3 raw data |
| **SNS** | Publish vers abonnés autorisés | Création/suppression de topics |

```python
import boto3, json

iam = boto3.client("iam")

policy_document = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": ["s3:PutObject"],
        "Resource": "arn:aws:s3:::ecosense-raw-data/*"
    }]
}

# Attacher la policy inline au rôle Firehose
iam.put_role_policy(
    RoleName="ecosense-firehose-role",
    PolicyName="firehose-s3-write-only",
    PolicyDocument=json.dumps(policy_document)
)
```

#### 1.2 Séparation des responsabilités (Separation of Duties)

- **Opérateurs infrastructure** : accès aux ressources AWS (Firehose, IoT Core), sans accès aux données brutes S3
- **Data Scientists / Analystes** : accès Athena + QuickSight, sans accès direct aux buckets S3 ni aux règles IoT
- **Administrateurs sécurité** : accès aux logs CloudTrail, KMS, IAM — sans accès aux données métier
- **Capteurs IoT** : uniquement `iot:Publish` sur leurs propres topics (`sensors/{sensor_id}/metrics`)

#### 1.3 Scoping des certificats IoT

Chaque capteur reçoit un certificat X.509 unique, associé à une **IoT Policy restrictive** :

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["iot:Connect"],
      "Resource": "arn:aws:iot:*:*:client/${iot:ClientId}"
    },
    {
      "Effect": "Allow",
      "Action": ["iot:Publish"],
      "Resource": "arn:aws:iot:*:*:topic/sensors/${iot:ClientId}/metrics"
    },
    {
      "Effect": "Deny",
      "Action": ["iot:Subscribe", "iot:Receive"],
      "Resource": "*"
    }
  ]
}
```

→ Un capteur ne peut publier **que sur son propre topic**. Il ne peut ni lire les données des autres capteurs, ni s'abonner à des topics.

---

### 2. Chiffrement — Données en transit et au repos

#### 2.1 En transit (in-transit)

| Segment | Protocole | Notes |
|---------|-----------|-------|
| Capteurs → IoT Core | MQTT over TLS 1.2+ | Certificats mutuels (mTLS) |
| IoT Core → Firehose | HTTPS / AWS internal TLS | Chiffré par défaut dans le réseau AWS |
| Firehose → S3 | HTTPS / AWS internal TLS | Chiffré par défaut |
| S3 → Athena | HTTPS | Chiffré par défaut |
| Athena → QuickSight | HTTPS | Chiffré par défaut |
| S3 CRR (cross-region) | TLS interne AWS | Chiffré en transit inter-régions |

**Vérification TLS :** désactiver explicitement TLS 1.0 et 1.1 sur tous les endpoints. Forcer TLS 1.2 minimum (TLS 1.3 recommandé).

#### 2.2 Au repos (at-rest)

- **S3** : chiffrement SSE-KMS (Server-Side Encryption avec AWS KMS)
  - Une clé KMS dédiée `ecosense-data-key` (rotation annuelle automatique)
  - Bucket policy pour **refuser** les `PutObject` sans chiffrement :
  ```json
  {
    "Effect": "Deny",
    "Principal": "*",
    "Action": "s3:PutObject",
    "Resource": "arn:aws:s3:::ecosense-raw-data/*",
    "Condition": {
      "StringNotEquals": { "s3:x-amz-server-side-encryption": "aws:kms" }
    }
  }
  ```
- **Kinesis Firehose** : chiffrement KMS activé sur le stream
- **SNS** : chiffrement SSE activé (clé KMS dédiée ou AWS managed)
- **Glue Data Catalog** : chiffrement des métadonnées activé

#### 2.3 Gestion des clés KMS

| Clé | Usage | Rotation |
|-----|-------|----------|
| `ecosense-data-key` | Chiffrement S3 raw data (les deux régions) | Annuelle (automatique) |
| `ecosense-stream-key` | Chiffrement Firehose streams | Annuelle (automatique) |
| `ecosense-sns-key` | Chiffrement SNS topics | Annuelle (automatique) |

- Politique KMS : uniquement les rôles IAM autorisés peuvent utiliser `kms:Decrypt`
- Accès `kms:CreateKey`, `kms:ScheduleKeyDeletion` réservé aux administrateurs sécurité

---

### 3. Isolation réseau

#### 3.1 VPC et VPC Endpoints (recommandé)

Pour empêcher que le trafic entre AWS et les services transite par internet public :

| Service | Action recommandée |
|---------|--------------------|
| **S3** | VPC Endpoint (Gateway) — gratuit, trafic interne AWS |
| **Kinesis Firehose** | VPC Endpoint (Interface) — ~$7/mois |
| **IoT Core** | VPC Endpoint optionnel (coût +50 USD/mois, cf. points à clarifier) |
| **Athena** | VPC Endpoint (Interface) — recommandé pour data scientists internes |
| **Glue** | VPC Endpoint (Interface) |

**Bucket Policy S3 — restriction aux VPC Endpoints uniquement :**
```json
{
  "Effect": "Deny",
  "Principal": "*",
  "Action": "s3:*",
  "Resource": ["arn:aws:s3:::ecosense-raw-data", "arn:aws:s3:::ecosense-raw-data/*"],
  "Condition": {
    "StringNotEquals": { "aws:SourceVpce": "vpce-XXXXXXXXX" }
  }
}
```

#### 3.2 Security Groups

- **IoT Core** : uniquement port 8883 (MQTT/TLS) entrant depuis Internet (capteurs terrain)
- **Services internes** (Glue, Athena, Firehose) : aucun accès Internet direct — uniquement via VPC Endpoints
- **QuickSight** : accès HTTPS depuis les réseaux internes uniquement (pas d'exposition publique)

#### 3.3 Blocage S3 public

Activer le **S3 Block Public Access** sur tous les buckets (paramètre au niveau du compte AWS) :

```python
import boto3

s3 = boto3.client("s3")

s3.put_public_access_block(
    Bucket="ecosense-raw-data",
    PublicAccessBlockConfiguration={
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True
    }
)
```

---

### 4. Authentification et gestion des identités

#### 4.1 Capteurs IoT — Rotation et révocation des certificats

- **Rotation préventive** : renouvellement des certificats X.509 tous les 12 mois
- **Révocation immédiate** : en cas de compromission, désactiver le certificat dans AWS IoT Core (liste CRL ou politique `INACTIVE`)
- **Inventaire des certificats** : chaque capteur référencé dans un registre IoT Core avec métadonnées (localisation, date installation, date expiration certificat)

```
IoT Core Registry → Thing Name: sensor-paris-01-district-3
                  → Certificate ARN: arn:aws:iot:eu-west-1:XXXX:cert/YYYY
                  → Status: ACTIVE / INACTIVE
                  → Expires: 2027-06-01
```

#### 4.2 Accès humains — Comptes et MFA

- **MFA obligatoire** pour tous les comptes AWS IAM humains (opérateurs, admins, data scientists)
- **Comptes de service** (utilisés par les applications) : pas de console AWS, credentials rotés via AWS Secrets Manager ou rôles IAM
- **Pas de clés d'accès AWS statiques** dans le code ou les variables d'environnement
- **AWS SSO / IAM Identity Center** recommandé pour la gestion centralisée des accès humains

#### 4.3 Accès analytique (QuickSight / Athena)

- Authentification via **AWS IAM** ou **AWS SSO**
- Groupes d'accès distincts : `ecosense-analysts` (lecture seule) vs `ecosense-ops` (opérations)
- Pas d'accès direct aux buckets S3 pour les analystes : uniquement via Athena (couche d'abstraction)

---

### 5. Traçabilité et audit

#### 5.1 CloudTrail — Audit de toutes les actions AWS

**CloudTrail activé** dans toutes les régions utilisées (Region 1 + us-east-2) :

- **Management Events** : création/suppression de ressources, modifications IAM, changements KMS
- **Data Events S3** : `GetObject`, `PutObject`, `DeleteObject` sur `ecosense-raw-data/` (volume élevé — filtrer si coûts importants)
- **Data Events IoT Core** : connexions, publications de messages (activé si besoin de forensics)

Logs CloudTrail stockés dans un **bucket S3 dédié et séparé** (`ecosense-audit-logs/`) avec :
- Accès restreint aux administrateurs sécurité uniquement
- Chiffrement KMS dédié (`ecosense-audit-key`)
- **Verrouillage S3 Object Lock** : rétention immuable 1 an (empêche toute suppression ou modification des logs)

#### 5.2 Logs applicatifs — IoT Core et Firehose

- **IoT Core Logs** : activer les logs de niveau `ERROR` en production, `DEBUG` lors des investigations
  - Destination : CloudWatch Logs `/ecosense/iot-core/`
- **Firehose Logs** : erreurs de livraison vers S3 loguées dans CloudWatch
  - Alarm si taux d'erreur > 1%

#### 5.3 Métriques de sécurité à monitorer dans CloudWatch

| Métrique | Seuil d'alerte | Action |
|----------|----------------|--------|
| `IoT.AuthenticationFailures` | > 10/min | Investigation immédiate (tentative d'usurpation) |
| `IoT.AuthorizationFailures` | > 5/min | Vérification politique IAM / certificats |
| `KMS.InvalidKeyId` | > 0 | Investigation (tentative accès données chiffrées) |
| `S3.GetObject` depuis IP non-VPC | Tout | Alerte (accès hors VPC endpoint) |
| Connexion depuis nouveau capteur inconnu | Tout | Alerte (registre IoT Core) |

#### 5.4 GuardDuty (recommandé)

Activer **AWS GuardDuty** pour la détection d'anomalies automatique :
- Détection d'accès S3 inhabituels (exfiltration de données)
- Détection de comportements IAM anormaux (credential compromise)
- Intégration avec SNS pour alertes opérationnelles de sécurité

---

### 6. Protection des données et conformité

#### 6.1 Classification des données

| Type de donnée | Niveau de sensibilité | Mesures appliquées |
|---------------|----------------------|-------------------|
| Métriques environnementales brutes | **Faible** (données publiques potentielles) | Chiffrement KMS, accès restreint |
| Localisation précise des capteurs | **Moyen** (infrastructure critique) | Accès opérateurs uniquement, non exposé analytique |
| Certificats IoT et clés privées | **Critique** | Jamais stockés en clair, rotation, révocation rapide |
| Logs d'audit CloudTrail | **Critique** | Bucket séparé, immuable, accès admins sécurité uniquement |

#### 6.2 Immutabilité des données brutes

Renforcer le principe d'immutabilité déjà mentionné dans l'architecture :
- **S3 Object Lock** (Governance Mode) sur `ecosense-raw-data/` : empêche la suppression accidentelle ou malveillante des données avant l'expiration des 30 jours
- **Versionning S3** activé (contrairement à la version actuelle) pour permettre la restauration en cas d'écrasement accidentel

#### 6.3 Minimisation des données

- Les règles SQL IoT Core ne doivent transmettre à Firehose que les champs nécessaires (éviter `SELECT *` si des champs sensibles peuvent apparaître)
- Les dashboards QuickSight ne doivent jamais exposer directement les identifiants techniques des capteurs à des utilisateurs non-opérateurs (pseudonymisation si besoin)

---

### 7. Gestion des incidents de sécurité

#### 7.1 Procédures de réponse rapide

| Incident | Action immédiate | Délai cible |
|----------|-----------------|-------------|
| Certificat capteur compromis | Désactiver le certificat dans IoT Core Registry | < 15 minutes |
| Fuite de credentials AWS | Révoquer les clés IAM, audit CloudTrail | < 30 minutes |
| Accès non autorisé à S3 | Bloquer l'IP/principal IAM, audit des accès | < 30 minutes |
| Clé KMS compromise | Rotation d'urgence, re-chiffrement des données | < 2 heures |

#### 7.2 Runbooks de sécurité à créer (Phase 2)

- `runbook-certificate-revocation.md` : procédure de révocation certificat capteur
- `runbook-iam-credential-leak.md` : procédure de compromission de credentials
- `runbook-data-access-anomaly.md` : investigation d'un accès S3 anormal
- `runbook-kms-key-rotation.md` : rotation d'urgence des clés KMS

---

### 8. Récapitulatif des contrôles de sécurité

| Domaine | Contrôle | Statut recommandé |
|---------|----------|--------------------|
| **Authentification IoT** | Certificats X.509 mutuels (mTLS) | En place |
| **Autorisation IoT** | Policy par capteur, topic restreint | À implémenter |
| **Chiffrement transit** | TLS 1.2+ sur tous les segments | En place |
| **Chiffrement repos** | SSE-KMS sur S3, Firehose, SNS | ⚠️ À implémenter (KMS dédié) |
| **Moindre privilège IAM** | Rôles dédiés par service, pas de wildcard | ⚠️ À implémenter |
| **Isolation réseau** | VPC Endpoints pour services internes | 🔴 À décider (voir §3.1) |
| **Blocage accès public S3** | S3 Block Public Access | À activer |
| **Audit CloudTrail** | Logs toutes régions, bucket immuable | ⚠️ À implémenter |
| **Rotation certificats** | Renouvellement annuel + révocation | ⚠️ Processus à définir |
| **MFA humains** | MFA obligatoire sur tous les comptes IAM | ⚠️ À enforcer |
| **GuardDuty** | Détection d'anomalies automatisée | 🔴 Optionnel mais recommandé |
| **S3 Object Lock** | Immutabilité données brutes 30 jours | 🔴 À décider |
| **Séparation des rôles** | Opérateurs / Data Scientists / Admins | ⚠️ À formaliser |

**Légende :** En place / ⚠️ À implémenter / 🔴 À décider ou optionnel

> Les décisions prises et la roadmap vers la production sont documentées dans [limitations-et-ameliorations.md](limitations-et-ameliorations.md).
