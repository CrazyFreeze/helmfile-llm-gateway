import os
import re
import json
import yaml
from fastapi import FastAPI
from kubernetes import client, config
from fastmcp import FastMCP

config.load_incluster_config()
v1 = client.CoreV1Api()
mcp = FastMCP("K8s SRE Diagnostic MCP")
BASELINE_PATH = os.getenv("PORTS_BASELINE_PATH", "/config/baseline.yaml")

def _validate_ns(namespace: str) -> str | None:
    if not namespace or not re.match(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", namespace):
        return f"Invalid Kubernetes namespace name: '{namespace}'"
    return None

@mcp.tool()
def list_pods(namespace: str) -> str:
    """Get a list of all pods and their statuses in the specified Kubernetes namespace."""
    if err := _validate_ns(namespace):
        return err
    try:
        pods = v1.list_namespaced_pod(namespace=namespace)
        result = []
        for p in pods.items:
            restarts = sum(cs.restart_count for cs in (p.status.container_statuses or []))
            result.append(f"{p.metadata.name} | Phase: {p.status.phase} | Restarts: {restarts}")
        return "\n".join(result) or f"No pods found in namespace '{namespace}'"
    except Exception as e:
        return f"Error listing pods in '{namespace}': {str(e)}"

@mcp.tool()
def get_pod_logs(namespace: str, pod_name: str, tail_lines: int = 50) -> str:
    """Retrieve the latest log lines for the specified pod in a namespace."""
    if err := _validate_ns(namespace):
        return err
    try:
        return v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=tail_lines,
        )
    except Exception as e:
        return f"Error reading logs for '{pod_name}' in '{namespace}': {str(e)}"

@mcp.tool()
def audit_exposed_ports(namespace: str) -> str:
    """Audit exposed container and service ports in a Kubernetes namespace against the allowed security baseline matrix."""
    if err := _validate_ns(namespace):
        return err
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            baseline = yaml.safe_load(f) or {}

        pods = v1.list_namespaced_pod(namespace=namespace)
        services = v1.list_namespaced_service(namespace=namespace)

        actual_state = {"pods": [], "services": []}

        for p in pods.items:
            pod_ports = []
            for c in p.spec.containers:
                for port in (c.ports or []):
                    pod_ports.append({
                        "container": c.name,
                        "port": port.container_port,
                        "protocol": port.protocol,
                    })
            actual_state["pods"].append({"name": p.metadata.name, "ports": pod_ports})

        for s in services.items:
            svc_ports = [
                {
                    "port": sp.port,
                    "targetPort": str(sp.target_port),
                    "protocol": sp.protocol,
                }
                for sp in (s.spec.ports or [])
            ]
            actual_state["services"].append({
                "name": s.metadata.name,
                "type": s.spec.type,
                "ports": svc_ports,
            })

        return json.dumps(
            {
                "allowed_baseline_matrix": baseline.get(namespace, "There are no rules for this namespace."),
                "actual_cluster_state": actual_state,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return f"Port audit error: {e}"

mcp_app = mcp.http_app(path="/")

app = FastAPI(title="K8s SRE Diagnostic Agent & MCP", lifespan=mcp_app.router.lifespan_context)

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

app.mount("/mcp", mcp_app)
