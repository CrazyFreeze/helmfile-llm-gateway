SHELL := /bin/bash
ENV ?= minikube
SECRET_FILE := bases/private/$(ENV).yaml
SECRET_EXAMPLE := bases/private/$(ENV).example.yaml
EVAL_DIR := evals
RELEASE ?=
TEST_IMAGE ?= mirror.gcr.io/library/busybox:1.36

.PHONY: ensure-secrets diff apply deploy destroy eval eval-view netpol

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

netpol:
	@NETPOL_ENABLED=$$(kubectl get networkpolicy -n litellm default-deny-all >/dev/null 2>&1 && echo "true" || echo "false"); \
	if [ "$$NETPOL_ENABLED" = "false" ]; then \
		echo "NetworkPolicies отключены. Пропуск сетевого аудита."; \
		exit 0; \
	fi; \
	echo "Запуск аудита сетевой изоляции (Zero Trust NetworkPolicies)..."; \
	\
	if kubectl run nettest-rag-ok -n anythingllm --image=$(TEST_IMAGE) --labels="app.kubernetes.io/name=anythingllm" --restart=Never --rm --attach -- \
		sh -c "sleep 2 && nc -zv -w 3 litellm.litellm.svc.cluster.local 4000" >/dev/null 2>&1; then \
		echo "  [1/4] РАЗРЕШЕНО: RAG -> LiteLLM (:4000)"; \
	else \
		echo "  [1/4] ОШИБКА: Нет доступа RAG -> LiteLLM (:4000)"; exit 1; \
	fi; \
	\
	if ! kubectl run nettest-rag-db -n anythingllm --image=$(TEST_IMAGE) --labels="app.kubernetes.io/name=anythingllm" --restart=Never --rm --attach -- \
		sh -c "sleep 2 && nc -zv -w 3 litellm-postgresql.litellm.svc.cluster.local 5432" >/dev/null 2>&1; then \
		echo "  [2/4] ЗАБЛОКИРОВАНО: RAG -> PostgreSQL (:5432)"; \
	else \
		echo "  [2/4] ОШИБКА: Открыт прямой доступ RAG -> PostgreSQL (:5432)!"; exit 1; \
	fi; \
	\
	if ! kubectl run nettest-gw-rag -n litellm --image=$(TEST_IMAGE) --labels="app.kubernetes.io/name=litellm" --restart=Never --rm --attach -- \
		sh -c "sleep 2 && nc -zv -w 3 anythingllm.anythingllm.svc.cluster.local 3001" >/dev/null 2>&1; then \
		echo "  [3/4] ЗАБЛОКИРОВАНО: LiteLLM -> AnythingLLM (:3001)"; \
	else \
		echo "  [3/4] ОШИБКА: Открыт доступ LiteLLM -> AnythingLLM (:3001)!"; exit 1; \
	fi; \
	\
	if ! kubectl run nettest-db-egress -n litellm --image=$(TEST_IMAGE) --labels="app.kubernetes.io/name=postgresql" --restart=Never --rm --attach -- \
		sh -c "sleep 2 && nc -zv -w 3 1.1.1.1 443" >/dev/null 2>&1; then \
		echo "  [4/4] ЗАБЛОКИРОВАНО: PostgreSQL -> Внешний мир (:443)"; \
	else \
		echo "  [4/4] ОШИБКА: Открыт исходящий трафик из PostgreSQL!"; exit 1; \
	fi; \
	\
	if kubectl get ns agent >/dev/null 2>&1; then \
		echo "Проверка изоляции контура SRE Agent (MCP)..."; \
		if kubectl run nettest-rag-mcp -n anythingllm --image=$(TEST_IMAGE) --labels="app.kubernetes.io/name=anythingllm" --restart=Never --rm --attach -- \
			sh -c "sleep 2 && nc -zv -w 3 sre-agent.agent.svc.cluster.local 8000" >/dev/null 2>&1; then \
			echo "  [5/8] РАЗРЕШЕНО: RAG -> SRE Agent MCP (:8000)"; \
		else \
			echo "  [5/8] ОШИБКА: Нет доступа RAG -> SRE Agent MCP (:8000)"; exit 1; \
		fi; \
		if ! kubectl run nettest-agent-gw -n agent --image=$(TEST_IMAGE) --labels="app=sre-agent" --restart=Never --rm --attach -- \
			sh -c "sleep 2 && nc -zv -w 3 litellm.litellm.svc.cluster.local 4000" >/dev/null 2>&1; then \
			echo "  [6/8] ЗАБЛОКИРОВАНО: SRE Agent -> LiteLLM (:4000)"; \
		else \
			echo "  [6/8] ОШИБКА: Открыт прямой доступ SRE Agent -> LiteLLM (:4000)"; exit 1; \
		fi; \
		if ! kubectl run nettest-agent-db -n agent --image=$(TEST_IMAGE) --labels="app=sre-agent" --restart=Never --rm --attach -- \
			sh -c "sleep 2 && nc -zv -w 3 litellm-postgresql.litellm.svc.cluster.local 5432" >/dev/null 2>&1; then \
			echo "  [7/8] ЗАБЛОКИРОВАНО: SRE Agent -> PostgreSQL (:5432)"; \
		else \
			echo "  [7/8] ОШИБКА: Открыт прямой доступ SRE Agent -> PostgreSQL (:5432)!"; exit 1; \
		fi; \
		if ! kubectl run nettest-agent-inet -n agent --image=$(TEST_IMAGE) --labels="app=sre-agent" --restart=Never --rm --attach -- \
			sh -c "sleep 2 && nc -zv -w 3 1.1.1.1 443" >/dev/null 2>&1; then \
			echo "  [8/8] ЗАБЛОКИРОВАНО: SRE Agent -> Внешний мир (:443)"; \
		else \
			echo "  [8/8] ОШИБКА: Открыт выход SRE Agent во внешний интернет (:443)!"; exit 1; \
		fi; \
	fi; \
	\
	echo "Сетевой аудит успешно пройден: контур Zero Trust подтвержден."

deploy: apply netpol eval