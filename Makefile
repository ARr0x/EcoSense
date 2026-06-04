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
REGION            ?= $(if $(CDK_DEFAULT_REGION),$(CDK_DEFAULT_REGION),us-east-1)
SECONDARY_REGION  ?= us-west-2
MULTI_REGION      ?= false
ACCOUNT_ID        ?= $(AWS_ACCOUNT_ID)

CDK_BUCKET                := ecosense-cdk-$(ACCOUNT_ID)-$(REGION)
CDK_BUCKET_SECONDARY      := ecosense-cdk-$(ACCOUNT_ID)-$(SECONDARY_REGION)
ARCHIVE_BUCKET            := ecosense-archives-$(ACCOUNT_ID)-$(REGION)
ARCHIVE_BUCKET_SECONDARY  := ecosense-archives-$(ACCOUNT_ID)-$(SECONDARY_REGION)
SNS_TOPIC_ARN             := arn:aws:sns:$(REGION):$(ACCOUNT_ID):ecosense-alert-$(REGION)

UV     := $(shell command -v uv)
PYTHON := $(shell command -v python3)

# Stacks CDK selon MULTI_REGION
STACKS_PRIMARY   := EcoSense-Primary
ifeq ($(MULTI_REGION),true)
STACKS_SECONDARY := EcoSense-Secondary
else
STACKS_SECONDARY :=
endif
STACKS_ALL := $(STACKS_PRIMARY) $(STACKS_SECONDARY)

.DEFAULT_GOAL := help

# ==============================================================================
# Aide
# ==============================================================================

.PHONY: help
help: ## Afficher cette aide
	@echo ""
	@echo "EcoSense — Commandes disponibles"
	@echo "================================="
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Variables actives :"
	@echo "  REGION       = $(REGION)"
	@echo "  MULTI_REGION = $(MULTI_REGION)"
	@echo "  ACCOUNT_ID   = $(ACCOUNT_ID)"
	@echo "  STACKS_ALL   = $(STACKS_ALL)"
	@echo ""

# ==============================================================================
# Installation
# ==============================================================================

.PHONY: venv
venv: ## Créer le venv (.venv) — uv si dispo, sinon python3 -m venv
	@if [ -d .venv ]; then \
		echo ".venv existe déjà"; \
	elif [ -n "$(UV)" ]; then \
		uv venv .venv; \
		echo "Venv créé avec uv"; \
	elif [ -n "$(PYTHON)" ]; then \
		if $(PYTHON) -m venv .venv; then \
			echo "Venv créé avec python3"; \
		else \
			echo "Erreur : impossible de créer le venv avec python3."; \
			echo "  Le module venv est manquant. Installer l'une des options :"; \
			echo "    sudo apt install python3-venv  (Debian/Ubuntu)"; \
			echo "    pip install virtualenv"; \
			echo "    https://docs.astral.sh/uv  (recommandé)"; \
			exit 1; \
		fi; \
	else \
		echo "Erreur : Python 3 introuvable."; \
		echo "  Installer l'une des options :"; \
		echo "    https://python.org  (Python 3.12+)"; \
		echo "    https://docs.astral.sh/uv  (recommandé)"; \
		exit 1; \
	fi

.PHONY: bootstrap
bootstrap: install bootstrap-bucket ## Premier démarrage : venv + dépendances + bucket CDK
	@echo ""
	@echo "Bootstrap terminé. Prochaines étapes :"
	@echo "  1. make deploy"
	@echo "  2. Remplir IOT_ENDPOINT_* dans .env (après deploy)"
	@echo "  3. python simulator/provision_certs.py us-east-1"
	@echo "  4. python simulator/simulator_mqtt.py --check"
	@echo ""

.PHONY: install
install: venv ## Installer les dépendances Python (CDK + simulateur)
	@if [ -n "$(UV)" ]; then \
		uv pip install -r iac/requirements.txt -r simulator/requirements.txt; \
	elif command -v pip; then \
		pip install -r iac/requirements.txt -r simulator/requirements.txt; \
	else \
		echo "Erreur : ni uv ni pip disponibles."; \
		echo "  Lancer 'make venv' puis réessayer."; \
		exit 1; \
	fi

# ==============================================================================
# CDK — Bootstrap (workaround Learner Lab)
# ==============================================================================

.PHONY: lab-restart
lab-restart: ## Après chaque lab restart : bucket CDK + deploy + certs
	@echo "=== 1/3 bucket CDK ==="
	@$(MAKE) bootstrap-bucket
	@echo "=== 2/3 deploy ==="
	cdk deploy $(STACKS_ALL) --require-approval never --import-existing-resources
	@echo "=== 3/3 certs ==="
	@$(MAKE) certs-force
	@echo "Lab prêt — penser à mettre à jour IOT_ENDPOINT_* dans .env si l'endpoint a changé"

.PHONY: bootstrap-bucket
bootstrap-bucket: ## (Re)créer le(s) bucket(s) CDK — supprime et recrée si accès refusé
	@$(MAKE) -s _reset-bucket BUCKET=$(CDK_BUCKET) BUCKET_REGION=$(REGION)
	@if [ "$(MULTI_REGION)" = "true" ]; then \
		$(MAKE) -s _reset-bucket BUCKET=$(CDK_BUCKET_SECONDARY) BUCKET_REGION=$(SECONDARY_REGION); \
	fi

# Cible interne : vide, supprime et recrée un bucket (gère "bucket exists but no access")
.PHONY: _reset-bucket
_reset-bucket:
	@echo "Reset bucket s3://$(BUCKET)..."
	@if aws s3api head-bucket --bucket $(BUCKET) --region $(BUCKET_REGION) 2>/dev/null; then \
		aws s3 rm s3://$(BUCKET) --recursive --region $(BUCKET_REGION); \
		aws s3api delete-bucket --bucket $(BUCKET) --region $(BUCKET_REGION); \
	else \
		echo "  Bucket $(BUCKET) absent, création..."; \
	fi
	@aws s3 mb s3://$(BUCKET) --region $(BUCKET_REGION)
	@echo "Bucket s3://$(BUCKET) prêt"

# ==============================================================================
# CDK — Déploiement
# ==============================================================================

.PHONY: _check-env
_check-env:
	@if [ -z "$(ACCOUNT_ID)" ]; then \
		echo "Erreur : AWS_ACCOUNT_ID manquant dans .env"; exit 1; \
	fi
	@if [ -z "$(ALERT_EMAIL)" ]; then \
		echo "Avertissement : ALERT_EMAIL non défini — l'abonnement SNS sera créé sans email"; \
	fi

.PHONY: synth
synth: ## Générer les templates CloudFormation (validation)
	JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 cdk synth $(STACKS_ALL) 2>&1 \
		| grep -v "Supply a stack id" \
		| grep -v "feature flags are not configured"
	@echo "Synthèse OK — templates dans cdk.out/"

.PHONY: diff
diff: ## Voir les changements depuis le dernier deploy (toutes régions actives)
	cdk diff $(STACKS_ALL)

.PHONY: deploy
deploy: _check-env ## Déployer les stacks selon MULTI_REGION (Primary + Secondary + Route53 si MULTI_REGION=true)
	cdk deploy $(STACKS_ALL) --require-approval never --import-existing-resources || \
	  { echo ""; echo "Deploy échoué. Vérifier :"; \
	    echo "  1. make bootstrap-bucket  (bucket CDK absent après un destroy)"; \
	    echo "  2. .env correctement rempli (IOT_ENDPOINT_*, ACCOUNT_ID...)"; \
	    echo "  3. pip install -r iac/requirements.txt"; \
	    exit 1; }
	@if [ "$(MULTI_REGION)" = "true" ]; then \
		$(MAKE) deploy-route53; \
	fi

.PHONY: deploy-primary
deploy-primary: _check-env ## Déployer uniquement les stacks Primary
	cdk deploy $(STACKS_PRIMARY) --require-approval never --import-existing-resources || \
	  { echo ""; echo "Deploy échoué. Vérifier :"; \
	    echo "  1. make bootstrap-bucket  (bucket CDK absent après un destroy)"; \
	    echo "  2. .env correctement rempli (IOT_ENDPOINT_*, ACCOUNT_ID...)"; \
	    echo "  3. pip install -r iac/requirements.txt"; \
	    exit 1; }

.PHONY: deploy-secondary
deploy-secondary: ## Déployer uniquement les stacks Secondary (MULTI_REGION=true requis)
	@if [ "$(MULTI_REGION)" != "true" ]; then \
		echo "MULTI_REGION=false dans .env — secondary désactivé"; exit 1; \
	fi
	cdk deploy $(STACKS_SECONDARY) --require-approval never --import-existing-resources || \
	  { echo ""; echo "Deploy échoué. Vérifier :"; \
	    echo "  1. make bootstrap-bucket  (bucket CDK absent après un destroy)"; \
	    echo "  2. .env correctement rempli (IOT_ENDPOINT_*, ACCOUNT_ID...)"; \
	    echo "  3. pip install -r iac/requirements.txt"; \
	    exit 1; }

.PHONY: health-urls
health-urls: ## Afficher les URLs health check des deux régions (après make deploy)
	@echo "=== Health Check URLs ==="
	@PRIMARY=$$(aws cloudformation describe-stacks --stack-name EcoSense-Primary \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?contains(OutputKey,'HealthCheckUrl')].OutputValue" \
		--output text 2>/dev/null); \
	SECONDARY=$$(aws cloudformation describe-stacks --stack-name EcoSense-Secondary \
		--region $(SECONDARY_REGION) \
		--query "Stacks[0].Outputs[?contains(OutputKey,'HealthCheckUrl')].OutputValue" \
		--output text 2>/dev/null); \
	echo "  HEALTH_URL_PRIMARY   = $$PRIMARY"; \
	echo "  HEALTH_URL_SECONDARY = $$SECONDARY"

.PHONY: deploy-route53
deploy-route53: _check-env ## Déployer les health checks Route 53 (après make deploy MULTI_REGION=true)
	@# Priorité 1 : HEALTH_URL_* déjà dans .env (strip guillemets éventuels)
	@PRIMARY_URL=$$(printenv HEALTH_URL_PRIMARY | tr -d '"'); \
	SECONDARY_URL=$$(printenv HEALTH_URL_SECONDARY | tr -d '"'); \
	if [ -z "$$PRIMARY_URL" ] || [ -z "$$SECONDARY_URL" ]; then \
		echo "HEALTH_URL_* absentes — récupération depuis CloudFormation..."; \
		PRIMARY_URL=$$(aws cloudformation describe-stacks --stack-name EcoSense-Primary \
			--region $(REGION) \
			--query "Stacks[0].Outputs[?contains(OutputKey,'HealthCheckUrl')].OutputValue" \
			--output text 2>/dev/null); \
		SECONDARY_URL=$$(aws cloudformation describe-stacks --stack-name EcoSense-Secondary \
			--region $(SECONDARY_REGION) \
			--query "Stacks[0].Outputs[?contains(OutputKey,'HealthCheckUrl')].OutputValue" \
			--output text 2>/dev/null); \
	fi; \
	if [ -z "$$PRIMARY_URL" ] || [ -z "$$SECONDARY_URL" ]; then \
		echo "Erreur : URLs introuvables — déployer d'abord : make deploy MULTI_REGION=true"; \
		exit 1; \
	fi; \
	echo "Déploiement Route 53 health checks :"; \
	echo "  Primary  : $$PRIMARY_URL"; \
	echo "  Secondary: $$SECONDARY_URL"; \
	JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 \
	HEALTH_URL_PRIMARY=$$PRIMARY_URL HEALTH_URL_SECONDARY=$$SECONDARY_URL \
		cdk deploy EcoSense-Route53 --require-approval never


.PHONY: empty-buckets
empty-buckets: ## Vider les buckets S3 archive + CDK assets (toutes régions actives)
	@if [ -z "$(FORCE)" ]; then \
		read -p "Vider les buckets S3 ? Données définitivement perdues (yes pour confirmer) : " _c; \
		if [ "$$_c" != "yes" ]; then echo "Annulé"; exit 1; fi; \
	fi
	@for bucket in $(ARCHIVE_BUCKET) $(if $(filter true,$(MULTI_REGION)),$(ARCHIVE_BUCKET_SECONDARY),); do \
		if aws s3api head-bucket --bucket $$bucket; then \
			echo "Vidage $$bucket..."; \
			aws s3 rm s3://$$bucket --recursive; \
		else \
			echo "Bucket $$bucket inexistant, ignoré"; \
		fi; \
	done
	@echo "Vidage buckets CDK assets..."
	@for bucket in $(CDK_BUCKET) $(if $(filter true,$(MULTI_REGION)),$(CDK_BUCKET_SECONDARY),); do \
		if aws s3api head-bucket --bucket $$bucket; then \
			aws s3 rm s3://$$bucket --recursive; \
		else \
			echo "Bucket $$bucket inexistant, ignoré"; \
		fi; \
	done
	@echo "Buckets vidés"

.PHONY: destroy
destroy: ## Détruire tous les stacks actifs + supprimer les buckets
	@$(MAKE) empty-buckets FORCE=1
	@if [ "$(MULTI_REGION)" = "true" ]; then \
		cdk destroy EcoSense-Route53 --force 2>/dev/null || true; \
	fi
	cdk destroy $(STACKS_ALL) --force
	@echo "Suppression des buckets résiduels..."
	@for bucket in \
		$(ARCHIVE_BUCKET) \
		$(CDK_BUCKET) \
		$(if $(filter true,$(MULTI_REGION)),$(ARCHIVE_BUCKET_SECONDARY) $(CDK_BUCKET_SECONDARY),); do \
		if aws s3api head-bucket --bucket $$bucket; then \
			aws s3 rb s3://$$bucket; \
		fi; \
	done
	@echo "Destroy complet"

# ==============================================================================
# Certificats & Simulateur MQTT
# ==============================================================================

.PHONY: certs
certs: ## Provisionner les certs X.509 pour la/les région(s) active(s)
	python simulator/provision_certs.py $(REGION)
	@if [ "$(MULTI_REGION)" = "true" ]; then \
		python simulator/provision_certs.py $(SECONDARY_REGION); \
	fi

.PHONY: certs-force
certs-force: ## Recréer les certs X.509 (--force, écrase les existants)
	python simulator/provision_certs.py $(REGION) --force
	@if [ "$(MULTI_REGION)" = "true" ]; then \
		python simulator/provision_certs.py $(SECONDARY_REGION) --force; \
	fi

.PHONY: check
check: ## Tester la connexion MQTT mTLS (exit 0/1)
	python simulator/simulator_mqtt.py --check
	@if [ "$(MULTI_REGION)" = "true" ]; then \
		python simulator/simulator_mqtt.py --check --region $(SECONDARY_REGION); \
	fi

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
	@echo "CRITICAL publié sur metropole/centre/S-TEST/telemetry ($(REGION))"

.PHONY: test-normal
test-normal: ## Publier un message NORMAL (→ S3 seulement, pas d'alerte)
	@aws iot-data publish \
		--topic "metropole/nord/S-TEST/telemetry" \
		--payload '{"sensor_id":"S-TEST","metric":"CO2","value":500,"unit":"ppm","status":"NORMAL","region":"$(REGION)","quartier":"nord","timestamp":1717256400}' \
		--cli-binary-format raw-in-base64-out \
		--region $(REGION)
	@echo "NORMAL publié sur metropole/nord/S-TEST/telemetry ($(REGION))"

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

.PHONY: post-deploy
post-deploy: ## Afficher les endpoints à copier dans .env + URLs health check (info)
	@echo ""
	@echo "Copier ces valeurs dans .env :"
	@echo "────────────────────────────────────────────────────────"
	@PRIMARY_EP=$$(aws iot describe-endpoint --endpoint-type iot:Data-ATS \
		--region $(REGION) --query 'endpointAddress' --output text 2>/dev/null); \
	echo "IOT_ENDPOINT_PRIMARY=$$PRIMARY_EP"
	@if [ "$(MULTI_REGION)" = "true" ]; then \
		SECONDARY_EP=$$(aws iot describe-endpoint --endpoint-type iot:Data-ATS \
			--region $(SECONDARY_REGION) --query 'endpointAddress' --output text 2>/dev/null); \
		echo "IOT_ENDPOINT_SECONDARY=$$SECONDARY_EP"; \
	fi
	@if [ "$(MULTI_REGION)" = "true" ]; then \
		PRIMARY_HEALTH=$$(aws cloudformation describe-stacks --stack-name EcoSense-Primary \
			--region $(REGION) \
			--query "Stacks[0].Outputs[?contains(OutputKey,'HealthCheckUrl')].OutputValue" \
			--output text 2>/dev/null); \
		SECONDARY_HEALTH=$$(aws cloudformation describe-stacks --stack-name EcoSense-Secondary \
			--region $(SECONDARY_REGION) \
			--query "Stacks[0].Outputs[?contains(OutputKey,'HealthCheckUrl')].OutputValue" \
			--output text 2>/dev/null); \
		echo "HEALTH_URL_PRIMARY=$$PRIMARY_HEALTH"; \
		echo "HEALTH_URL_SECONDARY=$$SECONDARY_HEALTH"; \
		PRIMARY_HC=$$(aws cloudformation describe-stacks --stack-name EcoSense-Route53 \
			--region $(REGION) \
			--query "Stacks[0].Outputs[?OutputKey=='PrimaryHealthCheckId'].OutputValue" \
			--output text 2>/dev/null); \
		SECONDARY_HC=$$(aws cloudformation describe-stacks --stack-name EcoSense-Route53 \
			--region $(REGION) \
			--query "Stacks[0].Outputs[?OutputKey=='SecondaryHealthCheckId'].OutputValue" \
			--output text 2>/dev/null); \
		echo "ROUTE53_HC_ID_PRIMARY=$$PRIMARY_HC"; \
		echo "ROUTE53_HC_ID_SECONDARY=$$SECONDARY_HC"; \
	fi
	@echo "────────────────────────────────────────────────────────"
	@echo ""

.PHONY: iot-endpoint
iot-endpoint: post-deploy ## Alias de post-deploy (compatibilité)

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
metrics-rules: ## Voir TopicMatch/Success/Failure des Topic Rules (10 dernières min)
	@for rule in ecosense_alert_rule ecosense_archive_rule; do \
		echo "=== $$rule ==="; \
		for metric in TopicMatch Success Failure; do \
			printf "  %-12s: " "$$metric"; \
			aws cloudwatch get-metric-statistics --namespace AWS/IoT \
				--metric-name $$metric \
				--dimensions Name=RuleName,Value=$$rule \
				--start-time $$(date -u -d '10 minutes ago' '+%Y-%m-%dT%H:%M:%S') \
				--end-time $$(date -u '+%Y-%m-%dT%H:%M:%S') \
				--period 600 --statistics Sum --region $(REGION) \
				--query 'Datapoints[0].Sum' --output text || echo "0"; \
		done; \
	done

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
clean: ## Repo fresh pour démo : supprime venv, certs, cdk.out, caches — garde .env
	@echo "Nettoyage du repo (le .env est conservé)..."
	rm -rf .venv
	rm -rf cdk.out
	rm -rf simulator/certs
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	@echo ""
	@echo "Repo nettoyé. Deux scénarios :"
	@echo ""
	@echo "  ── Démo (infra AWS déjà déployée) ───────────────────────"
	@echo "  make install      # recréer le venv + installer les dépendances"
	@echo "  make certs        # reprovisionner les certificats X.509"
	@echo "  make check        # tester la connexion MQTT"
	@echo "  make run          # lancer le simulateur"
	@echo ""
	@echo "  ── From scratch (première installation) ─────────────────"
	@echo "  make bootstrap        # venv + dépendances + bucket CDK"
	@echo "  make deploy           # déployer Primary + Secondary+ health checks Route 53"
	@echo "  make post-deploy      # vérifier/copier les endpoints dans .env"
	@echo "  make certs            # provisionner les certificats X.509"
	@echo "  make check            # tester la connexion MQTT"
	@echo "  make run              # lancer le simulateur"
