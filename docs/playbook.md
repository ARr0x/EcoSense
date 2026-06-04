# Playbooks opérationnels — EcoSense

| # | Playbook | Scénario |
|---|---|---|
| [PB-ES-001](#pb-es-001--pipeline-silencieux--perte-de-messages) | Pipeline silencieux | Des messages IoT sont publiés mais rien n'arrive ni dans S3 ni en email |
| [PB-ES-002](#pb-es-002--avalanche-dalertes--spam-ou-silence-email) | Avalanche d'alertes | Un pic de pollution est détecté mais les emails sont absents ou en spam |
| [PB-ES-003](#pb-es-003--indisponibilité-régionale) | Indisponibilité régionale | La région primaire `us-east-1` est suspectée hors service |

---

## PB-ES-001 — Pipeline silencieux / perte de messages

### 1. Informations générales

- **ID du Playbook** : PB-ES-001
- **Version** : 1.0
- **Auteur / Équipe** : Groupe 4 — Chapot, Lacombe, Nicoud
- **Type d'incident** : Données silencieuses — le simulateur publie, mais S3 reste vide et aucun email n'est envoyé malgré des messages `CRITICAL`
- **Symptôme initial** : Alerte CloudWatch `ecosense-health-errors-us-east-1` déclenchée, ou constat visuel de l'absence de nouveaux fichiers S3 après 5 minutes de simulation

### 2. Rôles et responsabilités

| Rôle | Responsabilité |
|---|---|
| **Incident Commander (IC)** | Pilote le playbook, décide des actions, suit le chrono |
| **Ops/Tech Lead** | Exécute les commandes AWS CLI et lit les métriques CloudWatch |
| **Communications** | Informe le professeur si l'incident dépasse 15 minutes sans résolution |

### 3. Phase 1 — Triage et vérification de l'alerte

Avant d'agir, **confirmer que le simulateur publie réellement**.

```bash
# 1. Vérifier que le simulateur tourne et se connecte bien
make run
# Attendu dans les logs :
# [INFO] Connecté à IoT Core (us-east-1)
# [INFO] Salve #1 | 10 messages | Région=us-east-1
```

```bash
# 2. Mesurer le trafic entrant sur IoT Core (dernières 10 minutes)
aws cloudwatch get-metric-statistics \
  --namespace AWS/IoT \
  --metric-name PublishIn.Success \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 600 --statistics Sum \
  --region us-east-1
```

**Décision :**
- Si `PublishIn.Success = 0` → Le problème est côté simulateur ou certificat. Appliquer **RB-ES-002**.
- Si `PublishIn.Success > 0` → IoT Core reçoit les messages. Passer à la **Phase 2**.

### 4. Phase 2 — Diagnostic et arbre de décision

Exécuter les vérifications dans l'ordre suivant.

#### Étape A — Vérifier les Topic Rules

```bash
# Vérifier que les deux Topic Rules ne sont pas désactivées
aws iot get-topic-rule --rule-name ecosense_archive_rule \
  --region us-east-1 --query 'rule.ruleDisabled'

aws iot get-topic-rule --rule-name ecosense_alert_rule \
  --region us-east-1 --query 'rule.ruleDisabled'
```

- Si une valeur est `true` → La Topic Rule est désactivée. Passer à l'**Action 1**.
- Si les deux sont `false` → Passer à l'**Étape B**.

#### Étape B — Vérifier le routage effectif (TopicMatch)

```bash
# Compter les messages routés par chaque règle
for RULE in ecosense_archive_rule ecosense_alert_rule; do
  echo "--- $RULE ---"
  aws cloudwatch get-metric-statistics \
    --namespace AWS/IoT --metric-name TopicMatch \
    --dimensions Name=RuleName,Value=$RULE \
    --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
    --period 600 --statistics Sum --region us-east-1
done
```

- Si `TopicMatch = 0` sur `ecosense_archive_rule` → Les messages ne passent pas le filtre SQL. Vérifier le topic MQTT dans les logs du simulateur (format attendu : `metropole/{quartier}/{sensor_id}/telemetry`).
- Si `TopicMatch > 0` mais S3 vide → Firehose est le problème. Passer à l'**Étape C**.
- Si `TopicMatch > 0` sur `ecosense_alert_rule` mais pas d'email → Passer à l'**Étape D**.

#### Étape C — Vérifier Kinesis Firehose

```bash
# Taux de livraison S3 (dernières 10 minutes)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Firehose \
  --metric-name DeliveryToS3.Success \
  --dimensions Name=DeliveryStreamName,Value=ecosense-delivery-us-east-1 \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 600 --statistics Sum --region us-east-1

# Vérifier les erreurs dans les logs Firehose
aws logs filter-log-events \
  --log-group-name /ecosense/firehose/us-east-1 \
  --start-time $(date -d '10 minutes ago' +%s)000 \
  --region us-east-1 --query 'events[*].message' --output text
```

- Si `DeliveryToS3.Success = 0` et des erreurs apparaissent → Passer à l'**Action 2**.
- Si Firehose est sain mais S3 semble vide → Le buffer n'a pas encore expiré (attendre jusqu'à 60 s). Passer à l'**Étape D** en parallèle.

#### Étape D — Vérifier Lambda Ingest et la queue SQS

```bash
# Vérifier les erreurs récentes de Lambda Ingest
aws logs filter-log-events \
  --log-group-name /aws/lambda/ecosense-ingest-us-east-1 \
  --start-time $(date -d '5 minutes ago' +%s)000 \
  --filter-pattern "ERROR" \
  --region us-east-1 --query 'events[*].message' --output text

# Vérifier les messages en attente dans la SQS principale
aws sqs get-queue-attributes \
  --queue-url $(aws sqs get-queue-url --queue-name ecosense-alerts-us-east-1 \
    --region us-east-1 --query 'QueueUrl' --output text) \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --region us-east-1

# Vérifier la DLQ (messages en échec irrémédiable)
aws sqs get-queue-attributes \
  --queue-url $(aws sqs get-queue-url --queue-name ecosense-alerts-dlq-us-east-1 \
    --region us-east-1 --query 'QueueUrl' --output text) \
  --attribute-names ApproximateNumberOfMessages \
  --region us-east-1
```

- Si des messages sont dans la **DLQ** → Les messages ont échoué 3 fois. Consulter les logs Lambda pour la cause (JSON invalide, quota DynamoDB, etc.). Passer à l'**Action 3**.
- Si la queue accumule mais Lambda n'est pas en erreur → Vérifier le mapping SQS→Lambda (voir Action 3).

### 5. Phase 3 — Actions de résolution

#### Action 1 — Réactiver une Topic Rule désactivée

```bash
# Réactiver la règle concernée (remplacer le nom si besoin)
aws iot enable-topic-rule --rule-name ecosense_archive_rule --region us-east-1
aws iot enable-topic-rule --rule-name ecosense_alert_rule --region us-east-1
```

Attendre 2 minutes puis vérifier les métriques `TopicMatch`.

#### Action 2 — Redéployer le stack Storage (Firehose en erreur)

```bash
source .venv/bin/activate
cdk deploy EcoSense-Primary --exclusively
```

Attendre la fin du déploiement, puis injecter un message de test et patienter 90 secondes (buffer Firehose) :

```bash
aws iot-data publish \
  --topic "metropole/centre/S-TEST/telemetry" \
  --payload '{"sensor_id":"S-TEST","metric":"CO2","value":500,"unit":"ppm","status":"NORMAL","region":"us-east-1","quartier":"centre","timestamp":0}' \
  --cli-binary-format raw-in-base64-out --region us-east-1

sleep 90
aws s3 ls s3://ecosense-archives-$(aws sts get-caller-identity --query Account --output text)-us-east-1/raw/ \
  --recursive | sort | tail -5
```

#### Action 3 — Diagnostiquer et purger la DLQ

```bash
# Lire un message de la DLQ pour comprendre la cause
aws sqs receive-message \
  --queue-url $(aws sqs get-queue-url --queue-name ecosense-alerts-dlq-us-east-1 \
    --region us-east-1 --query 'QueueUrl' --output text) \
  --region us-east-1

# Une fois la cause corrigée, purger la DLQ (messages définitivement perdus)
aws sqs purge-queue \
  --queue-url $(aws sqs get-queue-url --queue-name ecosense-alerts-dlq-us-east-1 \
    --region us-east-1 --query 'QueueUrl' --output text) \
  --region us-east-1
```

### 6. Phase 4 — Procédure d'escalade

Si le problème n'est pas résolu après **15 minutes** d'application des actions ci-dessus :

1. Rassembler les logs d'erreur CloudWatch des groupes concernés :
   - `/ecosense/iot/errors/alert/us-east-1`
   - `/ecosense/iot/errors/archive/us-east-1`
   - `/aws/lambda/ecosense-ingest-us-east-1`
2. Ouvrir la console AWS CloudFormation → stack `EcoSense-Primary` → onglet **Events** pour identifier une ressource en `UPDATE_FAILED`.
3. Contacter l'administrateur principal avec :
   - L'heure de début de l'incident
   - Les actions du playbook déjà tentées
   - Les extraits de logs pertinents

### 7. Phase 5 — Post-incident (rétrospective)

| Champ | À remplir |
|---|---|
| Heure de début | __ |
| Heure de résolution | __ |
| Cause racine | _(ex: Topic Rule désactivée manuellement par erreur / Firehose throttlé)_ |
| Données perdues | _(nombre de messages non archivés, fenêtre temporelle)_ |
| Action préventive | _(ex: ajouter alarme CloudWatch sur TopicMatch = 0 pendant 5 min)_ |

---

## PB-ES-002 — Avalanche d'alertes / spam ou silence email

### 1. Informations générales

- **ID du Playbook** : PB-ES-002
- **Version** : 1.0
- **Auteur / Équipe** : Groupe 4 — Chapot, Lacombe, Nicoud
- **Type d'incident** : Incident alerting — pics de pollution détectés dans CloudWatch mais aucun email reçu, **ou** boîte email inondée de doublons sans agrégation
- **Symptôme initial** : Des messages `status=CRITICAL` apparaissent dans les métriques IoT mais aucun email dans la boîte `ALERT_EMAIL` après 5 minutes, ou au contraire réception de centaines d'emails identiques

### 2. Rôles et responsabilités

| Rôle | Responsabilité |
|---|---|
| **Incident Commander (IC)** | Pilote le playbook, décide des actions |
| **Ops/Tech Lead** | Exécute les commandes AWS CLI |
| **Communications** | Informe les équipes opérationnelles en cas de silence alerting prolongé |

### 3. Phase 1 — Triage et vérification de l'alerte

```bash
# 1. Confirmer que des messages CRITICAL sont bien publiés
aws cloudwatch get-metric-statistics \
  --namespace AWS/IoT --metric-name TopicMatch \
  --dimensions Name=RuleName,Value=ecosense_alert_rule \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 600 --statistics Sum --region us-east-1
```

- Si `TopicMatch = 0` → Le problème vient de la Topic Rule ou du filtre CRITICAL. Appliquer **Action 1**.
- Si `TopicMatch > 0` → Les messages arrivent dans SQS. Passer à la **Phase 2**.

### 4. Phase 2 — Diagnostic et arbre de décision

#### Étape A — Vérifier l'abonnement SNS

```bash
TOPIC_ARN=$(aws sns list-topics --region us-east-1 \
  --query "Topics[?contains(TopicArn,'ecosense-alert')].TopicArn" --output text)

aws sns list-subscriptions-by-topic --topic-arn $TOPIC_ARN --region us-east-1 \
  --query 'Subscriptions[*].{Email:Endpoint,Statut:SubscriptionArn}' --output table
```

- Si statut = `PendingConfirmation` → L'email de confirmation SNS n'a pas été validé. Passer à l'**Action 2**.
- Si aucun abonnement listé → Redéployer le stack (`cdk deploy EcoSense-Primary`).

#### Étape B — Vérifier Lambda Ingest (logique d'agrégation)

```bash
# Compter les invocations de Lambda Ingest (dernières 10 min)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=ecosense-ingest-us-east-1 \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 600 --statistics Sum --region us-east-1

# Vérifier les erreurs
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=ecosense-ingest-us-east-1 \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 600 --statistics Sum --region us-east-1
```

- Si `Errors > 0` → Lambda Ingest est en erreur. Passer à l'**Action 3**.

#### Étape C — Vérifier l'état des quartiers dans DynamoDB (doublons emails)

Si des emails en doublon sont reçus, l'état DynamoDB du quartier peut être corrompu :

```bash
aws dynamodb scan \
  --table-name ecosense-quartier-state-us-east-1 \
  --region us-east-1 \
  --query 'Items[*].{quartier:quartier.S,interval:current_interval_sec.N,last_sent:last_sent_at.S}'
```

- Si un quartier a `current_interval_sec = 300` (valeur initiale) depuis plus de 30 minutes → Le scheduler Flush n'est pas créé. Passer à l'**Action 4**.

#### Étape D — Vérifier la DLQ du scheduler Flush

```bash
aws sqs get-queue-attributes \
  --queue-url $(aws sqs get-queue-url \
    --queue-name ecosense-flush-scheduler-dlq-us-east-1 \
    --region us-east-1 --query 'QueueUrl' --output text) \
  --attribute-names ApproximateNumberOfMessages \
  --region us-east-1
```

- Si `ApproximateNumberOfMessages > 0` → EventBridge Scheduler n'a pas réussi à invoquer Lambda Flush. Le quartier est bloqué dans son état courant. Passer à l'**Action 4**.

### 5. Phase 3 — Actions de résolution

#### Action 1 — Réactiver la Topic Rule d'alerte

```bash
aws iot enable-topic-rule --rule-name ecosense_alert_rule --region us-east-1
```

Voir aussi **RB-ES-004** pour le diagnostic complet de SNS muet.

#### Action 2 — Reconfirmer l'abonnement SNS

```bash
TOPIC_ARN=$(aws sns list-topics --region us-east-1 \
  --query "Topics[?contains(TopicArn,'ecosense-alert')].TopicArn" --output text)

aws sns subscribe --topic-arn $TOPIC_ARN \
  --protocol email \
  --notification-endpoint $(grep ALERT_EMAIL .env | cut -d= -f2) \
  --region us-east-1
# → Ouvrir la boîte email et cliquer "Confirm subscription"
```

#### Action 3 — Redéployer Lambda Ingest

```bash
source .venv/bin/activate
cdk deploy EcoSense-Primary --exclusively
```

Injecter un message CRITICAL de test pour valider :

```bash
aws iot-data publish \
  --topic "metropole/centre/S-TEST/telemetry" \
  --payload '{"sensor_id":"S-TEST","metric":"CO2","value":1500,"unit":"ppm","status":"CRITICAL","region":"us-east-1","quartier":"centre","timestamp":0}' \
  --cli-binary-format raw-in-base64-out --region us-east-1
# Attendre l'email dans les 2 minutes
```

#### Action 4 — Réinitialiser un quartier bloqué dans DynamoDB

Si le cycle d'agrégation d'un quartier est bloqué (plus aucun email depuis > 1h malgré des alertes persistantes) :

```bash
# Supprimer l'état du quartier bloqué pour le réinitialiser
aws dynamodb delete-item \
  --table-name ecosense-quartier-state-us-east-1 \
  --key '{"quartier": {"S": "centre"}}' \
  --region us-east-1
# La prochaine alerte CRITICAL du quartier relancera le cycle normalement
```

Purger aussi la DLQ du scheduler :

```bash
aws sqs purge-queue \
  --queue-url $(aws sqs get-queue-url \
    --queue-name ecosense-flush-scheduler-dlq-us-east-1 \
    --region us-east-1 --query 'QueueUrl' --output text) \
  --region us-east-1
```

### 6. Phase 4 — Procédure d'escalade

Si le problème n'est pas résolu après **15 minutes** :

1. Rassembler les logs `/aws/lambda/ecosense-ingest-us-east-1` et `/aws/lambda/ecosense-flush-us-east-1`.
2. Capturer l'état complet de DynamoDB (`scan` des deux tables `QuartierState` et `PendingAlerts`).
3. Contacter l'administrateur principal avec l'heure de début, les actions tentées, et les logs.

### 7. Phase 5 — Post-incident (rétrospective)

| Champ | À remplir |
|---|---|
| Heure de début | __ |
| Heure de résolution | __ |
| Cause racine | _(ex: abonnement SNS non confirmé / scheduler Flush échoué silencieusement)_ |
| Alertes manquées | _(quartiers affectés, fenêtre temporelle)_ |
| Action préventive | _(ex: ajouter alarme CloudWatch sur DLQ `ecosense-flush-scheduler-dlq` > 0)_ |

---

## PB-ES-003 — Indisponibilité régionale

### 1. Informations générales

- **ID du Playbook** : PB-ES-003
- **Version** : 1.0
- **Auteur / Équipe** : Groupe 4 — Chapot, Lacombe, Nicoud
- **Type d'incident** : Panne régionale — `us-east-1` est inaccessible ou dégradée, le simulateur ne peut plus publier vers IoT Core primaire
- **Symptôme initial** : Logs simulateur affichant `[WARNING] Déconnexion inattendue (rc=128)` ou health check Route 53 `us-east-1` en `UNHEALTHY` sans inversion manuelle

### 2. Rôles et responsabilités

| Rôle | Responsabilité |
|---|---|
| **Incident Commander (IC)** | Pilote le playbook, décide du moment du basculement et du retour en primaire |
| **Ops/Tech Lead** | Exécute les commandes AWS CLI, surveille les métriques des deux régions |
| **Communications** | Informe le professeur de la panne et de l'activation du failover |

### 3. Phase 1 — Triage et vérification de l'alerte

```bash
# 1. Vérifier la connectivité AWS us-east-1
aws sts get-caller-identity --region us-east-1

# 2. Vérifier l'état du health check Route 53 primaire
HC_ID_PRIMARY=$(grep ROUTE53_HC_ID_PRIMARY .env | cut -d= -f2)
aws route53 get-health-check-status \
  --health-check-id $HC_ID_PRIMARY \
  --query 'HealthCheckObservations[0].StatusReport.Status' --output text

# 3. Vérifier la Lambda health (proxy de santé)
HEALTH_URL=$(aws lambda get-function-url-config \
  --function-name ecosense-health-us-east-1 \
  --region us-east-1 \
  --query 'FunctionUrl' --output text 2>/dev/null)
curl -s --max-time 5 "$HEALTH_URL" || echo "HEALTH CHECK FAILED"
```

**Décision :**
- Statut Route 53 contient `Failure` **ET** `curl` timeout → Panne régionale confirmée. Passer directement à la **Phase 3, Action 1** (basculement).
- Statut Route 53 `Success` mais simulateur déconnecté → Problème local (certificat, réseau) — appliquer **RB-ES-002**.
- Statut ambigu → Passer à la **Phase 2**.

### 4. Phase 2 — Diagnostic

#### Étape A — Différencier panne régionale vs panne applicative

```bash
# Vérifier si us-east-1 répond encore à AWS (hors IoT)
aws s3 ls s3://ecosense-archives-$(aws sts get-caller-identity \
  --query Account --output text)-us-east-1 \
  --region us-east-1 2>&1 | head -5

# Vérifier les métriques IoT Core us-east-1
aws cloudwatch get-metric-statistics \
  --namespace AWS/IoT \
  --metric-name PublishIn.Success \
  --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 --statistics Sum --region us-east-1
```

- Si S3 répond mais IoT Core n'a aucun `PublishIn.Success` → La panne est isolée à IoT Core `us-east-1`. Basculer.
- Si S3 ne répond pas non plus → Panne régionale large. Basculer immédiatement.

#### Étape B — Vérifier la disponibilité de us-west-2

```bash
# Vérifier le health check Route 53 secondaire
HC_ID_SECONDARY=$(grep ROUTE53_HC_ID_SECONDARY .env | cut -d= -f2)
aws route53 get-health-check-status \
  --health-check-id $HC_ID_SECONDARY \
  --query 'HealthCheckObservations[0].StatusReport.Status' --output text

# Vérifier que la stack secondaire est bien déployée
aws cloudformation describe-stacks \
  --stack-name EcoSense-Secondary \
  --region us-west-2 \
  --query 'Stacks[0].StackStatus' --output text
```

**Attendu** : `StackStatus = UPDATE_COMPLETE` ou `CREATE_COMPLETE` et Route 53 `Success`. Si la stack secondaire est absente → voir **RB-ES-005** (redéploiement complet).

### 5. Phase 3 — Actions de résolution

#### Action 1 — Basculer vers us-west-2 (failover proactif manuel)

Le simulateur dispose d'un mécanisme de failover automatique via Route 53. Pour forcer le basculement sans attendre la détection automatique :

```bash
# Inverser le health check primaire → simulateur détecte UNHEALTHY et bascule
HC_ID_PRIMARY=$(grep ROUTE53_HC_ID_PRIMARY .env | cut -d= -f2)
aws route53 update-health-check \
  --health-check-id $HC_ID_PRIMARY \
  --inverted
```

Observer les logs du simulateur. Dans les 5 à 10 secondes :

```
[WARNING] [Route53] us-east-1 signalé UNHEALTHY — basculement proactif avant déconnexion MQTT
[WARNING] Basculement vers us-west-2
[INFO] Connecté à IoT Core (us-west-2)
```

Si le simulateur ne bascule pas automatiquement (absence des variables `ROUTE53_HC_ID_*` dans `.env`) :

```bash
# Forcer le basculement réactif : arrêter le simulateur et le relancer sur us-west-2
# Ctrl+C puis modifier .env
grep -v 'IOT_ENDPOINT_PRIMARY\|AWS_REGION' .env > .env.tmp
echo "AWS_REGION=us-west-2" >> .env.tmp
mv .env.tmp .env
make run
```

#### Action 2 — Vérifier la continuité de service sur us-west-2

```bash
# Confirmer que les données arrivent dans le bucket secondaire
sleep 90  # attendre le buffer Firehose (60 s)
aws s3 ls s3://ecosense-archives-$(aws sts get-caller-identity \
  --query Account --output text)-us-west-2/raw/ \
  --recursive | sort | tail -5
```

**Attendu** : Nouveaux fichiers horodatés dans les 2 minutes.

#### Action 3 — Retour en production sur us-east-1 (rollback)

Une fois us-east-1 rétablie :

```bash
# Annuler l'inversion du health check → us-east-1 redevient healthy
aws route53 update-health-check \
  --health-check-id $HC_ID_PRIMARY \
  --no-inverted

# Redémarrer le simulateur sur la région primaire
# Ctrl+C puis :
make run
```

Observer les logs :
```
[INFO] [Route53] us-east-1 redevenu HEALTHY
[INFO] Retour sur us-east-1
[INFO] Connecté à IoT Core (us-east-1)
```

Voir **RB-ES-001** pour la procédure complète de basculement/rollback.

### 6. Phase 4 — Procédure d'escalade

Si us-east-1 ne répond pas après **30 minutes** et que le fonctionnement sur us-west-2 est instable :

1. Rassembler les logs CloudWatch de us-west-2 pour confirmer la continuité d'archivage.
2. Ouvrir le [AWS Service Health Dashboard](https://health.aws.amazon.com) pour confirmer l'incident AWS officiel.
3. Contacter l'administrateur principal avec :
   - Heure de début de la panne
   - Région affectée et scope (IoT seul vs région entière)
   - Statut du failover us-west-2

### 7. Phase 5 — Post-incident (rétrospective)

| Champ | À remplir |
|---|---|
| Heure de début | __ |
| Heure de basculement vers us-west-2 | __ |
| Heure de retour sur us-east-1 | __ |
| Cause racine | _(ex: instabilité AWS us-east-1 / certificat expiré / lab restart)_ |
| Données perdues pendant la bascule | _(messages publiés entre la panne et le failover, RTO effectif)_ |
| Données non répliquées | _(S3 CRR non disponible en Learner Lab — les archives us-east-1 et us-west-2 sont indépendantes)_ |
| Action préventive | _(ex: vérifier la variable `ROUTE53_HC_ID_PRIMARY` au démarrage du simulateur)_ |
