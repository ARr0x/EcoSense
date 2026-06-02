# ==============================================================================
# Makefile — EcoSense
# Helpers pour déploiement, test et observation du pipeline IoT
#
# Usage : make help
#
# Note : le venv `.venv` est automatiquement ajouté au PATH par ce Makefile,
# pas besoin de `source .venv/bin/activate` au préalable.
# ==============================================================================

# Charger les variables depuis .env si présent (silencieux si absent)
-include .env
export

# Ajouter le venv au PATH pour que `cdk` trouve le bon python (et inversement)
export PATH := $(CURDIR)/.venv/bin:$(PATH)

# Variables (override possible : make deploy REGION=us-east-2)
REGION         ?= us-east-1
ACCOUNT_ID     ?= $(AWS_ACCOUNT_ID)
CDK_BUCKET     := cdk-hnb659fds-assets-$(ACCOUNT_ID)-$(REGION)
ARCHIVE_BUCKET := ecosense-archives-$(ACCOUNT_ID)-$(REGION)
SNS_TOPIC_ARN  := arn:aws:sns:$(REGION):$(ACCOUNT_ID):ecosense-alert-$(REGION)

.DEFAULT_GOAL := help

# ==============================================================================
# Aide
# ==============================================================================

.PHONY: help
help: ## Afficher cette aide
	@echo ""
	@echo "EcoSense — Commandes disponibles"
	@echo "================================="
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Variables (override : make <cmd> REGION=us-east-2) :"
	@echo "  REGION     = $(REGION)"
	@echo "  ACCOUNT_ID = $(ACCOUNT_ID)"
	@echo ""

# ==============================================================================
# Installation
# ==============================================================================

.PHONY: venv
venv: ## Créer le venv (.venv) — uv si dispo, sinon python -m venv
	@if [ ! -d .venv ]; then \
		if command -v uv >/dev/null 2>&1; then \
			uv venv .venv; \
		else \
			python -m venv .venv; \
		fi; \
		echo "✅ Venv créé dans .venv"; \
	else \
		echo "ℹ️  .venv existe déjà"; \
	fi

.PHONY: install
install: venv ## Installer les dépendances Python (CDK + simulateur)
	@if command -v uv >/dev/null 2>&1; then \
		uv pip install -r iac/requirements.txt -r simulator/requirements.txt; \
	else \
		pip install -r iac/requirements.txt; \
		pip install -r simulator/requirements.txt; \
	fi

# ==============================================================================
# CDK — Déploiement
# ==============================================================================

.PHONY: bootstrap-bucket
bootstrap-bucket: ## Créer le bucket S3 CDK manuellement (workaround Learner Lab)
	aws s3 mb s3://$(CDK_BUCKET) --region $(REGION)

.PHONY: synth
synth: ## Générer les templates CloudFormation (validation)
	cdk synth

.PHONY: diff
diff: ## Voir les changements depuis le dernier deploy
	cdk diff

.PHONY: deploy
deploy: ## Déployer les 3 stacks Primary (Sns + Storage + IoTCore)
	cdk deploy Sns-Primary Storage-Primary IoTCore-Primary --require-approval never

.PHONY: deploy-iot
deploy-iot: ## Redéployer uniquement IoTCore-Primary (après modif Topic Rules)
	cdk deploy IoTCore-Primary --require-approval never

.PHONY: empty-buckets
empty-buckets: ## Vider les buckets S3 (archive versionné + CDK assets)
	@echo "🧹 Vidage du bucket archive (toutes versions)..."
	@aws s3api list-object-versions --bucket $(ARCHIVE_BUCKET) --region $(REGION) \
		--output text --query 'Versions[].[Key,VersionId]' 2>/dev/null | \
		while read key vid; do \
			[ -z "$$key" ] || aws s3api delete-object --bucket $(ARCHIVE_BUCKET) \
				--key "$$key" --version-id "$$vid" --region $(REGION) >/dev/null; \
		done || true
	@aws s3api list-object-versions --bucket $(ARCHIVE_BUCKET) --region $(REGION) \
		--output text --query 'DeleteMarkers[].[Key,VersionId]' 2>/dev/null | \
		while read key vid; do \
			[ -z "$$key" ] || aws s3api delete-object --bucket $(ARCHIVE_BUCKET) \
				--key "$$key" --version-id "$$vid" --region $(REGION) >/dev/null; \
		done || true
	@echo "🧹 Vidage du bucket CDK assets..."
	@aws s3 rm s3://$(CDK_BUCKET) --recursive --region $(REGION) 2>/dev/null || true
	@echo "✅ Buckets vidés"

.PHONY: destroy
destroy: empty-buckets ## Détruire les 3 stacks + supprimer les buckets (clean total)
	cdk destroy Sns-Primary Storage-Primary IoTCore-Primary --force
	@echo "🧹 Suppression des buckets résiduels (RemovalPolicy.RETAIN)..."
	@aws s3 rb s3://$(ARCHIVE_BUCKET) --region $(REGION) 2>/dev/null || true
	@aws s3 rb s3://$(CDK_BUCKET) --region $(REGION) 2>/dev/null || true
	@echo "✅ Destroy complet"

# ==============================================================================
# Certificats & Simulateur MQTT
# ==============================================================================

.PHONY: certs
certs: ## Provisionner les certs X.509 pour us-east-1
	python simulator/provision_certs.py us-east-1

.PHONY: check
check: ## Tester la connexion MQTT mTLS (exit 0/1)
	python simulator/simulator_mqtt.py --check

.PHONY: dry-run
dry-run: ## Générer des payloads sans publier (validation locale)
	python simulator/simulator_mqtt.py --dry-run -v

.PHONY: run
run: ## Lancer le simulateur en mode publication
	python simulator/simulator_mqtt.py

# ==============================================================================
# Tests manuels via AWS CLI (bypasse mTLS, utile pour debug)
# ==============================================================================

.PHONY: test-critical
test-critical: ## Publier un message CRITICAL (→ SNS email + S3)
	@aws iot-data publish \
		--topic "metropole/centre/S-TEST/telemetry" \
		--payload '{"sensor_id":"S-TEST","metric":"CO2","value":1500,"unit":"ppm","status":"CRITICAL","region":"$(REGION)","quartier":"centre","timestamp":1717256400}' \
		--cli-binary-format raw-in-base64-out \
		--region $(REGION)
	@echo "✅ CRITICAL publié sur metropole/centre/S-TEST/telemetry"

.PHONY: test-normal
test-normal: ## Publier un message NORMAL (→ S3 seulement, pas d'alerte)
	@aws iot-data publish \
		--topic "metropole/nord/S-TEST/telemetry" \
		--payload '{"sensor_id":"S-TEST","metric":"CO2","value":500,"unit":"ppm","status":"NORMAL","region":"$(REGION)","quartier":"nord","timestamp":1717256400}' \
		--cli-binary-format raw-in-base64-out \
		--region $(REGION)
	@echo "✅ NORMAL publié sur metropole/nord/S-TEST/telemetry"

.PHONY: sns-test
sns-test: ## Publier un test direct sur SNS (skip IoT, vérifie email)
	aws sns publish \
		--topic-arn $(SNS_TOPIC_ARN) \
		--subject "Test EcoSense" \
		--message "Test direct SNS - si tu reçois ça, la subscription marche" \
		--region $(REGION)

# ==============================================================================
# Observation
# ==============================================================================

.PHONY: iot-endpoint
iot-endpoint: ## Afficher l'endpoint IoT Core (à mettre dans .env)
	aws iot describe-endpoint --endpoint-type iot:Data-ATS --region $(REGION)

.PHONY: s3-ls
s3-ls: ## Lister les fichiers archivés dans S3
	aws s3 ls s3://$(ARCHIVE_BUCKET)/raw/ --recursive --region $(REGION)

.PHONY: s3-latest
s3-latest: ## Afficher le contenu du dernier fichier S3
	@LATEST=$$(aws s3 ls s3://$(ARCHIVE_BUCKET)/raw/ --recursive --region $(REGION) | tail -1 | awk '{print $$NF}'); \
	echo "Fichier : $$LATEST"; \
	echo "----"; \
	aws s3 cp s3://$(ARCHIVE_BUCKET)/$$LATEST - --region $(REGION)

.PHONY: metrics-rules
metrics-rules: ## Voir les RuleMatches des Topic Rules (10 dernières min)
	@echo "=== AlertRule (CRITICAL → SNS) ==="
	@aws cloudwatch get-metric-statistics --namespace AWS/IoT --metric-name RuleMatches \
		--dimensions Name=RuleName,Value=ecosense_alert_rule \
		--start-time $$(date -u -d '10 minutes ago' '+%Y-%m-%dT%H:%M:%S') \
		--end-time $$(date -u '+%Y-%m-%dT%H:%M:%S') \
		--period 60 --statistics Sum --region $(REGION)
	@echo "=== ArchiveRule (ALL → Firehose) ==="
	@aws cloudwatch get-metric-statistics --namespace AWS/IoT --metric-name RuleMatches \
		--dimensions Name=RuleName,Value=ecosense_archive_rule \
		--start-time $$(date -u -d '10 minutes ago' '+%Y-%m-%dT%H:%M:%S') \
		--end-time $$(date -u '+%Y-%m-%dT%H:%M:%S') \
		--period 60 --statistics Sum --region $(REGION)

.PHONY: metrics-sns
metrics-sns: ## Voir les messages SNS publiés (15 dernières min)
	aws cloudwatch get-metric-statistics --namespace AWS/SNS \
		--metric-name NumberOfMessagesPublished \
		--dimensions Name=TopicName,Value=ecosense-alert-$(REGION) \
		--start-time $$(date -u -d '15 minutes ago' '+%Y-%m-%dT%H:%M:%S') \
		--end-time $$(date -u '+%Y-%m-%dT%H:%M:%S') \
		--period 60 --statistics Sum --region $(REGION)

.PHONY: metrics-firehose
metrics-firehose: ## Voir les records reçus par Firehose (10 dernières min)
	aws cloudwatch get-metric-statistics --namespace AWS/Firehose \
		--metric-name IncomingRecords \
		--dimensions Name=DeliveryStreamName,Value=ecosense-delivery-$(REGION) \
		--start-time $$(date -u -d '10 minutes ago' '+%Y-%m-%dT%H:%M:%S') \
		--end-time $$(date -u '+%Y-%m-%dT%H:%M:%S') \
		--period 60 --statistics Sum --region $(REGION)

# ==============================================================================
# Nettoyage local
# ==============================================================================

.PHONY: clean
clean: ## Nettoyer les artefacts locaux (cdk.out, __pycache__)
	rm -rf cdk.out
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Artefacts locaux nettoyés"
