# Référence commandes — EcoSense

Commandes manuelles pour les opérations courantes. Le workflow standard est dans le [README](../README.md).

Activer le venv avant toute commande Python ou CDK :
```bash
source .venv/bin/activate
# ou simplement utiliser make — le Makefile l'active automatiquement via PATH
```

---

## Environnement local

```bash
make install    # crée .venv + installe iac/requirements.txt et simulator/requirements.txt
                # aucun appel AWS — peut tourner sans credentials
```

Équivalent manuel :
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r iac/requirements.txt -r simulator/requirements.txt
```

---

## CDK

Toutes les commandes CDK s'exécutent depuis la **racine du dépôt** (`cdk.json` s'y trouve).

### Bucket CDK (workaround Learner Lab)

`cdk bootstrap` standard est bloqué en AWS Academy — les buckets d'assets CDK sont créés manuellement :

```bash
make bootstrap-bucket   # supprime + recrée les buckets si nécessaire

# Ou manuellement :
aws s3 mb s3://ecosense-cdk-{ACCOUNT_ID}-us-east-1 --region us-east-1
aws s3 mb s3://ecosense-cdk-{ACCOUNT_ID}-us-west-2 --region us-west-2
```

> `make bootstrap` = `make install` + `make bootstrap-bucket` en une commande.

### Générer les templates CloudFormation sans déployer

```bash
cdk synth
```

### Voir les changements avant deploy

```bash
cdk diff
cdk diff EcoSense-Primary
```

### Déployer un stack spécifique

```bash
cdk deploy EcoSense-Primary
cdk deploy EcoSense-Secondary
cdk deploy EcoSense-Route53
```

### Déployer tous les stacks actifs

```bash
cdk deploy --all --require-approval never --import-existing-resources
```

`--import-existing-resources` évite les conflits si des ressources existent déjà.

### Détruire l'infrastructure

```bash
# Vider les buckets d'archives avant destroy (RemovalPolicy.RETAIN — non supprimés par CDK)
aws s3 rm s3://ecosense-archives-{ACCOUNT_ID}-us-east-1 --recursive
aws s3 rm s3://ecosense-archives-{ACCOUNT_ID}-us-west-2 --recursive

cdk destroy --all --force
```

---

## Certificats X.509

### provision_certs.py

```
python simulator/provision_certs.py <region> [--force] [-v]
```

| Argument | Description |
|---|---|
| `region` | `us-east-1` ou `us-west-2` |
| `--force` | Recrée le certificat même si un existant est présent |
| `-v` | Mode verbose (DEBUG) |

**Fichiers produits** dans `simulator/certs/{region}/` :

| Fichier | Permissions | Contenu |
|---|---|---|
| `client.crt` | 644 | Certificat client X.509 |
| `private.key` | 400 | Clé privée |
| `AmazonRootCA1.pem` | 644 | CA racine Amazon Trust Services |
| `metadata.json` | 644 | `certificate_id`, ARN, région, policy, date |

```bash
# Provisionnement initial
python simulator/provision_certs.py us-east-1
python simulator/provision_certs.py us-west-2

# Forcer la recréation (rotation ou perte de la clé)
python simulator/provision_certs.py us-east-1 --force
python simulator/provision_certs.py us-west-2 --force
```

### Gérer un certificat via AWS CLI

```bash
# Lire le cert ID depuis le fichier local
CERT_ID=$(python3 -c "import json; print(json.load(open('simulator/certs/us-east-1/metadata.json'))['certificate_id'])")

# Vérifier le statut
aws iot describe-certificate --certificate-id $CERT_ID --region us-east-1 \
  --query 'certificateDescription.status' --output text

# Désactiver (pour tester le failover)
aws iot update-certificate --certificate-id $CERT_ID --new-status INACTIVE --region us-east-1

# Réactiver
aws iot update-certificate --certificate-id $CERT_ID --new-status ACTIVE --region us-east-1
```

---

## Simulateur MQTT

### simulator_mqtt.py

```
python simulator/simulator_mqtt.py [OPTIONS]
```

| Option | Défaut `.env` | Description |
|---|---|---|
| `--check` | — | Test de connexion TLS uniquement (exit 0/1) |
| `--dry-run` | — | Génère les payloads sans publier |
| `--region {us-east-1,us-west-2}` | `CDK_DEFAULT_REGION` | Forcer une région |
| `--burst-size INT` | `BURST_SIZE` | Messages par salve |
| `--burst-interval FLOAT` | `BURST_INTERVAL` | Secondes entre salves |
| `--sensor-count INT` | `SENSOR_COUNT` | Nombre de capteurs simulés |
| `--critical-rate FLOAT` | `CRITICAL_RATE` | Fraction de messages CRITICAL (0.0–1.0) |
| `-v` | — | Mode verbose (DEBUG) |
| `--mqtt-debug` | — | Logs internes paho-mqtt |

```bash
# Tester la connexion sur les deux régions
python simulator/simulator_mqtt.py --check
python simulator/simulator_mqtt.py --check --region us-west-2

# Prévisualiser les payloads sans publier
python simulator/simulator_mqtt.py --dry-run -v

# Simulation standard
python simulator/simulator_mqtt.py

# Crash-test : taux CRITICAL élevé pour la démo jury
python simulator/simulator_mqtt.py --burst-size 10 --critical-rate 0.5 --burst-interval 0.5

# Forcer la région secondaire
python simulator/simulator_mqtt.py --region us-west-2
```

---

## Route 53 — Failover manuel

```bash
# Variables de session
HC_ID_PRIMARY=$(grep ROUTE53_HC_ID_PRIMARY .env | cut -d= -f2)
HC_ID_SECONDARY=$(grep ROUTE53_HC_ID_SECONDARY .env | cut -d= -f2)

# État actuel des health checks
aws route53 get-health-check-status --health-check-id $HC_ID_PRIMARY \
  --query 'HealthCheckObservations[0].StatusReport.Status' --output text

# Déclencher un failover (marque us-east-1 UNHEALTHY)
aws route53 update-health-check --health-check-id $HC_ID_PRIMARY --inverted

# Rétablir
aws route53 update-health-check --health-check-id $HC_ID_PRIMARY --no-inverted
```

---

## Tests manuels AWS CLI

### Publier un message de test via l'API IoT Data

```bash
# Message NORMAL
aws iot-data publish \
  --topic "metropole/nord/S-TEST/telemetry" \
  --payload '{"sensor_id":"S-TEST","metric":"CO2","value":500,"unit":"ppm","status":"NORMAL","region":"us-east-1","quartier":"nord","timestamp":0}' \
  --cli-binary-format raw-in-base64-out --region us-east-1

# Message CRITICAL (déclenche SNS + archivage)
aws iot-data publish \
  --topic "metropole/centre/S-TEST/telemetry" \
  --payload '{"sensor_id":"S-TEST","metric":"CO2","value":1500,"unit":"ppm","status":"CRITICAL","region":"us-east-1","quartier":"centre","timestamp":0}' \
  --cli-binary-format raw-in-base64-out --region us-east-1
```

### Vérifier le pipeline

```bash
# Métriques Topic Rules (TopicMatch = messages reçus, Success = actions exécutées)
for rule in ecosense_alert_rule ecosense_archive_rule; do
  echo "=== $rule ==="
  for metric in TopicMatch Success Failure; do
    printf "  %-12s: " "$metric"
    aws cloudwatch get-metric-statistics \
      --namespace AWS/IoT --metric-name "$metric" \
      --dimensions Name=RuleName,Value="$rule" \
      --start-time "$(date -u -d '10 minutes ago' '+%Y-%m-%dT%H:%M:%S')" \
      --end-time "$(date -u '+%Y-%m-%dT%H:%M:%S')" \
      --period 600 --statistics Sum \
      --query 'Datapoints[0].Sum' --output text --region us-east-1
  done
done

# Derniers fichiers archivés en S3
aws s3 ls s3://ecosense-archives-{ACCOUNT_ID}-us-east-1/raw/ --recursive | sort | tail -10

# Toutes les valeurs à copier dans .env après deploy (IoT endpoints + Route53 HC IDs)
make post-deploy
```

### Test SNS direct

```bash
aws sns publish \
  --topic-arn "arn:aws:sns:us-east-1:{ACCOUNT_ID}:ecosense-alert-us-east-1" \
  --subject "Test EcoSense" \
  --message "Test direct SNS — vérification de la subscription" \
  --region us-east-1
```

---

## Schéma d'architecture

Le script `docs/scripts/archi.py` génère `docs/img/ecosense_archi.png` via la bibliothèque [`diagrams`](https://diagrams.mingrammer.com/).

```bash
# Dépendance système
sudo apt install graphviz

# Dépendance Python (hors requirements.txt)
pip install diagrams

# Générer
python docs/scripts/archi.py
mv ecosense_archi.png docs/img/ecosense_archi.png
```
