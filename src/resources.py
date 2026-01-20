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
        # Network alias should be the selector value (e.g., 'health' from {'app': 'health'})
        # This allows curl http://health:2000 to reach the service
        aliases = list(self.selector.values()) if self.selector else [self.name]

        return {
            "containers": [
                {
                    "name": "nginx",
                    "image": "nginx:alpine",
                    "env": {},
                    "network_aliases": aliases,
                }
            ]
        }
