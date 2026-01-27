"""
Resource definitions for the orchestrator.
"""

import hashlib
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class ResourceType(Enum):
    POD = "pod"
    SERVICE = "service"
    REPLICASET = "replicaset"


@dataclass
class Resource:
    """Base resource class"""

    api_version: str
    kind: str
    name: str
    namespace: str
    spec: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert resource to Kubernetes-style dict"""
        return {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": {
                "name": self.name,
                "namespace": self.namespace,
                **self.metadata,
            },
            "spec": self.spec,
            "status": self.status,
        }


@dataclass
class PodResource(Resource):
    """Pod resource definition"""

    def __init__(
        self,
        name: str,
        spec: Dict[str, Any],
        metadata: Dict[str, Any] = None,
        namespace: str = "default",
    ):
        # Validate name length
        if len(name) > 253:
            raise ValueError(f"Pod name '{name[:50]}...' exceeds maximum length of 253 characters")

        # Validate containers
        containers = spec.get("containers", [])
        if not containers:
            raise ValueError("Pod spec must contain at least one container")

        for i, container in enumerate(containers):
            if not container.get("name"):
                raise ValueError(f"Container at index {i} must have a name")
            if "image" not in container or container.get("image") is None:
                raise ValueError(f"Container '{container.get('name', i)}' must have an image")

        super().__init__(
            api_version="v1",
            kind="Pod",
            name=name,
            namespace=namespace,
            spec=spec,
            metadata=metadata or {},
        )
        self.spec_hash = self._generate_spec_hash(spec)

    @property
    def containers(self) -> List[Dict[str, Any]]:
        return self.spec.get("containers", [])

    @property
    def first_container(self) -> Optional[Dict[str, Any]]:
        containers = self.containers
        return containers[0] if containers else None

    @property
    def image(self) -> Optional[str]:
        container = self.first_container
        return container.get("image") if container else None

    @property
    def env(self) -> Dict[str, Any]:
        container = self.first_container
        if container:
            env = container.get("env", {})
            if isinstance(env, list):
                return {
                    item["name"]: item["value"]
                    for item in env
                    if "name" in item and "value" in item
                }
            return env
        return {}

    @property
    def labels(self) -> Dict[str, str]:
        return self.metadata.get("labels", {})

    def get_container_spec(self, container_name: str):
        for spec in self.containers:
            if spec.get("name") == container_name:
                return spec
        return None

    def _generate_spec_hash(self, spec: Dict[str, Any]) -> str:
        """Internal helper to create a deterministic hash of the spec"""
        spec_json = json.dumps(spec, sort_keys=True)
        return hashlib.sha256(spec_json.encode()).hexdigest()


@dataclass
class ServiceResource(Resource):
    """Service resource definition"""

    def __init__(
        self,
        name: str,
        spec: Dict[str, Any],
        metadata: Dict[str, Any] = None,
        namespace: str = "default",
    ):
        # Validate ports
        ports = spec.get("ports")
        if not ports:
            raise ValueError("Service spec must contain ports")

        super().__init__(
            api_version="v1",
            kind="Service",
            name=name,
            namespace=namespace,
            spec=spec,
            metadata=metadata or {},
        )

    @property
    def selector(self) -> Dict[str, str]:
        return self.spec.get("selector", {})

    @property
    def ports(self) -> List[Dict[str, Any]]:
        return self.spec.get("ports", [])

    @property
    def service_type(self) -> str:
        return self.spec.get("type", "ClusterIP")

    @property
    def proxy_pod_spec(self) -> Dict[str, Any]:
        """Returns the full Pod spec for the Nginx shadow pod"""
        # Network alias is the SERVICE NAME (e.g., 'health-service')
        # This allows: curl http://health-service:2000 to reach the load balancer
        aliases = [self.name]

        # Get the service port for host exposure
        host_ports = []
        for port_mapping in self.ports:
            port = port_mapping.get("port")
            if port:
                host_ports.append(port)

        return {
            "containers": [
                {
                    "name": "nginx",
                    "image": "nginx:alpine",
                    "env": {},
                    "network_aliases": aliases,
                    "ports": host_ports,
                }
            ]
        }


@dataclass
class ReplicaSetResource(Resource):
    """ReplicaSet resource definition"""

    def __init__(
        self,
        name: str,
        spec: Dict[str, Any],
        metadata: Dict[str, Any] = None,
        namespace: str = "default",
    ):
        # Validate selector
        if "selector" not in spec:
            raise ValueError("ReplicaSet spec must contain a selector")

        # Validate template
        if "template" not in spec:
            raise ValueError("ReplicaSet spec must contain a template")

        # Validate replicas if present
        if "replicas" in spec:
            replicas = spec["replicas"]
            if not isinstance(replicas, int):
                raise ValueError(f"replicas must be an integer, got {type(replicas).__name__}")
            if replicas < 0:
                raise ValueError(f"replicas must be >= 0, got {replicas}")

        super().__init__(
            api_version="apps/v1",
            kind="ReplicaSet",
            name=name,
            namespace=namespace,
            spec=spec,
            metadata=metadata or {},
        )

    @property
    def replicas(self) -> int:
        return self.spec.get("replicas", 1)

    @property
    def selector(self) -> Dict[str, str]:
        return self.spec.get("selector", {})

    @property
    def template(self) -> Dict[str, Any]:
        return self.spec.get("template", {})
