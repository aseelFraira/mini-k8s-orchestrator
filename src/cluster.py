"""
Main cluster orchestrator.
"""

import subprocess
import yaml
from typing import Dict, List, Any, Optional

from resources import Resource, PodResource, ServiceResource, ReplicaSetResource
from store import ResourceStore
from controllers import PodController, ServiceController, ReplicaSetController


# Network name for inter-pod communication
ORCHESTRATOR_NETWORK = "orchestrator-net"


def ensure_network_exists():
    """Create the orchestrator network if it doesn't exist."""
    result = subprocess.run(
        ["podman", "network", "exists", ORCHESTRATOR_NETWORK], capture_output=True
    )
    if result.returncode != 0:
        subprocess.run(
            ["podman", "network", "create", ORCHESTRATOR_NETWORK],
            check=True,
            capture_output=True,
        )
        print(f"Created network: {ORCHESTRATOR_NETWORK}")
    else:
        print(f"Network {ORCHESTRATOR_NETWORK} already exists")


class OrchestratorCluster:
    """Main cluster orchestrator"""

    def __init__(self):
        self.store = ResourceStore()
        self.pod_controller = None
        self.service_controller = None
        self.replicaset_controller = None
        self._started = False

    def start(self):
        """Start the cluster"""
        if self._started:
            return

        # Ensure the orchestrator network exists before starting anything
        ensure_network_exists()

        # Initialize controllers
        self.pod_controller = PodController(self.store)
        self.service_controller = ServiceController(self.store, self.pod_controller)
        self.replicaset_controller = ReplicaSetController(self.store)

        # Start controllers
        self.pod_controller.start()
        self.service_controller.start()
        self.replicaset_controller.start()

        self._started = True
        print("Orchestrator cluster started")

    def stop(self):
        """Stop the cluster"""
        if self.pod_controller:
            self.pod_controller.stop()
        if self.service_controller:
            self.service_controller.stop()
        if self.replicaset_controller:
            self.replicaset_controller.stop()
        self._started = False
        print("Orchestrator cluster stopped")

    def apply_resource(
        self, resource_dict: Dict[str, Any], namespace: str = "default"
    ) -> Resource:
        """Apply a resource from a dict (Kubernetes-style)"""
        kind = resource_dict.get("kind")
        metadata = resource_dict.get("metadata", {})
        name = metadata.get("name")
        ns = metadata.get("namespace", namespace)
        spec = resource_dict.get("spec", {})

        clean_metadata = {
            k: v for k, v in metadata.items() if k not in ("name", "namespace")
        }

        if not kind or not name:
            raise ValueError(f"Invalid resource: missing kind or name: {resource_dict}")

        if kind == "Pod":
            resource = PodResource(name, spec, clean_metadata, ns)
        elif kind == "Service":
            resource = ServiceResource(name, spec, clean_metadata, ns)
        elif kind == "ReplicaSet":
            resource = ReplicaSetResource(name, spec, clean_metadata, ns)
        else:
            raise ValueError(f"Unknown resource kind: {kind}")

        return self.store.create_or_update(resource)

    def apply_yaml(self, yaml_content: str, namespace: str = "default"):
        """Apply YAML resource definitions"""
        docs = yaml.safe_load_all(yaml_content)

        for doc in docs:
            if not doc:
                continue
            try:
                resource = self.apply_resource(doc, namespace)
                print(f"Applied {resource.kind}: {resource.namespace}/{resource.name}")
            except Exception as e:
                print(f"Error applying resource: {e}")

    def delete_resource(self, kind: str, name: str, namespace: str = "default"):
        """Delete a resource"""
        if self.store.delete(kind, name, namespace):
            print(f"Deleted {kind}: {namespace}/{name}")
        else:
            print(f"{kind} {namespace}/{name} not found")

    def get_resource(
        self, kind: str, name: str, namespace: str = "default"
    ) -> Optional[Resource]:
        """Get a resource"""
        return self.store.get(kind, name, namespace)

    def list_resources(self, kind: str, namespace: str = None) -> List[Resource]:
        """List resources"""
        return self.store.list(kind, namespace)
