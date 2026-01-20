"""
In-memory resource store for the orchestrator.
"""
import threading
from typing import Dict, List, Optional

from resources import Resource


class ResourceStore:
    """In-memory resource tree"""

    def __init__(self):
        self.resources: Dict[str, Dict[str, Dict[str, Resource]]] = {
            "Pod": {},  # namespace -> name -> resource
            "Service": {},
        }
        self.lock = threading.RLock()

    def _ensure_namespace(self, kind: str, namespace: str):
        """Ensure namespace dict exists"""
        if namespace not in self.resources[kind]:
            self.resources[kind][namespace] = {}

    def create(self, resource: Resource) -> Resource:
        """Create a resource"""
        with self.lock:
            self._ensure_namespace(resource.kind, resource.namespace)
            if resource.name in self.resources[resource.kind][resource.namespace]:
                raise ValueError(
                    f"{resource.kind} {resource.namespace}/{resource.name} already exists"
                )
            self.resources[resource.kind][resource.namespace][resource.name] = resource
            return resource

    def get(
        self, kind: str, name: str, namespace: str = "default"
    ) -> Optional[Resource]:
        """Get a resource by kind, namespace, and name"""
        with self.lock:
            return self.resources.get(kind, {}).get(namespace, {}).get(name)

    def list(self, kind: str, namespace: str = None) -> List[Resource]:
        """List all resources of a kind, optionally filtered by namespace"""
        with self.lock:
            if namespace:
                return list(self.resources.get(kind, {}).get(namespace, {}).values())
            else:
                result = []
                for ns_resources in self.resources.get(kind, {}).values():
                    result.extend(ns_resources.values())
                return result

    def update(self, resource: Resource) -> Resource:
        """Update a resource"""
        with self.lock:
            self._ensure_namespace(resource.kind, resource.namespace)
            if resource.name not in self.resources[resource.kind][resource.namespace]:
                raise ValueError(
                    f"{resource.kind} {resource.namespace}/{resource.name} does not exist"
                )
            self.resources[resource.kind][resource.namespace][resource.name] = resource
            return resource

    def delete(self, kind: str, name: str, namespace: str = "default") -> bool:
        """Delete a resource"""
        with self.lock:
            ns_resources = self.resources.get(kind, {}).get(namespace, {})
            if name in ns_resources:
                del ns_resources[name]
                return True
            return False

    def create_or_update(self, resource: Resource) -> Resource:
        """Create or update a resource"""
        with self.lock:
            self._ensure_namespace(resource.kind, resource.namespace)
            self.resources[resource.kind][resource.namespace][resource.name] = resource
            return resource
