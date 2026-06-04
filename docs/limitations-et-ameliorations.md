# Limitations et améliorations — EcoSense

Ce document compare l'implémentation actuelle (contrainte AWS Academy Learner Lab) avec l'architecture cible production décrite dans [`architecture-cible.md`](architecture-cible.md).

---

## Contraintes AWS Academy Learner Lab

Ces limitations sont imposées par l'environnement et ne reflètent pas des choix d'architecture.

| Contrainte | Impact | Workaround actuel |
|---|---|---|
| **CDK bootstrap bloqué** | `cdk bootstrap` interdit, le bucket d'assets CDK doit être créé manuellement | `make bootstrap-bucket` recrée le bucket avant chaque déploiement |
| **LabRole imposé** | Impossible de créer des rôles IAM via CDK — tous les services utilisent le même rôle partagé `LabRole` | Référence hardcodée `arn:aws:iam::{account}:role/LabRole` dans tous les stacks |
| **Session qui expire** | À chaque restart du lab, les credentials et endpoints IoT changent | `make lab-restart` recrée le bucket CDK, redéploie et re-provisionne les certificats |
| **Route 53 domaine non enregistrable** | Impossible d'enregistrer un domaine DNS dans Learner Lab → pas de DNS failover MQTT (CNAME vers IoT Core incompatible avec mTLS/SNI) | Route 53 health checks HTTPS sur Lambda Function URLs proxy. Le simulateur interroge `GetHealthCheckStatus` pour basculer proactivement (voir [infrastructure.md](infrastructure.md)) |
| **us-east-2 inaccessible** | `s3:CreateBucket` et `cloudformation:CreateStack` bloqués pour `voclabs` en us-east-2 (contrairement aux consignes) | Région secondaire déployée sur `us-west-2` — tous les services nécessaires disponibles |
| **KMS customer-managed keys bloqué** | Impossible de créer des clés KMS dédiées | Chiffrement SSE-S3 (AWS managed keys) |
| **NestedStack interdit** | Certains patterns CDK avancés ne sont pas supportés | Architecture à stacks séparés sans imbrication |
| **VPC endpoints limités** | Configuration réseau avancée non disponible | Services sur endpoints publics, sécurisés uniquement par IAM et TLS |
| **S3 CRR non configurable** | La réplication cross-région S3 nécessite des permissions IAM non disponibles avec `LabRole` | Deux buckets indépendants, aucune synchronisation automatique |
| **QuickSight indisponible** | Pas de dashboards natifs | Requêtes Athena manuelles via la console |
| **GuardDuty désactivé** | Pas de détection d'anomalies automatisée | — |
| **CloudTrail limité** | Audit des actions AWS non configurable | — |

---

## Delta actuel → production

### Failover et résilience

| | Actuel (Lab) | Production |
|---|---|---|
| **Mécanisme failover** | Proactif : Route 53 health checks HTTPS (intervalle 10s, seuil 1 échec) + polling simulateur toutes les 5s (démo) ou 30s (prod). Réactif : détection déconnexion MQTT → reconnexion sur région secondaire. | Route 53 Failover Policy + DNS automatique en 2–3 min — impossible car CNAME vers IoT Core incompatible avec mTLS/SNI |
| **RTO failover proactif** | ~10-15 secondes (health check 10s + poll simulateur 5s) | 2–3 min (DNS TTL 60s + détection 90s) |
| **Désactivation de cert IoT** | AWS IoT Core ne coupe **pas** les sessions MQTT actives immédiatement — l'existing session reste active jusqu'au keepalive (60s) ou cycle interne AWS (~5-10 min). Seules les nouvelles connexions sont rejetées. | Idem en production — préférer le failover proactif Route 53 pour les démos |
| **Réplication des données** | Aucune — les deux régions sont totalement indépendantes | S3 Cross-Region Replication (CRR) → RPO ~15 secondes |
| **Perte de données en cas de panne** | Données perdues pendant le basculement (~10s proactif / jusqu'à 10 min réactif) | Quasi-nulle (15s max, le temps du dernier flush Firehose non répliqué) |

### IAM et sécurité

| | Actuel (Lab) | Production |
|---|---|---|
| **Rôles IAM** | `LabRole` unique et partagé pour tous les services | Un rôle dédié par service (IoT → Firehose, Firehose → S3, Lambda exécution) avec permissions minimales |
| **Chiffrement au repos** | SSE-S3 (clés gérées par AWS) | SSE-KMS avec clés dédiées par usage (`ecosense-data-key`, `ecosense-stream-key`), rotation annuelle automatique |
| **Isolation réseau** | Endpoints publics | VPC Endpoints pour S3 (gratuit), Firehose, Athena — trafic interne AWS uniquement |
| **Audit** | CloudWatch Logs applicatifs uniquement | CloudTrail activé toutes régions, bucket d'audit immuable (S3 Object Lock), alarmes sur `AuthenticationFailures > 10/min` |
| **Détection d'anomalies** | Aucune | GuardDuty activé — détection exfiltration S3, comportements IAM anormaux |
| **IoT Policy** | Une policy par région, commune à tous les capteurs | Une policy par capteur, restreinte à son propre topic — compromission d'un capteur n'affecte pas les autres |
| **MFA** | Non applicable (lab) | MFA obligatoire sur tous les comptes IAM humains, AWS SSO pour gestion centralisée |

### Stockage et analytique

| | Actuel (Lab) | Production |
|---|---|---|
| **Format S3** | JSON compressé GZIP | Parquet (columnar) — 10–100x moins de données scannées par Athena |
| **Catalogage Glue** | Partition Projection statique (plage 2026–2030 hardcodée) | Glue Crawler hebdomadaire → découverte automatique des nouvelles partitions |
| **Visualisation** | Requêtes Athena manuelles | QuickSight — dashboards opérationnels (pollution par quartier, tendances, alertes) |
| **Versioning S3** | Désactivé | Activé + S3 Object Lock (Governance Mode) 30 jours |

### Alertes et observabilité

| | Actuel (Lab) | Production |
|---|---|---|
| **DLQ Lambda Flush** | `FlushSchedulerDLQ` capture les échecs de livraison EventBridge → Lambda, pas les crashes internes Lambda | DLQ configurée sur Lambda Flush elle-même (`on_failure=SqsDestination`) → capture tous les échecs |
| **Monitoring** | CloudWatch Logs + métriques basiques | Dashboard CloudWatch complet : `IoT.PublishIn.Success`, `Firehose.DeliveryToS3.DataFreshness`, alarme si `DataFreshness > 1800s` |
| **Format Firehose** | Buffer 60s / 5MB, GZIP | Buffer ajusté selon volume réel + Parquet via transformation Lambda inline |

---

## Bugs connus (non corrigés)

| # | Composant | Description | Impact |
|---|---|---|---|
| B-01 | `simulator/simulator_mqtt.py` — `is_region_healthy()` | La fonction lit les statuts bruts des observations Route 53 mais n'applique pas le flag `Inverted` du health check. Si un health check est inversé manuellement (démo), le simulateur ne voit pas le changement. | Le failover proactif via inversion de health check ne fonctionne pas — utiliser la désactivation de certificat à la place |

---

## Bugs corrigés (session 4 juin 2026)

| # | Composant | Description | Correction |
|---|---|---|---|
| F-01 | `simulator/simulator_mqtt.py` — `cmd_run()` | Lors d'un failover vers la région secondaire, `build_mqtt_client()` utilisait toujours les certificats de `us-east-1` (via `config.active_region`) au lieu de la région cible. La connexion à `us-west-2` avec des certs `us-east-1` retournait `rc=128`. | Ajout de `config.forced_region = other_region` avant l'appel à `build_mqtt_client()` dans le bloc de reconnexion |
| F-02 | `simulator/provision_certs.py` — `save_certificate_files()` | Le fichier `private.key` est créé en mode `400` (lecture seule). Avec `--force`, le script tentait de l'écraser sans le rendre modifiable au préalable → `PermissionError`. Cassait `make lab-restart`. | Ajout de `key_file.chmod(0o600)` avant l'écriture si le fichier existe déjà |
| F-03 | `.env` | `SECONDARY_REGION=us-east-2` alors que le stack `EcoSense-Secondary` est déployé en `us-west-2` — le simulateur tentait de se connecter à un endpoint IoT inexistant. | Correction de la variable en `us-west-2` |

---

## Améliorations identifiées sur l'implémentation actuelle

Ces points sont indépendants des contraintes AWS Academy.

### Priorité haute

| # | Composant | Amélioration |
|---|---|---|
| 1 | `lambdas/flush/handler.py` | Ajouter `on_failure=SqsDestination(dlq)` sur Lambda Flush dans CDK — les crashes internes (timeout, exception non catchée) ne sont actuellement pas capturés par la DLQ |
| 2 | `simulator/simulator_mqtt.py` — `is_region_healthy()` | Corriger le bug B-01 : lire le flag `Inverted` via `get_health_check()` et l'appliquer au résultat des observations |
| 3 | `iac/stacks/alert_aggregator_stack.py` | À la clôture du cycle (Flush sans nouvelles alertes), purger les `PendingAlerts` du quartier avec `batch_write_item` — évite que les alertes d'un incident passé parasitent l'historique d'un incident futur |

### Priorité moyenne

| # | Composant | Amélioration |
|---|---|---|
| 4 | `iac/stacks/storage_stack.py` | TTL `PendingAlerts` fixé à 24h sans lien avec `INTERVAL_MAX_SEC` (12h) — valeur plus rigoureuse : `INTERVAL_MAX_SEC + marge` |
| 5 | `Makefile` | Ajouter `make logs` pour afficher les derniers logs CloudWatch des Lambdas Ingest et Flush en une commande |
| 6 | `Makefile` | Ajouter `make dlq-status` pour afficher le nombre de messages dans les deux DLQ |
| 7 | `simulator/simulator_mqtt.py` | Le failover côté client ne gère pas le cas où les deux régions sont indisponibles simultanément — ajouter un circuit breaker avec arrêt propre après N tentatives |

### Priorité basse

| # | Composant | Amélioration |
|---|---|---|
| 8 | `docs/architecture-cible.md` | Les topics MQTT (`sensors/{sensor_id}/metrics`) ne correspondent pas à l'implémentation réelle (`metropole/{quartier}/{sensor_id}/telemetry`) — aligner la doc |

---

## Estimation coûts production

Voir [architecture-cible.md — §Considérations non-fonctionnelles](architecture-cible.md#considérations-non-fonctionnelles) pour le détail (~1 060 USD/mois double région).

L'implémentation Learner Lab tourne à coût quasi-nul (Free Tier + crédits académiques).
