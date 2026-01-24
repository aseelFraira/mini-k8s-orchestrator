"""
Controllers for the orchestrator.
"""

import subprocess
import threading
import time
import json
from typing import Dict, List, Optional, TYPE_CHECKING

from resources import PodResource, ServiceResource, ReplicaSetResource
from container import Container
from store import ResourceStore

if TYPE_CHECKING:
    from api import OrchestratorAPI


class Controller:
    """Base controller class"""

    def __init__(self, store: ResourceStore):
        self.store = store
        self.running = False
        self.thread = None

    def start(self):
        """Start the controller"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._reconcile_loop, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the controller"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _reconcile_loop(self):
        """Main reconciliation loop"""
        while self.running:
            try:
                self.reconcile()
            except Exception as e:
                print(f"Controller {self.__class__.__name__} error: {e}")
                import traceback

                traceback.print_exc()
            time.sleep(1)

    def reconcile(self):
        """Reconcile desired state with actual state"""
        raise NotImplementedError


class PodController(Controller):
    """Controller for Pod resources"""

    def __init__(self, store: ResourceStore, api_client: "OrchestratorAPI"):
        super().__init__(store)
        self.api_client = api_client

    def _container_key(self, namespace: str, name: str) -> str:
        return f"{namespace}_{name}"

    def get_actual_state(self) -> Dict[str, List[Dict]]:
        cmd = [
            "podman",
            "ps",
            "-a",
            "--filter",
            "label=managed-by=my-orchestrator",
            "--format",
            "json",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if not result.stdout.strip():
            return {}

        actual_data = json.loads(result.stdout)
        actual_state = {}

        for info in actual_data:
            labels = info.get("Labels", {})
            ns = labels.get("orchestrator-namespace")
            p_name = labels.get("orchestrator-pod-name")

            if ns and p_name:
                pod_key = self._container_key(ns, p_name)
                if pod_key not in actual_state:
                    actual_state[pod_key] = []

                actual_state[pod_key].append(
                    {
                        "full_name": info["Names"][0],
                        "is_running": info["State"] == "running",
                        "c_name": labels.get("orchestrator-container-name"),
                    }
                )

        return actual_state

    def reconcile(self):
        """Ensure host state matches store state"""
        # 1. Gather Desired State (Store)
        desired_pods = {
            self._container_key(p.namespace, p.name): p for p in self.store.list("Pod")
        }

        # 2. Gather Actual State (Podman)
        actual_state = self.get_actual_state()

        # 3. Stop containers that are not in desired state (Orphans)
        for pod_key, containers_info in actual_state.items():
            if pod_key not in desired_pods:
                print(f"Cleaning up orphaned pod: {pod_key}")
                for c in containers_info:
                    Container(full_name=c["full_name"]).stop()

        # 4. SYNC & REPAIR
        for key, pod in desired_pods.items():
            host_containers = actual_state.get(key, [])
            host_map = {c["c_name"]: c for c in host_containers}

            for spec in pod.containers:
                c_name = spec.get("name")
                c_info = host_map.get(c_name)

                if not c_info:
                    print(f"Starting missing container {c_name} for pod {key}")
                    Container(pod=pod, container_name=c_name).start()
                elif not c_info["is_running"]:
                    Container(full_name=c_info["full_name"]).stop()
                    Container(pod=pod, container_name=c_name).start()

        # 5. Hash check for spec changes
        for key, pod in desired_pods.items():
            if not pod.containers:
                continue
            c_name = pod.containers[0].get("name")
            host_containers = actual_state.get(key, [])
            host_map = {c["c_name"]: c for c in host_containers}
            c_info = host_map.get(c_name)

            if c_info and c_info["is_running"]:
                shadow = Container(full_name=c_info["full_name"])
                actual_hash = shadow.get_label("pod-hash-number")
                if actual_hash != pod.spec_hash:
                    print(f"Pod spec changed for {key}, recreating...")
                    for c in host_containers:
                        Container(full_name=c["full_name"]).stop()

    def get_container(
        self, name: str, namespace: str = "default"
    ) -> Optional[Container]:
        """Get a running container by pod name"""
        pod = self.store.get("Pod", name, namespace)
        if not pod or not pod.containers:
            return None

        c_name = pod.containers[0]["name"]
        container = Container(pod=pod, container_name=c_name)
        if container.is_alive():
            return container
        return None

    def list_containers(self, namespace: str = None) -> List[Container]:
        """List all running containers"""
        result = []
        pods = self.store.list("Pod", namespace)
        for pod in pods:
            for spec in pod.containers:
                c = Container(pod=pod, container_name=spec["name"])
                if c.is_alive():
                    result.append(c)
        return result

    def get_containers_by_labels(
        self, selector: Dict[str, str], namespace: str = "default"
    ) -> List[Container]:
        """Get running containers matching label selector"""
        matching_pods = []
        for pod in self.store.list("Pod", namespace):
            matches = all(pod.labels.get(k) == v for k, v in selector.items())
            if matches:
                matching_pods.append(pod)

        containers = []
        for pod in matching_pods:
            for spec in pod.containers:
                c = Container(pod=pod, container_name=spec["name"])
                if c.is_alive():
                    containers.append(c)
        return containers


class ServiceController(Controller):  # !! Note: check how does
    """
    Controller for Service resources.
    Implements the 'Service Proxy' pattern using Nginx.
    """

    def __init__(self, store: ResourceStore, pod_controller: PodController):
        super().__init__(store)
        self.pod_controller = pod_controller

    def reconcile(self):
        """Reconcile services - create proxy pods and update configs"""
        services = self.store.list("Service")
        existing_service_names = {svc.name for svc in services}

        # Delete proxy pods whose owner service no longer exists
        all_pods = self.store.list("Pod")
        for pod in all_pods:
            owner_service_name = pod.labels.get("service-name")
            if owner_service_name and owner_service_name not in existing_service_names:
                print(
                    f"Cleaning up orphaned proxy pod: {pod.name} (owner Service '{owner_service_name}' deleted)"
                )
                self.store.delete("Pod", pod.name, pod.namespace)

        # Reconcile each service
        for service in services:
            try:
                self._reconcile_service(service)
            except Exception as e:
                print(f"Error reconciling service {service.name}: {e}")

    def _reconcile_service(self, service: ServiceResource):
        proxy_pod_name = f"proxy-{service.name}"

        # 1. Ensure Shadow Pod Exists in Store
        existing_pod = self.store.get("Pod", proxy_pod_name, service.namespace)
        if not existing_pod:
            print(f"Creating proxy pod for {service.name}")
            pod = PodResource(
                name=proxy_pod_name,
                namespace=service.namespace,
                spec=service.proxy_pod_spec,
                metadata={
                    "labels": {"role": "service-proxy", "service-name": service.name},
                },
            )
            self.store.create(pod)
            return

        # 2. Get the running proxy container
        proxy_container = self.pod_controller.get_container(
            proxy_pod_name, service.namespace
        )
        if not proxy_container:
            return

        # 3. Find backend pods
        backend_containers = self.pod_controller.get_containers_by_labels(
            service.selector, service.namespace
        )

        pod_ips = []
        for container in backend_containers:
            ip = container.get_ip_address()
            if ip:
                pod_ips.append(ip)

        # 4. Generate and inject Nginx Config
        nginx_conf = self._generate_nginx_config(service, pod_ips)
        self._inject_config(proxy_container, nginx_conf)
        proxy_container.exec_command(["nginx", "-s", "reload"])

    def _generate_nginx_config(
        self, service: ServiceResource, pod_ips: List[str]
    ) -> str:
        """Generates the Nginx configuration"""
        if not service.ports:
            return ""

        port_mapping = service.ports[0]
        listen_port = port_mapping.get("port")
        target_port = port_mapping.get("targetPort")

        if not listen_port or not target_port:
            print(f"Service {service.name} missing port or targetPort")
            return ""

        # Build upstream servers
        if pod_ips:
            upstream_servers = "\n".join(
                f"    server {ip}:{target_port};" for ip in pod_ips
            )
        else:
            upstream_servers = "    server 127.0.0.1:65535 down;"

        # Upstream block MUST come before server block
        config = f"""upstream {service.name}_backend {{
{upstream_servers}
}}

server {{
    listen {listen_port};

    location / {{
        proxy_pass http://{service.name}_backend;
    }}
}}
"""
        return config

    def _inject_config(self, container: Container, config_content: str):
        """Injects config into the container"""
        cmd = [
            "podman",
            "exec",
            "-i",
            container.full_name,
            "tee",
            "/etc/nginx/conf.d/default.conf",
        ]
        subprocess.run(cmd, input=config_content, text=True, capture_output=True)


# NOTE: The ReplicaSetController operates at a different level than the PodController.
# - PodController: Reconciles the STORE (desired) vs HOST (actual containers on Podman).
# - ReplicaSetController: Reconciles the REPLICASET SPEC (desired replicas) vs STORE (current pods).
#
# This creates a cascading effect:
#   1. ReplicaSetController ensures N pod resources exist in the store.
#   2. PodController reacts to store changes and manages actual containers.


class ReplicaSetController(Controller):
    """Controller for ReplicaSet resources."""

    def __init__(self, store: ResourceStore):
        super().__init__(store)

    def reconcile(self):
        """Reconcile ReplicaSets - ensure desired number of pods in store"""
        replicasets = self.store.list("ReplicaSet")
        existing_rs_names = {rs.name for rs in replicasets}

        # Delete pods whose owner ReplicaSet no longer exists
        all_pods = self.store.list("Pod")
        for pod in all_pods:
            owner_rs_name = pod.metadata.get("owner-replicaset")
            if owner_rs_name and owner_rs_name not in existing_rs_names:
                print(
                    f"Cleaning up orphaned pod: {pod.name} (owner RS '{owner_rs_name}' deleted)"
                )
                self.store.delete("Pod", pod.name, pod.namespace)

        # Reconcile each ReplicaSet
        for rs in replicasets:
            try:
                self._reconcile_replicaset(rs)
            except Exception as e:
                print(f"Error reconciling ReplicaSet {rs.name}: {e}")

    def _matches_selector(self, pod: PodResource, selector: Dict[str, str]) -> bool:
        """Check if a pod's labels match the selector"""
        pod_labels = pod.labels
        return all(pod_labels.get(k) == v for k, v in selector.items())

    def _reconcile_replicaset(self, rs: ReplicaSetResource):
        """Ensure the store has the correct number of pods for this ReplicaSet"""
        # 1. Find existing pods in STORE that match this ReplicaSet's selector
        all_pods = self.store.list("Pod", rs.namespace)
        owned_pods = [p for p in all_pods if self._matches_selector(p, rs.selector)]

        current_count = len(owned_pods)
        desired_count = rs.replicas

        # Case 1: current pod number < desired count (create missing pods)

        if current_count < desired_count:
            # Find the next available index
            existing_indices = set()
            for pod in owned_pods:
                # Extract index from name like "my-rs-0"
                if pod.name.startswith(f"{rs.name}-"):
                    try:
                        idx = int(pod.name.split("-")[-1])
                        existing_indices.add(idx)
                    except ValueError:
                        pass

            for i in range(desired_count):
                if i not in existing_indices:
                    # Use ReplicaSet name as pod name base
                    pod_name = f"{rs.name}-{i}"
                    print(f"Creating pod {pod_name} for ReplicaSet {rs.name}")

                    # Get template spec and labels
                    template_spec = rs.template.get("spec", {})
                    template_labels = rs.template.get("metadata", {}).get(
                        "labels", rs.selector
                    )

                    pod = PodResource(
                        name=pod_name,
                        namespace=rs.namespace,
                        spec=template_spec,
                        metadata={
                            "labels": template_labels,
                            "owner-replicaset": rs.name,
                        },
                    )
                    self.store.create(pod)
                    current_count += 1

                    if current_count >= desired_count:
                        break

        # 3. Delete excess pods from store
        elif current_count > desired_count:
            excess = current_count - desired_count
            # Filter to only pods owned by THIS ReplicaSet (using metadata, not name parsing)
            rs_owned_pods = [
                p for p in owned_pods if p.metadata.get("owner-replicaset") == rs.name
            ]
            # Delete the excess (just pick any, order doesn't matter for correctness)
            pods_to_delete = rs_owned_pods[:excess]

            for pod in pods_to_delete:
                print(f"Deleting pod {pod.name} for ReplicaSet {rs.name}")
                self.store.delete("Pod", pod.name, rs.namespace)
