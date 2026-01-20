"""
API for interacting with containers.
"""
import random
from typing import Any, Optional
from concurrent.futures import Future

from store import ResourceStore
from controllers import PodController


class OrchestratorAPI:
    """API for interacting with containers"""

    def __init__(self, pod_controller: PodController, store: ResourceStore):
        self.pod_controller = pod_controller
        self.store = store

    def send_to_pod(
        self,
        pod_name: str,
        value: Any,
        namespace: str = "default",
        expect_response: bool = False,
    ) -> Optional[Future]:
        """Send a value to a pod's container queue"""
        container = self.pod_controller.get_container(pod_name, namespace)
        if not container:
            raise ValueError(f"Pod {namespace}/{pod_name} not found or not running")

        if expect_response:
            future = Future()
            container.input_queue.put((value, future))
            return future
        else:
            container.input_queue.put(value)
            return None

    def send_to_service(
        self,
        service_name: str,
        value: Any,
        namespace: str = "default",
        expect_response: bool = False,
        port: int = None,
    ) -> Optional[Future]:
        """Send a value to a random pod matched by the service"""
        service = self.store.get("Service", service_name, namespace)
        if not service:
            raise ValueError(f"Service {namespace}/{service_name} not found")

        containers = self.pod_controller.get_containers_by_labels(
            service.selector, namespace
        )

        if not containers:
            raise ValueError(f"No pods match service {service_name}")

        container = random.choice(containers)

        if expect_response:
            future = Future()
            container.input_queue.put((value, future))
            return future
        else:
            container.input_queue.put(value)
            return None

    def resolve_service(self, service_ref: str, namespace: str = "default") -> tuple:
        """Resolve a service reference like 'service-name:port' to (service, port)"""
        if ":" in service_ref:
            service_name, port = service_ref.rsplit(":", 1)
            port = int(port)
        else:
            service_name = service_ref
            port = None

        service = self.store.get("Service", service_name, namespace)
        return service, port
