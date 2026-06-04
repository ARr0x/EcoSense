# Architecture Decision Records — EcoSense

Les ADR documentent les décisions techniques structurantes prises pendant le hackathon.

---

## ADR-001 — AWS IoT Core comme point d'entrée au lieu de SNS Topic

**Date** : 30 mai 2026
**Statut** : Accepté

### Contexte

Le sujet prescrit un **Topic SNS public** comme point d'entrée unique pour la publication des données capteurs. Les capteurs simulent l'envoi de payloads JSON depuis un script Python local.

### Décision

Utilisation d'**AWS IoT Core** (MQTT/mTLS port 8883) à la place d'un Topic SNS HTTP/HTTPS.

### Raisons

- IoT Core est le service AWS natif pour les objets connectés : il gère le protocole MQTT, l'authentification par certificat X.509, les IoT Policies par capteur et le QoS (garantie de livraison).
- Un Topic SNS public n'offre aucune authentification des producteurs — n'importe qui connaissant l'ARN pourrait publier.
- IoT Core inclut nativement un moteur de règles SQL (Topic Rules) qui fait office de bus de routage, remplaçant le filtre SNS Subscription Filter Policy prescrit.
- Le simulateur MQTT (`paho-mqtt`) est plus proche du comportement réel d'un capteur qu'un client HTTP/SNS.

### Conséquences

- Le format du topic MQTT (`metropole/{quartier}/{sensor_id}/telemetry`) devient la convention de nommage du projet.
- Les certificats X.509 doivent être provisionnés par région (`provision_certs.py`).
- En cas de migration vers de vrais capteurs matériels, aucun changement côté infrastructure n'est nécessaire.

---

## ADR-002 — Kinesis Firehose au lieu de Lambda Archiving-Processor + SQS

**Date** : 1er juin 2026
**Statut** : Accepté

### Contexte

Le sujet prescrit une architecture **SQS Archiving-Queue → Lambda Archiving-Processor → S3**. La Lambda reçoit les messages par lots (batch de 10-20) et écrit des fichiers JSON/CSV en S3.

### Décision

Utilisation d'**Amazon Kinesis Data Firehose** à la place du couple SQS + Lambda.

### Raisons

- Firehose est conçu spécifiquement pour l'ingestion de masse : buffer configurable (60 s / 5 MB), compression GZIP, partitionnement Hive automatique — sans une ligne de code Lambda à maintenir.
- Pas de risque de perte de messages entre SQS et Lambda (Firehose garantit la livraison à S3 avec retry automatique).
- La Lambda Archiving-Processor aurait nécessité du code de gestion des batches, des erreurs de partition, de la sérialisation JSON → formats analytiques — Firehose le fait nativement.
- Coût inférieur : Firehose facture au volume ingéré, la Lambda au nombre d'invocations × durée.

### Conséquences

- Le bucket S3 utilise le partitionnement `YYYY-MM-DD-HH/`.
- Une table Glue avec **partition projection** (plage 2026–2030) est créée dans `StorageStack` pour éviter d'avoir besoin d'un crawler Glue.
- Les requêtes Athena fonctionnent immédiatement sans `MSCK REPAIR TABLE`.

---

## ADR-003 — Failover client-side au lieu de Route 53 DNS Failover

**Date** : 2 juin 2026
**Statut** : Accepté (contrainte environnementale)

### Contexte

La stratégie de résilience multi-région standard sur AWS repose sur **Route 53 Failover Policy** : un enregistrement DNS (CNAME) pointe vers le endpoint actif, et Route 53 bascule automatiquement le DNS en cas d'échec du health check (RTO ~2 min).

### Décision

Implémentation d'un **failover côté client** : le simulateur interroge directement l'API Route 53 (`GetHealthCheckStatus`) et bascule lui-même vers la région secondaire.

### Raisons

- Route 53 ne permet pas l'enregistrement de noms de domaine dans AWS Academy Learner Lab.
- AWS IoT Core utilise **mTLS** (certificats X.509) — un CNAME Route 53 vers l'endpoint IoT Core est techniquement incompatible : le SNI TLS (`*.iot.us-east-1.amazonaws.com`) ne correspondrait pas à un CNAME personnalisé, causant une erreur de certificat.
- Le failover client-side offre un RTO comparable (~5-30 s selon le mode) avec une complexité implémentation acceptable.

### Conséquences

- Deux mécanismes de failover coexistent : **proactif** (polling Route 53 toutes les 5 s) et **réactif** (détection déconnexion MQTT → reconnexion sur l'autre région).
- Le failover ne s'applique qu'au simulateur. En production, chaque client IoT (capteur matériel) devrait embarquer la même logique de reconnexion.
- RTO réel : 5-10 s (proactif Route 53) ou jusqu'à 10 min (réactif, car AWS IoT ne coupe pas les sessions MQTT existantes immédiatement lors de la désactivation d'un certificat).

---

## ADR-004 — Région secondaire us-west-2 au lieu de us-east-2

**Date** : 2 juin 2026
**Statut** : Accepté (contrainte environnementale)

### Contexte

Les consignes du hackathon prescrivent les régions `us-east-1` (primaire) et `us-east-2` (secondaire).

### Décision

Utilisation de `us-west-2` comme région secondaire à la place de `us-east-2`.

### Raisons

- Dans l'environnement AWS Academy Learner Lab, `us-east-2` est bloquée pour le rôle `voclabs` : `s3:CreateBucket` et `cloudformation:CreateStack` retournent `AccessDeniedException`.
- `us-west-2` dispose de tous les services nécessaires (IoT Core, Kinesis Firehose, S3, SNS, Lambda) et n'est pas soumise à ces restrictions.

### En production

La migration vers `us-east-1` + `us-east-2` ne nécessite qu'un changement de la variable `SECONDARY_REGION` dans `.env` et un redéploiement CDK. Aucun code applicatif ne hardcode les noms de régions.

### Conséquences

- Le sujet prescrit `us-west-2` comme secondaire dans la section "défi multi-région" — ce choix est donc aligné avec le sujet même s'il dévie des consignes générales.
- Documenté dans [`docs/limitations-et-ameliorations.md`](limitations-et-ameliorations.md).

---

## ADR-005 — LabRole unique au lieu de rôles IAM dédiés par service

**Date** : 29 mai 2026
**Statut** : Accepté (contrainte environnementale)

### Contexte

Bonne pratique AWS : chaque service (IoT Core, Firehose, Lambda) doit avoir un rôle IAM dédié avec les permissions minimales nécessaires (principe du moindre privilège).

### Décision

Utilisation du rôle pré-existant `LabRole` (`arn:aws:iam::{account}:role/LabRole`) pour tous les services.

### Raisons

- AWS Academy Learner Lab interdit strictement la création de rôles, politiques ou profils d'instance IAM personnalisés.
- `LabRole` dispose de permissions larges couvrant tous les services utilisés.

### En production

Remplacer `LabRole` par des rôles dédiés :
- `ecosense-iot-role` : `iot:Publish` → SNS ARN + Firehose ARN spécifiques
- `ecosense-firehose-role` : `s3:PutObject` → bucket ARN spécifique + préfixe `raw/`
- `ecosense-lambda-role` : `sns:Publish` + `sqs:*` + `dynamodb:*` sur les ressources EcoSense uniquement

### Conséquences

- Le `LabRole` est référencé dans tous les stacks CDK via `iam.Role.from_role_arn(...)`.
- Déployer hors AWS Academy sans adapter les rôles entraîne des erreurs CDK.
