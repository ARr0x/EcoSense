# Scripts et commandes

Référence des commandes manuelles sans Makefile. Pour le workflow standard, utiliser `make help`.

---

## Prérequis

**Venv** — activer avant toute commande Python ou CDK :

```bash
source .venv/bin/activate
# ou créer + installer si absent
python -m venv .venv
pip install -r iac/requirements.txt -r simulator/requirements.txt
```

**Fichier `.env`** — copier `.env.example` et renseigner au minimum :

```bash
cp .env.example .env
# Variables obligatoires avant cdk deploy :
#   AWS_ACCOUNT_ID
#   ALERT_EMAIL (optionnel mais recommandé)
#
# Variables à renseigner après cdk deploy :
#   IOT_ENDPOINT_PRIMARY
#   IOT_ENDPOINT_SECONDARY
```

**AWS credentials** — les commandes supposent un profil AWS configuré avec les droits nécessaires (déploiement CDK, IoT, SNS, S3, CloudWatch).

---

## CDK

Toutes les commandes CDK s'exécutent depuis la **racine du dépôt** (`cdk.json` y est situé).

### Dépendances

```bash
pip install -r iac/requirements.txt
```

### Buckets CDK assets (Learner Lab)

L'environnement AWS Academy bloque le bootstrap CDK standard. Les buckets d'assets doivent être créés manuellement avant le premier déploiement :

```bash
aws s3 mb s3://ecosense-cdk-{ACCOUNT_ID}-us-east-1 --region us-east-1
aws s3 mb s3://ecosense-cdk-{ACCOUNT_ID}-us-east-2 --region us-east-2
```

Remplacer `{ACCOUNT_ID}` par la valeur de `AWS_ACCOUNT_ID` dans `.env`.

### Générer les templates CloudFormation

```bash
cdk synth
```

### Déploiement

```bash
# Toutes les stacks actives (selon MULTI_REGION dans .env)
cdk deploy --all --require-approval never --import-existing-resources

# Région primaire uniquement
cdk deploy EcoSense-Primary --require-approval never --import-existing-resources

# Région secondaire uniquement
cdk deploy EcoSense-Secondary --require-approval never --import-existing-resources
```

`--require-approval never` supprime la confirmation interactive. `--import-existing-resources` évite les conflits si des ressources existent déjà dans le compte.

### Voir les changements depuis le dernier déploiement

```bash
cdk diff
cdk diff EcoSense-Primary
```

### Destruction

La destruction nécessite de vider les buckets S3 au préalable (le versioning empêche la suppression automatique) :

```bash
# Vider le bucket d'archives (toutes versions)
aws s3api list-object-versions --bucket ecosense-archives-{ACCOUNT_ID}-us-east-1 \
    --output text --query 'Versions[].[Key,VersionId]' | \
    while read key vid; do
        aws s3api delete-object --bucket ecosense-archives-{ACCOUNT_ID}-us-east-1 \
            --key "$key" --version-id "$vid"
    done

# Détruire les stacks
cdk destroy --all --force

# Supprimer les buckets résiduels (RemovalPolicy.RETAIN)
aws s3 rb s3://ecosense-archives-{ACCOUNT_ID}-us-east-1
aws s3 rb s3://ecosense-cdk-{ACCOUNT_ID}-us-east-1
```

---

## Certificats X.509

### provision_certs.py

```
Usage : python simulator/provision_certs.py <region> [--force] [-v]
```

| Argument | Requis | Description |
|---|---|---|
| `region` | oui | `us-east-1` ou `us-east-2` |
| `--force` | non | Recrée le certificat même si un existant est détecté |
| `-v` | non | Mode verbose (niveau DEBUG) |

Le script appelle l'API IoT Core (`CreateKeysAndCertificate`), crée la policy `EcoSenseSimulatorPolicy-{region}` si absente, et l'attache au certificat.

**Fichiers produits** dans `simulator/certs/{region}/` :

| Fichier | Permissions | Contenu |
|---|---|---|
| `client.crt` | 644 | Certificat client X.509 |
| `private.key` | 400 | Clé privée (lecture seule) |
| `AmazonRootCA1.pem` | 644 | CA racine Amazon Trust Services |
| `metadata.json` | 644 | certificateId, ARN, region, policy_name, created_at |

**Exemples :**

```bash
# Provisionner les deux régions
python simulator/provision_certs.py us-east-1
python simulator/provision_certs.py us-east-2

# Recréer (en cas de rotation ou perte de la clé privée)
python simulator/provision_certs.py us-east-1 --force
python simulator/provision_certs.py us-east-2 --force -v
```

Si un certificat existe déjà et que `--force` n'est pas fourni, le script affiche un avertissement et sort sans modifier les fichiers existants.

---

## Simulateur MQTT

### simulator_mqtt.py

```
Usage : python simulator/simulator_mqtt.py [OPTIONS]
```

**Arguments :**

| Argument | Défaut | Description |
|---|---|---|
| `--check` | — | Teste la connexion TLS et sort (exit 0 = OK, 1 = erreur) |
| `--dry-run` | — | Génère des payloads sans publier sur MQTT |
| `--region {us-east-1,us-east-2}` | valeur `.env` | Force une région spécifique |
| `--burst-size INT` | `BURST_SIZE` (.env) ou 50 | Nombre de messages par salve |
| `--burst-interval FLOAT` | `BURST_INTERVAL` (.env) ou 1.0 | Délai entre salves en secondes |
| `--sensor-count INT` | `SENSOR_COUNT` (.env) ou 500 | Nombre de capteurs simulés |
| `--critical-rate FLOAT` | `CRITICAL_RATE` (.env) ou 0.1 | Probabilité qu'un message soit CRITICAL (0.0–1.0) |
| `-v` | — | Mode verbose (niveau DEBUG) |
| `--mqtt-debug` | — | Active les logs internes de paho-mqtt |

`--check` et `--dry-run` sont mutuellement exclusifs. Les arguments CLI ont priorité sur les variables `.env`.

**Modes :**

`--check` — vérifie que les certificats sont présents, établit une connexion TLS, affiche le résultat et sort. Utile pour valider la configuration avant de lancer le simulateur.

`--dry-run` — boucle infinie qui génère et affiche les payloads JSON sans connexion MQTT. Permet de vérifier la génération des données localement.

Mode publication (défaut) — se connecte à la région active, publie des salves en continu. Si `MULTI_REGION=true` dans `.env` et qu'aucune `--region` n'est forcée, bascule automatiquement vers la région secondaire en cas de déconnexion.

**Exemples :**

```bash
# Vérification connexion
python simulator/simulator_mqtt.py --check

# Vérification sur la région secondaire
python simulator/simulator_mqtt.py --check --region us-east-2

# Prévisualiser les payloads sans publier
python simulator/simulator_mqtt.py --dry-run -v

# Publication standard
python simulator/simulator_mqtt.py

# Forcer la région secondaire
python simulator/simulator_mqtt.py --region us-east-2

# Simulation de pic de pollution (taux CRITICAL élevé, salves rapides)
python simulator/simulator_mqtt.py --burst-size 10 --critical-rate 0.5 --burst-interval 0.5
```

---

## Schéma d'architecture

Le script `docs/scripts/archi.py` génère le schéma d'architecture au format PNG via la bibliothèque [`diagrams`](https://diagrams.mingrammer.com/).

### Dépendances

**Paquet système** (moteur de rendu Graphviz) :

```bash
sudo apt install graphviz
```

**Paquet Python** :

```bash
pip install diagrams
```

`diagrams` est indépendant des dépendances CDK et simulateur — il n'est pas inclus dans `iac/requirements.txt` ni `simulator/requirements.txt`.

### Générer le PNG

```bash
# Depuis la racine du dépôt
python docs/scripts/archi.py
# Produit ecosense_archi.png dans le répertoire courant

mv ecosense_archi.png docs/img/ecosense_archi.png
```

Le paramètre `filename="ecosense_archi"` dans le script est relatif au répertoire de travail au moment de l'exécution. L'image de référence est versionnée dans `docs/img/ecosense_archi.png`.

---

## Tests manuels AWS CLI

Ces commandes publient directement via l'API IoT Data (pas de mTLS, utilise les credentials AWS). Utile pour tester le pipeline sans passer par le simulateur.

### Publier un message CRITICAL

```bash
aws iot-data publish \
  --topic "metropole/centre/S-TEST/telemetry" \
  --payload '{"sensor_id":"S-TEST","metric":"CO2","value":1500,"unit":"ppm","status":"CRITICAL","region":"us-east-1","quartier":"centre","timestamp":1748952000}' \
  --cli-binary-format raw-in-base64-out \
  --region us-east-1
```

Ce message déclenche `ecosense_alert_rule` (WHERE status = 'CRITICAL') et `ecosense_archive_rule` (toujours).

### Publier un message NORMAL

```bash
aws iot-data publish \
  --topic "metropole/nord/S-TEST/telemetry" \
  --payload '{"sensor_id":"S-TEST","metric":"CO2","value":500,"unit":"ppm","status":"NORMAL","region":"us-east-1","quartier":"nord","timestamp":1748952000}' \
  --cli-binary-format raw-in-base64-out \
  --region us-east-1
```

Ce message déclenche uniquement `ecosense_archive_rule`.

### Test SNS direct

Publie directement sur le topic SNS, sans passer par IoT Core. Permet de vérifier que l'abonnement email est actif.

```bash
aws sns publish \
  --topic-arn "arn:aws:sns:us-east-1:{ACCOUNT_ID}:ecosense-alert-us-east-1" \
  --subject "Test EcoSense" \
  --message "Test direct SNS — vérification de la subscription" \
  --region us-east-1
```

---

## Observation

### Endpoint IoT Core

```bash
aws iot describe-endpoint --endpoint-type iot:Data-ATS --region us-east-1
aws iot describe-endpoint --endpoint-type iot:Data-ATS --region us-east-2
```

### Contenu S3

```bash
# Lister les fichiers archivés
aws s3 ls s3://ecosense-archives-{ACCOUNT_ID}-us-east-1/ --recursive

# Lire un fichier spécifique
aws s3 cp s3://ecosense-archives-{ACCOUNT_ID}-us-east-1/2026-06-03-14/firehose-... -
```

### Métriques CloudWatch — Topic Rules

Vérifie que les messages sont bien routés par les deux règles IoT (`TopicMatch` = messages reçus, `Success` = actions exécutées, `Failure` = erreurs).

```bash
for rule in ecosense_alert_rule ecosense_archive_rule; do
  echo "=== $rule ==="
  for metric in TopicMatch Success Failure; do
    printf "  %-12s: " "$metric"
    aws cloudwatch get-metric-statistics \
      --namespace AWS/IoT \
      --metric-name "$metric" \
      --dimensions Name=RuleName,Value="$rule" \
      --start-time "$(date -u -d '10 minutes ago' '+%Y-%m-%dT%H:%M:%S')" \
      --end-time "$(date -u '+%Y-%m-%dT%H:%M:%S')" \
      --period 600 --statistics Sum \
      --query 'Datapoints[0].Sum' --output text \
      --region us-east-1
  done
done
```

### Métriques CloudWatch — SNS

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/SNS \
  --metric-name NumberOfMessagesPublished \
  --dimensions Name=TopicName,Value=ecosense-alert-us-east-1 \
  --start-time "$(date -u -d '15 minutes ago' '+%Y-%m-%dT%H:%M:%S')" \
  --end-time "$(date -u '+%Y-%m-%dT%H:%M:%S')" \
  --period 60 --statistics Sum \
  --region us-east-1
```

### Métriques CloudWatch — Firehose

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/Firehose \
  --metric-name IncomingRecords \
  --dimensions Name=DeliveryStreamName,Value=ecosense-delivery-us-east-1 \
  --start-time "$(date -u -d '10 minutes ago' '+%Y-%m-%dT%H:%M:%S')" \
  --end-time "$(date -u '+%Y-%m-%dT%H:%M:%S')" \
  --period 60 --statistics Sum \
  --region us-east-1
```
