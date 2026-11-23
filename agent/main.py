import os
import re
import json
import yaml
from fastapi import FastAPI, HTTPException
from kubernetes import client, config
from fastmcp import FastMCP
from kubernetes.client.rest import ApiException

config.load_incluster_config()
v1 = client.CoreV1Api()
mcp = FastMCP("K8s SRE Diagnostic MCP")
BASELINE_PATH = os.getenv("PORTS_BASELINE_PATH", "/config/baseline.yaml")

NS_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
RESOURCE_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
MAX_LOG_LINES = 200
MAX_LOG_BYTES = 32768

def _validate_name(value: str, pattern: re.Pattern, field_name: str) -> str | None:
    if not value or len(value) > 253 or not pattern.match(value):
        return f"Invalid Kubernetes {field_name}: '{value}'"
    return None


def _get_pod_detailed_status(pod) -> str:
    if pod.metadata.deletion_timestamp:
        return "Terminating"

    for cs in (pod.status.init_container_statuses or []):
        if cs.state.waiting and cs.state.waiting.reason:
            return f"Init:{cs.state.waiting.reason}"
        if cs.state.terminated and cs.state.terminated.exit_code != 0:
            return f"Init:Error({cs.state.terminated.reason or cs.state.terminated.exit_code})"

    for cs in (pod.status.container_statuses or []):
        if cs.state.waiting and cs.state.waiting.reason:
            return cs.state.waiting.reason
        if cs.state.terminated and cs.state.terminated.exit_code != 0:
            return cs.state.terminated.reason or f"Error({cs.state.terminated.exit_code})"

    return pod.status.phase or "Unknown"


@mcp.tool()
def list_pods(namespace: str) -> str:
    """Get a list of all pods, their detailed statuses, restart counts, and container names in the specified namespace."""
    if err := _validate_name(namespace, NS_PATTERN, "namespace"):
        return err

    try:
        pods = v1.list_namespaced_pod(namespace=namespace)
        result = []
        for p in pods.items:
            restarts = sum(cs.restart_count for cs in (p.status.container_statuses or []))
            status = _get_pod_detailed_status(p)
            containers = [c.name for c in p.spec.containers]
            result.append(
                f"{p.metadata.name} | Status: {status} | Restarts: {restarts} | Containers: {','.join(containers)}"
            )
        return "\n".join(result) or f"No pods found in namespace '{namespace}'"
    except ApiException as e:
        return f"Kubernetes API error listing pods in '{namespace}': {e.reason} ({e.status})"
    except Exception as e:
        return f"Error listing pods in '{namespace}': {str(e)}"

@mcp.tool()
def get_pod_logs(
    namespace: str,
    pod_name: str,
    tail_lines: int = 50,
    container: str | None = None,
) -> str:
    """Retrieve the latest log lines for a pod (and optional container) in a namespace."""
    if err := _validate_name(namespace, NS_PATTERN, "namespace"):
        return err
    if err := _validate_name(pod_name, RESOURCE_PATTERN, "pod_name"):
        return err
    if container and (err := _validate_name(container, NS_PATTERN, "container")):
        return err

    safe_tail = min(max(1, tail_lines), MAX_LOG_LINES)

    try:
        if not container:
            pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            if len(pod.spec.containers) > 1:
                container = pod.spec.containers[0].name

        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=safe_tail,
            limit_bytes=MAX_LOG_BYTES,
        )
        return logs or f"Logs are empty for pod '{pod_name}' in '{namespace}'."
    except ApiException as e:
        return f"Kubernetes API error reading logs for '{pod_name}': {e.reason} ({e.status})"
    except Exception as e:
        return f"Error reading logs for '{pod_name}' in '{namespace}': {str(e)}"

@mcp.tool()
def audit_exposed_ports(namespace: str) -> str:
    """Audit exposed container and service ports in a Kubernetes namespace against the allowed security baseline matrix."""
    if err := _validate_name(namespace, NS_PATTERN, "namespace"):
        return err

    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            baseline = yaml.safe_load(f) or {}

        ns_rules = baseline.get(namespace)
        pods = v1.list_namespaced_pod(namespace=namespace)
        services = v1.list_namespaced_service(namespace=namespace)

        actual_state = {"pods": [], "services": []}
        violations = []

        allowed_pod_ports = set()
        allowed_svcs_map = {}
        if isinstance(ns_rules, dict):
            allowed_pod_ports = set(ns_rules.get("allowed_container_ports", []))
            for svc_rule in ns_rules.get("allowed_services", []):
                allowed_svcs_map[svc_rule["name"]] = {
                    "type": svc_rule.get("type", "ClusterIP"),
                    "ports": set(svc_rule.get("ports", [])),
                }

        for p in pods.items:
            pod_ports = []
            for c in p.spec.containers:
                for port in (c.ports or []):
                    pod_ports.append({
                        "container": c.name,
                        "port": port.container_port,
                        "protocol": port.protocol,
                    })
                    if isinstance(ns_rules, dict) and port.container_port not in allowed_pod_ports:
                        violations.append(
                            f"Pod '{p.metadata.name}' (container '{c.name}') exposes unauthorized port {port.container_port}/{port.protocol}"
                        )
            actual_state["pods"].append({"name": p.metadata.name, "ports": pod_ports})

        for s in services.items:
            svc_name = s.metadata.name
            svc_type = s.spec.type
            svc_ports = []

            rule = allowed_svcs_map.get(svc_name) if isinstance(ns_rules, dict) else None
            if isinstance(ns_rules, dict) and rule is None:
                violations.append(f"Unauthorized Service '{svc_name}' (type: {svc_type}) found in namespace '{namespace}'")
            elif rule and svc_type != rule["type"]:
                violations.append(
                    f"Service '{svc_name}' has type '{svc_type}', expected '{rule['type']}'"
                )

            for sp in (s.spec.ports or []):
                svc_ports.append({
                    "port": sp.port,
                    "targetPort": str(sp.target_port),
                    "protocol": sp.protocol,
                })
                if rule and sp.port not in rule["ports"]:
                    violations.append(
                        f"Service '{svc_name}' exposes unauthorized port {sp.port}/{sp.protocol}"
                    )

            actual_state["services"].append({
                "name": svc_name,
                "type": svc_type,
                "ports": svc_ports,
            })

        return json.dumps(
            {
                "namespace": namespace,
                "audit_status": "VIOLATIONS_FOUND" if violations else "COMPLIANT",
                "detected_violations": violations,
                "allowed_baseline_matrix": ns_rules or "There are no rules for this namespace.",
                "actual_cluster_state": actual_state,
            },
            ensure_ascii=False,
            indent=2,
        )
    except ApiException as e:
        return f"Kubernetes API error during port audit in '{namespace}': {e.reason} ({e.status})"
    except Exception as e:
        return f"Port audit error: {e}"

mcp_app = mcp.http_app(path="/")

app = FastAPI(title="K8s SRE Diagnostic Agent & MCP", lifespan=mcp_app.router.lifespan_context)

@app.get("/healthz")
def healthz():
    if not os.path.exists(BASELINE_PATH):
        raise HTTPException(status_code=503, detail=f"Baseline config missing at {BASELINE_PATH}")
    return {"status": "ok"}

app.mount("/mcp", mcp_app)
