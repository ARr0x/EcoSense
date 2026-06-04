# Runbooks opérationnels — EcoSense

| # | Runbook | Scénario |
|---|---|---|
| [RB-ES-001](#rb-es-001--basculement-vers-la-région-secondaire) | Basculement région secondaire | Panne ou simulation de perte de us-east-1 |
| [RB-ES-002](#rb-es-002--certificat-iot-core-invalide-ou-manquant) | Certificat IoT invalide | Simulateur ne se connecte plus (rc=128 / rc=5) |
| [RB-ES-003](#rb-es-003--données-non-archivées-firehose--s3-silencieux) | Firehose / S3 silencieux | Données publiées mais absentes de S3 |
| [RB-ES-004](#rb-es-004--alertes-critical-non-reçues) | SNS muet | Messages CRITICAL publiés, aucun email reçu |
| [RB-ES-005](#rb-es-005--redéploiement-complet-après-lab-restart) | Lab restart | Credentials expirés, redéploiement complet |

---

## RB-ES-001 — Basculement vers la région secondaire

### 1. Informations générales

- **ID du Runbook** : RB-ES-001
- **Version** : 1.0
- **Auteur / Équipe** : Groupe 4 — Chapot, Lacombe, Nicoud
- **Description** : Basculer manuellement le flux de télémétrie IoT de la région primaire `us-east-1` vers la région secondaire `us-west-2` en cas de panne ou pour un crash-test jury.
- **Régions AWS** : `us-east-1` (primaire) → `us-west-2` (secondaire)
- **Rôle / Permissions IAM** : `LabRole` — accès en lecture/écriture Route 53, IoT Core
- **Outils requis** : AWS CLI configuré, Python 3.11+, `.env` à jour

### 2. Prérequis

- Certificats X.509 provisionnés pour les deux régions (`simulator/certs/us-east-1/` et `simulator/certs/us-west-2/`)
- Variables `.env` renseignées : `IOT_ENDPOINT_PRIMARY`, `IOT_ENDPOINT_SECONDARY`, `ROUTE53_HC_ID_PRIMARY`
- Simulateur en cours d'exécution sur `us-east-1` (`make run`)

```bash
# Définir les variables de session
HC_ID_PRIMARY=$(grep ROUTE53_HC_ID_PRIMARY .env | cut -d= -f2)
```

### 3. Environnement & ressources cibles

- **Stack CDK** : `EcoSense-Primary` (us-east-1), `EcoSense-Secondary` (us-west-2)
- **Health Check Route 53 primaire** : lu depuis `.env` → `ROUTE53_HC_ID_PRIMARY`
- **Endpoint IoT primaire** : `a25vwbkbhsb4tz-ats.iot.us-east-1.amazonaws.com`
- **Endpoint IoT secondaire** : `a25vwbkbhsb4tz-ats.iot.us-west-2.amazonaws.com`

### 4. Procédure étape par étape

#### Étape 1 : Vérification de l'état initial (pré-check)

```bash
# Vérifier que le simulateur est bien connecté à us-east-1
# La sortie doit afficher : Région=us-east-1
make run  # Observer les logs "Salve #XXX | Région=us-east-1"

# Vérifier le statut du health check Route 53
aws route53 get-health-check-status --health-check-id $HC_ID_PRIMARY \
  --query 'HealthCheckObservations[0].StatusReport.Status' --output text
```

**Attendu** : Statut `Success: HTTP Status Code 200`

#### Étape 2 : Déclenchement du basculement

```bash
# Inverser le health check primaire → marque us-east-1 comme UNHEALTHY
aws route53 update-health-check \
  --health-check-id $HC_ID_PRIMARY \
  --inverted
```

#### Étape 3 : Observation du basculement automatique

Observer les logs du simulateur. Dans les 5 à 10 secondes :

```
[WARNING] [Route53] us-east-1 signalé UNHEALTHY — basculement proactif avant déconnexion MQTT
[WARNING] Basculement vers us-west-2
[INFO] Connecté à IoT Core (us-west-2)
```

#### Étape 4 : Post-check

```bash
# Vérifier que les données arrivent bien en us-west-2
aws s3 ls s3://ecosense-archives-825804607046-us-west-2/raw/ --recursive \
  | sort | tail -5
```

**Attendu** : Nouveaux fichiers horodatés dans le bucket us-west-2 dans les 2 minutes (buffer Firehose 60 s).

### 5. Procédure de rollback

```bash
# Annuler l'inversion → us-east-1 redevient healthy
aws route53 update-health-check \
  --health-check-id $HC_ID_PRIMARY \
  --no-inverted

# Redémarrer le simulateur pour reprendre sur us-east-1
# Ctrl+C puis :
make run
```

---

## RB-ES-002 — Certificat IoT Core invalide ou manquant

### 1. Informations générales

- **ID du Runbook** : RB-ES-002
- **Version** : 1.0
- **Auteur / Équipe** : Groupe 4 — Chapot, Lacombe, Nicoud
- **Description** : Résoudre les échecs de connexion MQTT dus à un certificat X.509 désactivé, manquant ou incohérent.
- **Région AWS** : `us-east-1` et/ou `us-west-2`
- **Rôle / Permissions IAM** : `LabRole` — accès IoT Core (`iot:UpdateCertificate`, `iot:CreateKeysAndCertificate`)
- **Outils requis** : AWS CLI, Python 3.11+, `.venv` activé

### 2. Prérequis

- Accès AWS CLI avec credentials valides
- Dépôt cloné, `.venv` activé (`source .venv/bin/activate`)

```bash
CERT_ID=$(python3 -c "import json; print(json.load(open('simulator/certs/us-east-1/metadata.json'))['certificate_id'])")
```

### 3. Environnement & ressources cibles

- **Certificat IoT primaire** : `simulator/certs/us-east-1/metadata.json` → champ `certificate_id`
- **Certificat IoT secondaire** : `simulator/certs/us-west-2/metadata.json` → champ `certificate_id`
- **Policy IoT** : `EcoSenseSimulatorPolicy-us-east-1` / `EcoSenseSimulatorPolicy-us-west-2`

### 4. Procédure étape par étape

#### Étape 1 : Vérification de l'état initial (pré-check)

```bash
# Symptôme typique dans les logs :
# [WARNING] Déconnexion inattendue (rc=128)
# [ERROR] Impossible de se connecter

# Vérifier le statut du certificat
aws iot describe-certificate --certificate-id $CERT_ID \
  --region us-east-1 \
  --query 'certificateDescription.status' --output text
```

**Attendu** : `ACTIVE`. Si `INACTIVE` → Étape 2A. Si fichiers absents → Étape 2B.

#### Étape 2A : Certificat INACTIVE — réactivation

```bash
aws iot update-certificate \
  --certificate-id $CERT_ID \
  --new-status ACTIVE \
  --region us-east-1
```

#### Étape 2B : Fichiers absents ou incohérents — reprovision

```bash
source .venv/bin/activate
make certs-force
```

#### Étape 3 : Post-check

```bash
make check
```

**Attendu** :
```
[INFO] Connecté à IoT Core (rc=0)
[INFO] TLS handshake OK
```

### 5. Procédure de rollback

Sans objet — la réactivation ou le reprovision sont idempotents. En cas d'échec du reprovision, vérifier que les credentials AWS sont valides (`aws sts get-caller-identity`).

---

## RB-ES-003 — Données non archivées (Firehose / S3 silencieux)

### 1. Informations générales

- **ID du Runbook** : RB-ES-003
- **Version** : 1.0
- **Auteur / Équipe** : Groupe 4 — Chapot, Lacombe, Nicoud
- **Description** : Diagnostiquer et résoudre l'absence de données dans S3 alors que le simulateur publie normalement.
- **Région AWS** : `us-east-1`
- **Rôle / Permissions IAM** : `LabRole` — IoT Core, Kinesis Firehose, CloudWatch, S3
- **Outils requis** : AWS CLI

### 2. Prérequis

- Simulateur actif et publiant (`make run`)
- Attendre au minimum 5 minutes après le démarrage avant de diagnostiquer (buffer Firehose 60 s + latence CloudWatch)

### 3. Environnement & ressources cibles

- **Topic Rule** : `ecosense_archive_rule` (us-east-1)
- **Firehose** : `ecosense-delivery-us-east-1`
- **Bucket S3** : `ecosense-archives-825804607046-us-east-1`

### 4. Procédure étape par étape

#### Étape 1 : Vérification de l'état initial (pré-check)

```bash
# Vérifier que la Topic Rule est active
aws iot get-topic-rule --rule-name ecosense_archive_rule \
  --region us-east-1 --query 'rule.ruleDisabled'
```

**Attendu** : `false`. Si `true` → Étape 2A.

```bash
# Vérifier les métriques IoT Rule (messages routés)
aws cloudwatch get-metric-statistics \
  --namespace AWS/IoT --metric-name TopicMatch \
  --dimensions Name=RuleName,Value=ecosense_archive_rule \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 600 --statistics Sum --region us-east-1
```

**Attendu** : valeur > 0.

#### Étape 2A : Topic Rule désactivée

```bash
aws iot enable-topic-rule --rule-name ecosense_archive_rule --region us-east-1
```

#### Étape 2B : Firehose en erreur — redéploiement

```bash
source .venv/bin/activate
cdk deploy EcoSense-Primary
```

#### Étape 3 : Post-check

```bash
# Injecter un message de test et attendre 90 secondes
aws iot-data publish \
  --topic "metropole/centre/S-TEST/telemetry" \
  --payload '{"sensor_id":"S-TEST","metric":"CO2","value":500,"unit":"ppm","status":"NORMAL","region":"us-east-1","quartier":"centre","timestamp":0}' \
  --cli-binary-format raw-in-base64-out --region us-east-1

sleep 90

aws s3 ls s3://ecosense-archives-825804607046-us-east-1/raw/ --recursive | sort | tail -5
```

**Attendu** : Nouveaux objets S3 horodatés.

### 5. Procédure de rollback

La réactivation de la Topic Rule est immédiate. Si le redéploiement CDK échoue, vérifier les credentials et relancer `make lab-restart`.

---

## RB-ES-004 — Alertes CRITICAL non reçues

### 1. Informations générales

- **ID du Runbook** : RB-ES-004
- **Version** : 1.0
- **Auteur / Équipe** : Groupe 4 — Chapot, Lacombe, Nicoud
- **Description** : Résoudre l'absence d'emails d'alerte lors de la publication de messages `status=CRITICAL`.
- **Région AWS** : `us-east-1`
- **Rôle / Permissions IAM** : `LabRole` — IoT Core, SNS
- **Outils requis** : AWS CLI, accès à la boîte email `mateo.nicoud@ynov.com`

### 2. Prérequis

- Le simulateur publie des messages avec `status=CRITICAL` (augmenter `CRITICAL_RATE` si besoin)
- Abonnement SNS créé lors du `cdk deploy`

### 3. Environnement & ressources cibles

- **Topic Rule** : `ecosense_alert_rule` (us-east-1)
- **Topic SNS** : `ecosense-alert-us-east-1`
- **Abonné** : `mateo.nicoud@ynov.com`

### 4. Procédure étape par étape

#### Étape 1 : Vérification de l'état initial (pré-check)

```bash
TOPIC_ARN=$(aws sns list-topics --region us-east-1 \
  --query "Topics[?contains(TopicArn,'ecosense-alert')].TopicArn" --output text)

# Vérifier l'abonnement email
aws sns list-subscriptions-by-topic --topic-arn $TOPIC_ARN --region us-east-1 \
  --query 'Subscriptions[*].{Email:Endpoint,Statut:SubscriptionArn}' --output table
```

**Attendu** : statut différent de `PendingConfirmation`. Si `PendingConfirmation` → Étape 2A.

```bash
# Vérifier la Topic Rule d'alerte
aws iot get-topic-rule --rule-name ecosense_alert_rule \
  --region us-east-1 --query 'rule.ruleDisabled'
```

**Attendu** : `false`. Si `true` → Étape 2B.

#### Étape 2A : Abonnement email non confirmé

```bash
aws sns subscribe --topic-arn $TOPIC_ARN \
  --protocol email --notification-endpoint mateo.nicoud@ynov.com \
  --region us-east-1
# → Ouvrir la boîte email et cliquer "Confirm subscription"
```

#### Étape 2B : Topic Rule désactivée

```bash
aws iot enable-topic-rule --rule-name ecosense_alert_rule --region us-east-1
```

#### Étape 3 : Post-check

```bash
# Publier un message CRITICAL de test
aws iot-data publish \
  --topic "metropole/centre/S-TEST/telemetry" \
  --payload '{"sensor_id":"S-TEST","metric":"CO2","value":1500,"unit":"ppm","status":"CRITICAL","region":"us-east-1","quartier":"centre","timestamp":0}' \
  --cli-binary-format raw-in-base64-out --region us-east-1
```

**Attendu** : Email d'alerte reçu dans les 2 minutes.

### 5. Procédure de rollback

Sans objet — les opérations sont sans effet de bord destructeur.

---

## RB-ES-005 — Redéploiement complet après lab restart

### 1. Informations générales

- **ID du Runbook** : RB-ES-005
- **Version** : 1.0
- **Auteur / Équipe** : Groupe 4 — Chapot, Lacombe, Nicoud
- **Description** : Remettre en service l'intégralité de l'infrastructure EcoSense après un redémarrage du Learner Lab (credentials expirés, bucket CDK disparu).
- **Région AWS** : `us-east-1` et `us-west-2`
- **Rôle / Permissions IAM** : `LabRole`
- **Outils requis** : AWS CLI, Python 3.11+, `.venv`, accès à la console Learner Lab

### 2. Prérequis

- Accès à la console AWS Academy → "AWS Details" pour copier les nouveaux credentials
- Dépôt Git cloné et `.env` configuré

### 3. Environnement & ressources cibles

- **Stacks CDK** : `EcoSense-Primary`, `EcoSense-Secondary`, `EcoSense-Route53`
- **Buckets CDK** : `ecosense-cdk-825804607046-us-east-1`, `ecosense-cdk-825804607046-us-west-2`

### 4. Procédure étape par étape

#### Étape 1 : Vérification de l'état initial (pré-check)

```bash
aws sts get-caller-identity
```

**Attendu** : Réponse JSON avec `Account: 825804607046`. Si `ExpiredTokenException` → Étape 2.

#### Étape 2 : Mise à jour des credentials

Copier les credentials depuis la console Learner Lab > "AWS Details" vers `~/.aws/credentials`.

#### Étape 3 : Redéploiement complet

```bash
source .venv/bin/activate
make lab-restart
```

Cette commande exécute en séquence : recréation du bucket CDK → `cdk deploy --all` → reprovision des certificats X.509 pour les deux régions.

#### Étape 4 : Vérification des endpoints IoT

```bash
make iot-endpoint
# Comparer avec les valeurs IOT_ENDPOINT_* dans .env
# Si différentes, mettre à jour .env manuellement
```

#### Étape 5 : Post-check

```bash
make check
```

**Attendu** :
```
[INFO] Connecté à IoT Core (rc=0)  ← us-east-1
[INFO] TLS handshake OK
[INFO] Connecté à IoT Core (rc=0)  ← us-west-2
[INFO] TLS handshake OK
```

### 5. Procédure de rollback

Si `cdk deploy` échoue sur un stack :

```bash
# Déployer uniquement le stack en erreur
cdk deploy EcoSense-Primary
# ou
cdk deploy EcoSense-Secondary

# Consulter les événements CloudFormation pour identifier la cause
aws cloudformation describe-stack-events \
  --stack-name EcoSense-Primary --region us-east-1 \
  --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
  --output table
```
