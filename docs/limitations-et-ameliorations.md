# Limitations et améliorations — EcoSense

Ce document compare l'implémentation actuelle (contrainte AWS Academy Learner Lab) avec l'architecture cible production décrite dans `EcoSense_Architecture.md`.

---

## Contraintes AWS Academy Learner Lab

Ces limitations sont imposées par l'environnement et ne reflètent pas des choix d'architecture.

| Contrainte | Impact | Workaround actuel |
|---|---|---|
| **CDK bootstrap bloqué** | `cdk bootstrap` interdit, le bucket d'assets CDK doit être créé manuellement | `make bootstrap-bucket` recrée le bucket avant chaque déploiement |
| **LabRole imposé** | Impossible de créer des rôles IAM via CDK — tous les services utilisent le même rôle partagé `LabRole` | Référence hardcodée `arn:aws:iam::{account}:role/LabRole` dans tous les stacks |
| **Session qui expire** | À chaque restart du lab, les credentials et endpoints IoT changent | `make lab-restart` recrée le bucket CDK, redéploie et re-provisionne les certificats |
| **Route 53 domaine non enregistrable** | Impossible d'enregistrer un domaine DNS dans Learner Lab → pas de DNS failover MQTT (CNAME vers IoT Core impossible via mTLS/SNI) | Route 53 health checks HTTPS sur Lambda Function URLs. Le simulateur interroge l'API Route 53 (`get_health_check_status`) pour basculer proactivement. |
| **us-east-2 inaccessible** | `s3:CreateBucket` et `cloudformation:CreateStack` bloqués pour voclabs en us-east-2 | Région secondaire migrée sur `us-west-2` (S3, Lambda, IoT Core, CloudFormation tous disponibles) |
| **KMS customer-managed keys bloqué** | Impossible de créer des clés KMS dédiées | Chiffrement SSE-S3 (AWS managed keys) à la place |
| **NestedStack interdit** | Certains patterns CDK avancés ne sont pas supportés | Architecture à stacks séparés sans imbrication |
| **VPC endpoints limités** | Configuration réseau avancée non disponible | Services sur endpoints publics, sécurisés uniquement par IAM et TLS |
| **S3 CRR non configurable** | La réplication cross-région S3 nécessite des permissions IAM non disponibles | Deux buckets indépendants, aucune synchronisation automatique des données |
| **QuickSight indisponible** | Pas de dashboards natifs | Requêtes Athena manuelles via la console |
| **GuardDuty désactivé** | Pas de détection d'anomalies automatisée | — |
| **CloudTrail limité** | Audit des actions AWS non configurable | — |

---

## Delta actuel → production

### Failover et résilience

| | Actuel (Lab) | Production |
|---|---|---|
| **Mécanisme failover** | Proactif : Route 53 health checks HTTPS + polling simulateur toutes les 30s. Réactif : détection déconnexion MQTT → bascule immédiat. | Route 53 Failover Policy + DNS automatique en 2–3 min (RTO) — impossible car CNAME vers IoT Core incompatible avec mTLS/SNI |
| **Réplication des données** | Aucune — les deux régions sont totalement indépendantes | S3 Cross-Region Replication (CRR) → RPO ~15 secondes |
| **Perte de données en cas de panne** | Données perdues entre la détection Route 53 et le basculement (~30s max) | Quasi-nulle (15s max, le temps du dernier flush Firehose non répliqué) |

### IAM et sécurité

| | Actuel (Lab) | Production |
|---|---|---|
| **Rôles IAM** | `LabRole` unique et partagé pour tous les services | Un rôle dédié par service (IoT → SQS, IoT → Firehose, Firehose → S3, Lambda exécution) avec permissions minimales |
| **Chiffrement au repos** | SSE-S3 (clés gérées par AWS) | SSE-KMS avec clés dédiées par usage (`ecosense-data-key`, `ecosense-stream-key`, `ecosense-sns-key`), rotation annuelle automatique |
| **Isolation réseau** | Endpoints publics | VPC Endpoints pour S3 (gratuit), Firehose, Athena — trafic interne AWS uniquement |
| **Audit** | CloudWatch Logs applicatifs uniquement | CloudTrail activé toutes régions, bucket d'audit immuable (S3 Object Lock), alarmes sur `AuthenticationFailures > 10/min` |
| **Détection d'anomalies** | Aucune | GuardDuty activé — détection exfiltration S3, comportements IAM anormaux, credential compromise |
| **IoT Policy** | Une policy par région, commune à tous les capteurs | Une policy par capteur, restreinte à son propre topic (`sensors/{clientId}/metrics`) — compromission d'un capteur n'affecte pas les autres |
| **MFA** | Non applicable (lab) | MFA obligatoire sur tous les comptes IAM humains, AWS SSO pour gestion centralisée |

### Stockage et analytique

| | Actuel (Lab) | Production |
|---|---|---|
| **Format S3** | JSON compressé GZIP | Parquet (columnar) — 10–100x moins de données scannées par Athena, coût requêtes réduit d'autant |
| **Catalogage Glue** | Partition Projection statique (plage 2026–2030 hardcodée) | Glue Crawler hebdomadaire → découverte automatique des nouvelles partitions et évolutions de schéma |
| **Visualisation** | Requêtes Athena manuelles | QuickSight — dashboards opérationnels (pollution par quartier, tendances, alertes en temps réel) |
| **Versioning S3** | Désactivé | Activé + S3 Object Lock (Governance Mode) 30 jours → protection contre suppression accidentelle ou malveillante |

### Alertes et observabilité

| | Actuel (Lab) | Production |
|---|---|---|
| **DLQ Lambda Flush** | `FlushSchedulerDLQ` capture les échecs de livraison EventBridge → Lambda, pas les crashes internes Lambda | DLQ configurée sur Lambda Flush elle-même (`on_failure=SqsDestination`) → capture tous les échecs |
| **Monitoring** | CloudWatch Logs + métriques basiques | Dashboard CloudWatch complet : `IoT.PublishIn.Success`, `Firehose.DeliveryToS3.DataFreshness`, `SNS.NumberOfNotificationsFailed`, alarme si `DataFreshness > 1800s` |
| **Format Firehose** | Buffer 60s / 5MB, GZIP | Buffer ajusté selon volume réel + Parquet via transformation Lambda inline |

---

## Améliorations identifiées sur l'implémentation actuelle

Ces points sont indépendants des contraintes AWS Academy et pourraient être traités dans le périmètre du projet.

### Priorité haute

| # | Composant | Amélioration |
|---|---|---|
| 1 | `lambdas/flush/handler.py` | Ajouter `on_failure=SqsDestination(dlq)` sur Lambda Flush dans CDK — les crashes internes (timeout, exception non catchée) ne sont actuellement pas capturés |
| 2 | `iac/stacks/alert_aggregator_stack.py` | À la clôture du cycle (Flush sans nouvelles alertes), purger les `PendingAlerts` du quartier avec `batch_write_item` — évite que les alertes d'un incident passé parasitent l'`HISTORIQUE` d'un incident futur |
| 3 | `iac/stacks/storage_stack.py` | TTL `PendingAlerts` actuellement fixé à 24h sans lien avec `INTERVAL_MAX_SEC` (12h) — une alerte peut persister dans `HISTORIQUE` longtemps après la clôture du cycle. Valeur plus rigoureuse : `INTERVAL_MAX_SEC + marge` |

### Priorité moyenne

| # | Composant | Amélioration |
|---|---|---|
| 4 | `docs/scripts/archi.py` | Régénérer `docs/img/ecosense_archi.png` après chaque modification du schéma (`python docs/scripts/archi.py`) |
| 5 | `Makefile` | Ajouter `make logs` pour afficher les derniers logs CloudWatch des Lambdas Ingest et Flush en une commande |
| 6 | `Makefile` | Ajouter `make dlq-status` pour afficher le nombre de messages dans les deux DLQ en un coup |
| 7 | Simulateur | Le failover côté client ne gère pas le cas où les deux régions sont indisponibles simultanément — ajouter un circuit breaker avec arrêt propre après N tentatives |

### Priorité basse / polish

| # | Composant | Amélioration |
|---|---|---|
| 8 | `PlaningEquipe.md` | Les entrées du 2 au 5 juin sont à compléter par chaque membre |
| 9 | `EcoSense_Architecture.md` | Les topics MQTT de ce document (`sensors/{sensor_id}/metrics`) ne correspondent pas à l'implémentation réelle (`metropole/{quartier}/{sensor_id}/telemetry`) — à aligner |
| 10 | `EcoSense_Architecture.md` | La section Firehose mentionne le format Parquet — l'implémentation actuelle utilise JSON GZIP. À noter explicitement comme delta dans ce doc |

---

## Estimation coûts production

Voir [architecture-cible.md — §Considérations non-fonctionnelles](architecture-cible.md#considérations-non-fonctionnelles) pour le détail (~1 780 USD/mois double région).

L'implémentation Learner Lab tourne à coût quasi-nul (Free Tier + crédits académiques).
