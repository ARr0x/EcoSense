# EcoSense
![logo ecosens](docs/img/Ecosenslogo.png)

Pipeline de télémétrie IoT multi-région sur AWS. Les capteurs publient des mesures environnementales via MQTT/mTLS vers IoT Core ; les Topic Rules routent le flux en deux chemins : alertes CRITICAL vers une chaîne d'agrégation par quartier avec backoff exponentiel, et 100 % du flux vers Kinesis Firehose pour archivage dans S3 et interrogation via Athena. Deux régions indépendantes (`us-east-1` / `us-west-2`) avec failover côté client.

## Architecture

![ArchiEcosens](docs/img/archiecosens.png)

Schémas Draw.io éditables dans `docs/img/Drowio/` :
- [`EcosensV4Archi.drawio`](docs/img/Drowio/EcosensV4Archi.drawio) — architecture générale multi-région
- [`ecosense_alerting.drawio`](docs/img/Drowio/ecosense_alerting.drawio) — pipeline d'alertes CRITICAL

---

## Prérequis

- Python 3.11+
- AWS CLI configuré (`~/.aws/credentials` valides)
- Rôle IAM `LabRole` présent dans le compte (AWS Academy)

---

## Quick start

**Étape 1 — Configurer `.env`**

```bash
cp .env.example .env
```

Renseigner dans `.env` :

| Variable | Où la trouver |
|---|---|
| `AWS_ACCOUNT_ID` | Console AWS → coin supérieur droit |
| `ALERT_EMAIL` | L'adresse qui recevra les alertes CRITICAL |

**Étape 2 — Déployer**

```bash
make bootstrap        # venv + dépendances + buckets CDK
make deploy           # déploie Primary + Secondary + Route 53 health checks
```

**Étape 3 — Renseigner les valeurs post-deploy dans `.env`**

```bash
make post-deploy      # affiche toutes les valeurs à copier dans le .env
make deploy-route53   # Met à jour les healthchecks
```

Copier ces 4 variables dans `.env` :

| Variable | Description |
|---|---|
| `IOT_ENDPOINT_PRIMARY` | Endpoint MQTT us-east-1 |
| `IOT_ENDPOINT_SECONDARY` | Endpoint MQTT us-west-2 |
| `ROUTE53_HC_ID_PRIMARY` | ID health check Route 53 — active le failover proactif |
| `ROUTE53_HC_ID_SECONDARY` | ID health check Route 53 secondaire |

**Étape 4 — Certificats et lancement**

```bash
make certs            # provisionne les certificats X.509
make check            # vérifie la connexion MQTT (doit afficher "TLS handshake OK")
make run              # lance le simulateur
```

> Après `make deploy`, AWS envoie un email de confirmation SNS à `ALERT_EMAIL`.
> **Cliquer "Confirm subscription"** avant de lancer `make run`, sinon les alertes CRITICAL ne seront pas reçues.

---

## Démo (infra déjà déployée)

```bash
make clean && make install && make certs && make check && make run
```

---

## Référence des commandes

| Cible | Description |
|---|---|
| `make install` | Crée le venv et installe les dépendances (local, sans appel AWS) |
| `make bootstrap` | `install` + création des buckets CDK (premier démarrage) |
| `make deploy` | Déploie Primary + Secondary + Route 53 si `MULTI_REGION=true` |
| `make deploy-route53` | Redéploie uniquement les health checks Route 53 |
| `make post-deploy` | Affiche toutes les valeurs à copier dans `.env` après deploy |
| `make certs` | Provisionne les certificats X.509 pour le simulateur |
| `make certs-force` | Reprovisionne les certificats (écrase les existants) |
| `make check` | Teste la connexion MQTT mTLS sur toutes les régions actives |
| `make run` | Lance le simulateur en mode publication |
| `make clean` | Supprime venv, certs, cdk.out — conserve `.env` |
| `make lab-restart` | Après restart Learner Lab : bucket CDK + redeploy + certs |
| `make destroy` | Vide les buckets et détruit tous les stacks |

```bash
make help   # liste complète
```

---

## Documentation

### Projet
- [Sujet](Sujet.md) — scénario métier, architecture implémentée, déviations vs sujet prescrit, crash-tests
- [Journal de bord](PlaningEquipe.md) — répartition des tâches par membre et par jour

### Architecture
- [Infrastructure](docs/infrastructure.md) — composants AWS déployés, flux de données, ressources par région
- [Architecture cible](docs/architecture-cible.md) — architecture production cible (Route 53, CRR, KMS, sécurité complète)
- [ADR — Architecture Decision Records](docs/adr.md) — décisions techniques structurantes
- [Limitations et améliorations](docs/limitations-et-ameliorations.md) — contraintes AWS Academy, bugs connus, delta actuel → production

### Exploitation
- [Runbooks](docs/runbook.md) — procédures de résolution des pannes critiques
- [Référence commandes](docs/usage.md) — CDK, certificats, simulateur, AWS CLI, Route 53
