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
| **Route 53 failover DNS impossible** | Trois blocages cumulés empêchent le DNS failover natif (voir détail ci-dessous) | Route 53 health checks HTTPS sur Lambda Function URLs proxy. Le simulateur interroge `GetHealthCheckStatus` pour basculer proactivement |
| **us-east-2 inaccessible** | `s3:CreateBucket` et `cloudformation:CreateStack` bloqués pour `voclabs` en us-east-2 (contrairement aux consignes) | Région secondaire déployée sur `us-west-2` — tous les services nécessaires disponibles |
| **KMS customer-managed keys bloqué** | Impossible de créer des clés KMS dédiées | Chiffrement SSE-S3 (AWS managed keys) |
| **NestedStack interdit** | Certains patterns CDK avancés ne sont pas supportés | Architecture à stacks séparés sans imbrication |
| **VPC endpoints limités** | Configuration réseau avancée non disponible | Services sur endpoints publics, sécurisés uniquement par IAM et TLS |
| **S3 CRR non configurable** | La réplication cross-région S3 nécessite des permissions IAM non disponibles avec `LabRole` | Deux buckets indépendants, aucune synchronisation automatique |
| **QuickSight indisponible** | Pas de dashboards natifs | Requêtes Athena manuelles via la console |
| **GuardDuty désactivé** | Pas de détection d'anomalies automatisée | — |
| **CloudTrail limité** | Audit des actions AWS non configurable | — |

---

## Pourquoi le failover DNS Route 53 est impossible sur IoT Core

### Ce que serait le failover idéal

En production standard, Route 53 Failover Policy bascule automatiquement le DNS :

```
Capteurs  ──DNS──►  iot.ecosense.example.com
                          │
                    Route 53 Failover Policy
                    ┌─────┴──────┐
                    ▼            ▼ (si primary UNHEALTHY)
             PRIMARY            SECONDARY
      iot.us-east-1.aws    iot.us-west-2.aws
```

Quand Route 53 détecte que la région primaire est indisponible, il met à jour l'enregistrement DNS. Les capteurs, à leur prochaine reconnexion, obtiennent automatiquement l'IP de la région secondaire — sans aucun changement côté client. RTO : 2-3 minutes.

### Blocage n°1 — Incompatibilité mTLS / SNI

IoT Core utilise **mTLS** (mutual TLS) : le serveur s'authentifie avec un certificat TLS émis par Amazon pour le domaine exact `*.iot.us-east-1.amazonaws.com`. Voici ce qui se passe si on place un CNAME Route 53 devant :

```
Étape 1 — Résolution DNS
  Capteur demande : iot.ecosense.example.com
  Route 53 répond : xxx.iot.us-east-1.amazonaws.com   ✓

Étape 2 — Handshake TLS (champ SNI)
  Capteur annonce dans le SNI : "iot.ecosense.example.com"
  Serveur IoT Core présente   : certificat "*.iot.us-east-1.amazonaws.com"

Étape 3 — Vérification TLS côté client
  SNI ≠ certificat  →  TLS HANDSHAKE FAILED  →  connexion impossible  ✗
```

Le SNI (Server Name Indication) est l'extension TLS qui indique au serveur quel certificat présenter. Si le nom annoncé par le client ne correspond pas au certificat du serveur, la connexion est rejetée. Ce n'est pas un bug — c'est la sécurité TLS qui fonctionne correctement.

### Blocage n°2 — Pas de domaine personnalisé disponible

AWS IoT Core supporte les **custom domains** : on peut configurer `iot.ecosense.example.com` comme endpoint officiel avec un certificat TLS valide. Le CNAME Route 53 fonctionnerait alors. Mais cette fonctionnalité requiert trois étapes, toutes bloquées :

| Étape | Prérequis | Blocage Learner Lab |
|---|---|---|
| 1. Enregistrer un domaine | Route 53 Domain Registration | **Interdit** en AWS Academy |
| 2. Créer un certificat ACM | Domaine valide + validation DNS/email | Impossible sans domaine |
| 3. Créer une IoT Domain Configuration | Domaine + certificat ACM | Impossible sans étapes 1 et 2 |

### Blocage n°3 — Restrictions IAM

Même avec un domaine disponible, configurer un custom domain IoT Core nécessite des **actions IAM** (`iot:CreateDomainConfiguration`, `iot:UpdateDomainConfiguration`) qui requièrent un rôle dédié. En AWS Academy, la création de rôles IAM personnalisés est strictement interdite — seul `LabRole` est utilisable, et ces permissions n'y figurent pas.

### Récapitulatif

| Blocage | Raison technique | Levable hors Learner Lab ? |
|---|---|---|
| SNI mismatch mTLS | Protocole TLS standard | Oui, avec custom domain IoT Core |
| Pas de domaine enregistrable | Restriction AWS Academy | Oui (~13 USD/an sur Route 53) |
| Certificat ACM impossible | Dépend du domaine | Oui, automatique avec ACM |
| IoT Domain Configuration bloquée | Création de rôles IAM interdite | Oui, avec un rôle dédié |

### Ce qui est implémenté à la place

Route 53 est utilisé comme **registre de santé distribué** — pas comme DNS. Ses 15 points de présence mondiaux sondent une Lambda Function URL HTTPS toutes les 10 secondes. Le simulateur interroge l'API Route 53 (`GetHealthCheckStatus`) et bascule lui-même son endpoint MQTT. C'est du **failover applicatif côté client**.

```
  Route 53 health checkers (×15 régions AWS)
          │  HTTPS GET /  toutes les 10 s
          ▼
  Lambda ecosense-health-{region}  →  {"status": "ok"}
  (proxy de santé — ne touche pas IoT Core)

  Simulateur  ──boto3──►  Route 53 API  (GetHealthCheckStatus, toutes les 5 s)
       │                       │
       │              retourne Healthy / UNHEALTHY
       │
       ├─ HEALTHY   ──MQTT/mTLS──►  IoT Core us-east-1  (port 8883)
       └─ UNHEALTHY ──MQTT/mTLS──►  IoT Core us-west-2  (port 8883)
```

### Déclencher le failover (démo)

```bash
HC_ID_PRIMARY=$(grep ROUTE53_HC_ID_PRIMARY .env | cut -d= -f2)

# Basculement proactif (~10 s)
aws route53 update-health-check --health-check-id $HC_ID_PRIMARY --inverted

# Rétablissement
aws route53 update-health-check --health-check-id $HC_ID_PRIMARY --no-inverted
```

> **Alternative lente (~5-10 min)** : désactiver le certificat IoT Core (`aws iot update-certificate --new-status INACTIVE`). AWS IoT ne coupe pas les sessions MQTT actives immédiatement — la vérification du certificat n'intervient qu'à la prochaine tentative de connexion.

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
| ~~B-01~~ | ~~`simulator/simulator_mqtt.py` — `is_region_healthy()`~~ | Corrigé — `get_health_check()` est maintenant appelé pour lire le flag `Inverted` et l'appliquer au résultat. | — |

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
| 2 | `iac/stacks/alert_aggregator_stack.py` | À la clôture du cycle (Flush sans nouvelles alertes), purger les `PendingAlerts` du quartier avec `batch_write_item` — évite que les alertes d'un incident passé parasitent l'historique d'un incident futur |

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
