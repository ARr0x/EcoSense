# Documentation d'Architecture - EcoSense
## Plateforme de Monitoring Environnemental Intelligent

**Version:** 1.0  
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
8. [Points à clarifier](#points-à-clarifier)

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
- ✅ Pas de besoin actuel (volume d'alertes limité)
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
- **Destination alternative (DLQ)** : ✅ À implémenter
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

#### S3 Cross-Region Replication (CRR) ✅ ACTIVÉ
- **Source** : Bucket Region 1 (Primary)
- **Destination** : Bucket us-east-2 (Replica)
- **Réplication** : Automatique, temps quasi-réel (~15 sec)
- **Scope** : Tous les objets `YYYY/MM/DD/HH/`
- **Delete marker replication** : Activé (suppressions aussi répliquées)

**Avantages CRR :**
- ✅ RPO = ~15 secondes (données synchronisées)
- ✅ Disaster recovery : récupération instant en cas panne Region 1
- ✅ Analytics distribuées possibles (Athena lit depuis Region 2)
- ✅ Coût acceptable : ~200 USD/mois supplémentaires

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
- ✅ S3 CRR activé : Region 1 → us-east-2 (réplication automatique)
- ✅ Données us-east-2 étaient déjà synchronisées (RPO 15s)
- ✅ Perte maximale : 15-20 sec de données (le temps du dernier flush Firehose)
- ✅ Pas besoin d'intervention manuelle : basculement complètement automatique

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

✅ S3 avec CRR (haute dispo) : RPO = 15 sec
✅ Pas de Glacier (rétention 30j seulement)
✅ Data transfer inter-région : ~50-100 USD/mois (estimé, non inclus)
✅ Peu d'alertes SNS → coût minimal
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

## Points à clarifier (V2.1 - CRR confirmé ✅)

### ✅ Décisions confirmées
1. **Architecture multi-région** : Duplication complète (Region 1 + us-east-2), failover DNS via Route 53
2. **S3 Partitioning** : Par timestamp (YYYY/MM/DD/HH/) pour fraîcheur 5-10 min
3. **Firehose buffering** : 5MB ou 300s (ce qui arrive en premier)
4. **Rétention S3** : 30 jours (suppression automatique, pas Glacier)
5. **Scaling SNS** : Non requis (volume alertes actuel très faible)
6. **S3 Cross-Region Replication (CRR)** : ✅ **OUI, ACTIVÉ**
   - RPO = ~15 secondes (données quasi-synchronisées)
   - Coût : +200 USD/mois
   - Avantage : zéro perte données pratiquement garantie

### 🔴 À décider encore

**#1. Dead Letter Queue (DLQ) pour Firehose - PRIORITAIRE**
- Implémentation recommandée : Bucket S3 séparé `ecosense-firehose-dlq/`
- Messages rejetés (format invalide, etc.) s'accumulent ici
- CloudWatch alarm si DLQ > 100 messages/jour
- Action : Implémenter DLQ + alarm dans Terraform avant production

**#2. Monitoring avancé & SLA interne**
- Actuellement : CloudWatch metrics de base
- À valider : SLA interne acceptable ? (< 1 min pour alertes critiques ✅, 5-10 min pour analytique ✅)
- À ajouter : Dashboard opérationnel custom (dépend outils internes)

**#3. Sécurité : Besoin VPC isolation ?**
- Actuellement : IoT Core public endpoint + certificats
- Option : VPC IoT Core endpoint (isolation réseau, coût +50 USD/mois)
- Décision : dépend politique sécurité interne

---

## Prochaines étapes (Roadmap)

### Phase 1 : Finalisation architecture (URGENT - semaine 1)
- [ ] **Décider DLQ** : Approuver implémentation Firehose DLQ
- [ ] **Décider CRR** : S3 Cross-Region Replication oui/non ?
- [ ] **Valider SLA** : Latence cibles acceptables ?
- [ ] **Sécurité review** : VPC isolation, KMS encryption, IAM policies
- [ ] **Estimation capteurs** : Combien exactement ? (affecte coûts)

### Phase 2 : Infrastructure as Code (2-3 semaines)
- [ ] Créer templates Terraform (Region 1 + us-east-2)
  - IoT Core + Rules
  - Firehose + S3
  - SNS + DLQ
  - Route 53 health checks
- [ ] Validation cloudformation (dry-run)
- [ ] Documentation runbooks d'opération

### Phase 3 : Testing (1-2 semaines)
- [ ] **Load test** : 1,000 msg/sec sur 5 min (capacité check)
- [ ] **Failover test** : Simuler panne Region 1 → vérifier basculement
- [ ] **Data integrity** : Vérifier zéro perte sur 24h
- [ ] **Latence SLA** : Mesurer P95 latence alertes critiques

### Phase 4 : Déploiement production (1 semaine)
- [ ] Déploiement Region 1
- [ ] Déploiement us-east-2 (réplication)
- [ ] Validation santé complète
- [ ] Cutover capteurs (progressive)

### Phase 5 : Monitoring à long terme
- [ ] Dashboards QuickSight opérationnels
- [ ] Alertes CloudWatch configurées
- [ ] Runbooks incident (panne région, data loss, etc.)
- [ ] Coûts : tracking mensuel vs budget

---

**Révision prochaine :** Après décisions sur DLQ et CRR

