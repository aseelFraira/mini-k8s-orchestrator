"""
FastAPI REST API for the orchestrator.
This is the main entry point for the application.
"""

import uvicorn
from typing import Dict, List, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from resources import Resource
from cluster import OrchestratorCluster


# Global cluster instance
cluster = OrchestratorCluster()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    cluster.start()
    yield
    cluster.stop()


app = FastAPI(
    title="Orchestrator API",
    description="Kubernetes-like orchestration system",
    version="1.0.0",
    lifespan=lifespan,
)


# Pydantic models for request/response
class ResourceMetadata(BaseModel):
    name: str
    namespace: Optional[str] = "default"
    labels: Optional[Dict[str, str]] = None
    model_config = {"extra": "allow"}


class ResourceRequest(BaseModel):
    apiVersion: str
    kind: str
    metadata: Dict[str, Any]
    spec: Dict[str, Any]
    model_config = {"extra": "allow"}


def resource_response(resource: Resource) -> Dict[str, Any]:
    return resource.to_dict()


def resources_list_response(kind: str, resources: List[Resource]) -> Dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": f"{kind}List",
        "items": [r.to_dict() for r in resources],
    }


# =============================================================================
# Pod Endpoints
# =============================================================================


@app.post("/api/v1/namespaces/{namespace}/pods")
async def create_pod(namespace: str, resource: ResourceRequest):
    """Create a Pod"""
    if resource.kind != "Pod":
        raise HTTPException(400, f"Expected kind Pod, got {resource.kind}")

    resource_dict = resource.model_dump()
    resource_dict["metadata"]["namespace"] = namespace

    try:
        created = cluster.apply_resource(resource_dict, namespace)
        return JSONResponse(status_code=201, content=resource_response(created))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/v1/namespaces/{namespace}/pods")
async def list_pods(namespace: str):
    """List all Pods in a namespace"""
    pods = cluster.list_resources("Pod", namespace)
    return resources_list_response("Pod", pods)


@app.get("/api/v1/namespaces/{namespace}/pods/{name}")
async def get_pod(namespace: str, name: str):
    """Get a specific Pod"""
    pod = cluster.get_resource("Pod", name, namespace)
    if not pod:
        raise HTTPException(404, f"Pod {namespace}/{name} not found")
    return resource_response(pod)


@app.delete("/api/v1/namespaces/{namespace}/pods/{name}")
async def delete_pod(namespace: str, name: str):
    """Delete a Pod"""
    if cluster.store.delete("Pod", name, namespace):
        return {"status": "Success", "message": f"Pod {namespace}/{name} deleted"}
    raise HTTPException(404, f"Pod {namespace}/{name} not found")


@app.put("/api/v1/namespaces/{namespace}/pods/{name}")
async def update_pod(namespace: str, name: str, resource: ResourceRequest):
    """Update a Pod"""
    if resource.kind != "Pod":
        raise HTTPException(400, f"Expected kind Pod, got {resource.kind}")

    existing = cluster.get_resource("Pod", name, namespace)
    if not existing:
        raise HTTPException(404, f"Pod {namespace}/{name} not found")

    resource_dict = resource.model_dump()
    resource_dict["metadata"]["namespace"] = namespace
    resource_dict["metadata"]["name"] = name

    try:
        updated = cluster.apply_resource(resource_dict, namespace)
        return resource_response(updated)
    except ValueError as e:
        raise HTTPException(400, str(e))


# =============================================================================
# Service Endpoints
# =============================================================================


@app.post("/api/v1/namespaces/{namespace}/services")
async def create_service(namespace: str, resource: ResourceRequest):
    """Create a Service"""
    if resource.kind != "Service":
        raise HTTPException(400, f"Expected kind Service, got {resource.kind}")

    resource_dict = resource.model_dump()
    resource_dict["metadata"]["namespace"] = namespace

    try:
        created = cluster.apply_resource(resource_dict, namespace)
        return JSONResponse(status_code=201, content=resource_response(created))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/v1/namespaces/{namespace}/services")
async def list_services(namespace: str):
    """List all Services in a namespace"""
    services = cluster.list_resources("Service", namespace)
    return resources_list_response("Service", services)


@app.get("/api/v1/namespaces/{namespace}/services/{name}")
async def get_service(namespace: str, name: str):
    """Get a specific Service"""
    svc = cluster.get_resource("Service", name, namespace)
    if not svc:
        raise HTTPException(404, f"Service {namespace}/{name} not found")
    return resource_response(svc)


@app.delete("/api/v1/namespaces/{namespace}/services/{name}")
async def delete_service(namespace: str, name: str):
    """Delete a Service"""
    if cluster.store.delete("Service", name, namespace):
        return {"status": "Success", "message": f"Service {namespace}/{name} deleted"}
    raise HTTPException(404, f"Service {namespace}/{name} not found")


@app.put("/api/v1/namespaces/{namespace}/services/{name}")
async def update_service(namespace: str, name: str, resource: ResourceRequest):
    """Update a Service"""
    if resource.kind != "Service":
        raise HTTPException(400, f"Expected kind Service, got {resource.kind}")

    existing = cluster.get_resource("Service", name, namespace)
    if not existing:
        raise HTTPException(404, f"Service {namespace}/{name} not found")

    resource_dict = resource.model_dump()
    resource_dict["metadata"]["namespace"] = namespace
    resource_dict["metadata"]["name"] = name

    try:
        updated = cluster.apply_resource(resource_dict, namespace)
        return resource_response(updated)
    except ValueError as e:
        raise HTTPException(400, str(e))


# =============================================================================
# ReplicaSet Endpoints
# =============================================================================


@app.post("/api/apps/v1/namespaces/{namespace}/replicasets")
async def create_replicaset(namespace: str, resource: ResourceRequest):
    """Create a ReplicaSet"""
    if resource.kind != "ReplicaSet":
        raise HTTPException(400, f"Expected kind ReplicaSet, got {resource.kind}")

    resource_dict = resource.model_dump()
    resource_dict["metadata"]["namespace"] = namespace

    try:
        created = cluster.apply_resource(resource_dict, namespace)
        return JSONResponse(status_code=201, content=resource_response(created))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/apps/v1/namespaces/{namespace}/replicasets")
async def list_replicasets(namespace: str):
    """List all ReplicaSets in a namespace"""
    replicasets = cluster.list_resources("ReplicaSet", namespace)
    return resources_list_response("ReplicaSet", replicasets)


@app.get("/api/apps/v1/namespaces/{namespace}/replicasets/{name}")
async def get_replicaset(namespace: str, name: str):
    """Get a specific ReplicaSet"""
    rs = cluster.get_resource("ReplicaSet", name, namespace)
    if not rs:
        raise HTTPException(404, f"ReplicaSet {namespace}/{name} not found")
    return resource_response(rs)


@app.delete("/api/apps/v1/namespaces/{namespace}/replicasets/{name}")
async def delete_replicaset(namespace: str, name: str):
    """Delete a ReplicaSet"""
    if cluster.store.delete("ReplicaSet", name, namespace):
        return {
            "status": "Success",
            "message": f"ReplicaSet {namespace}/{name} deleted",
        }
    raise HTTPException(404, f"ReplicaSet {namespace}/{name} not found")


@app.put("/api/apps/v1/namespaces/{namespace}/replicasets/{name}")
async def update_replicaset(namespace: str, name: str, resource: ResourceRequest):
    """Update a ReplicaSet"""
    if resource.kind != "ReplicaSet":
        raise HTTPException(400, f"Expected kind ReplicaSet, got {resource.kind}")

    existing = cluster.get_resource("ReplicaSet", name, namespace)
    if not existing:
        raise HTTPException(404, f"ReplicaSet {namespace}/{name} not found")

    resource_dict = resource.model_dump()
    resource_dict["metadata"]["namespace"] = namespace
    resource_dict["metadata"]["name"] = name

    try:
        updated = cluster.apply_resource(resource_dict, namespace)
        return resource_response(updated)
    except ValueError as e:
        raise HTTPException(400, str(e))


# =============================================================================
# Health and Info Endpoints
# =============================================================================


@app.get("/healthz")
async def healthz():
    """Health check endpoint"""
    return {"status": "ok"}


@app.get("/api/v1/namespaces")
async def list_namespaces():
    """List all namespaces"""
    return {
        "apiVersion": "v1",
        "kind": "NamespaceList",
        "items": [
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "default"}}
        ],
    }


# =============================================================================
# Main
# =============================================================================


def main():
    """Run the orchestrator server"""
    import argparse

    parser = argparse.ArgumentParser(description="Orchestrator API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=3000, help="Port to bind to")
    args = parser.parse_args()

    print(f"Starting Orchestrator API on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
