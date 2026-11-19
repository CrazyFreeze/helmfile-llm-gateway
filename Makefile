SHELL := /bin/bash
ENV ?= minikube
SECRET_FILE := bases/private/$(ENV).yaml
SECRET_EXAMPLE := bases/private/$(ENV).example.yaml
EVAL_DIR := evals
RELEASE ?=

.PHONY: ensure-secrets diff apply deploy destroy eval eval-view

ensure-secrets:
	@if [ ! -f $(SECRET_FILE) ]; then \
		mkdir -p $$(dirname $(SECRET_FILE)); \
		echo "$(SECRET_FILE) не найден. Создаю копию из $(SECRET_EXAMPLE)..."; \
		cp $(SECRET_EXAMPLE) $(SECRET_FILE); \
		echo "Пожалуйста, заполните реальные значения в $(SECRET_FILE) и запустите команду снова."; \
		exit 1; \
	fi
	@if grep -q "CHANGE_ME" $(SECRET_FILE); then \
		echo "В файле $(SECRET_FILE) остались незаполненные заглушки (CHANGE_ME)!"; \
		exit 1; \
	fi

diff: ensure-secrets
	helmfile -e $(ENV) $(if $(RELEASE),-l app=$(RELEASE),) diff --suppress-secrets

apply: ensure-secrets
	helmfile -e $(ENV) $(if $(RELEASE),-l app=$(RELEASE),) apply

eval:
	@if [ ! -f $(EVAL_DIR)/.env ]; then \
		echo "Файл $(EVAL_DIR)/.env не найден! Скопируйте его из $(EVAL_DIR)/.env.example"; \
		exit 1; \
	fi
	@set -a && source $(EVAL_DIR)/.env && set +a && \
	export LITELLM_MODEL=$${LITELLM_MODEL:-$$(grep -m 1 'model_name:' bases/envs/$(ENV).yaml.gotmpl | awk -F'"' '{print $$2}')} && \
	echo "Running evals against model: $$LITELLM_MODEL" && \
	npx --yes promptfoo@latest eval -c $(EVAL_DIR)/promptfooconfig.yaml --no-cache

eval-view:
	npx promptfoo@latest view

destroy: ensure-secrets
	helmfile -e $(ENV) $(if $(RELEASE),-l name=$(RELEASE),) destroy

deploy: apply eval