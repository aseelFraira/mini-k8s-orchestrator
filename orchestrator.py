"""
Kubernetes-like orchestration system for Python threads.

This implements:
- Resource types: Pod, Service (strict subset of Kubernetes API)
- Controllers for each resource type
- In-memory resource tree
- Inter-container communication via queues
- FastAPI REST API compatible with kubectl-style requests
"""
import subprocess
import threading
import queue
import yaml
import random
import importlib
import time
import re
import uvicorn
import hashlib
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from concurrent.futures import Future
from enum import Enum
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel


# =============================================================================
# Resource Definitions
# =============================================================================


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
            # Handle both dict format and list format
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
        return self.metadata
    
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
        return {
            "containers": [{
                "name": "nginx",  
                "image": "nginx:alpine",
                "env": {},
                "network_aliases": [self.name] # The magical DNS name
            }]
        }


# =============================================================================
# Container Runtime
# =============================================================================

class Container:
    def __init__(self, pod=None, container_name=None, full_name=None, network_aliases=None):
        # Standard Case: Creating or Managing a Pod from the Store
        if pod and container_name:
            container_specs = pod.get_container_spec(container_name)
            
            if not container_specs:
                raise ValueError(f"Container {container_name} not found in pod {pod.name}")

            self.container_name = container_name
            self.namespace = pod.namespace
            self.pod_name = pod.name
            
            # The Podman ID
            self.full_name = f"{self.namespace}_{self.pod_name}_{container_name}"
            
            # The Spec
            self.image = container_specs.get("image")
            self.env = container_specs.get("env", {})    
            self.labels = pod.metadata.get("labels", {})
            self.hash_number = pod.spec_hash
            self.network_aliases = container_specs.get("network_aliases", [])

        # 2. Cleanup Case: Creating a "Shadow" object from Podman data
        elif full_name:
            self.full_name = full_name
            self.pod = None
            self.container_name = None
            self.image = None
            self.env = {}
            self.labels = {}
            self.network_aliases = []

        # 3. Special Case: Service Proxy (managed directly)
        elif container_name and full_name:
             self.container_name = container_name
             self.full_name = full_name
             self.network_aliases = network_aliases or []
             self.image = None # Will be set by caller usually, but here we might need flexibility
             self.env = {}
             self.labels = {}
             self.pod_name = "service-proxy" # Dummy
             self.namespace = "default"
             self.hash_number = "0"

        else:
            raise ValueError("Must provide either (pod and container_name) or (full_name)")
            
        self.running = False
        
    
    def start(self):
        """Starts the container with full metadata and environment."""
        # Idempotency - Cleanup any existing container with this name first
        self.stop()

        cmd = ["podman", "run", "-d", "--name", self.full_name]
        
        # Network aliases
        for alias in self.network_aliases:
            cmd.extend(["--network-alias", alias])

        #  Orchestrator Labels - for mangment
        cmd.extend(["--label", "managed-by=my-orchestrator"])
        cmd.extend(["--label", f"orchestrator-namespace={self.namespace}"])
        cmd.extend(["--label", f"orchestrator-pod-name={self.pod_name}"])
        cmd.extend(["--label", f"orchestrator-container-name={self.container_name}"])
        cmd.extend(["--label", f"pod-hash-number={self.hash_number}"])

        # Add Environment Variables
        for key, value in self.env.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Add User Labels
        for key, value in self.labels.items():
            cmd.extend(["--label", f"{key}={value}"])

        # Image and Execution
        if not self.image:
             raise ValueError("Cannot start container without image")
        cmd.append(self.image)
        
        print(f"Executing: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        

    def stop(self):
            cmd = ["podman", "rm", "-f", self.full_name]
            try:
                # We use check=False here because if the container is 
                # already gone, we don't want our whole orchestrator to crash.
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                
                if result.returncode == 0:
                    print(f"Successfully removed container: {self.full_name}")
                else:
                    # This might happen if the container didn't exist, which is often fine
                    pass
                    # print(f"Note: Could not remove {self.full_name}: {result.stderr.strip()}")
                    
            except Exception as e:
                print(f"Critical error during stop: {e}")
            
            
    def is_alive(self):
        """Check the ground truth via podman inspect"""
        cmd = ["podman", "inspect", "-f", "{{.State.Running}}", self.full_name]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        # If the command succeeded and returned 'true', the container is healthy
        return result.returncode == 0 and result.stdout.strip() == "true"
    
    def get_label(self, label_name: str):
        # This command asks: "For THIS container, what is the value of THIS label?"
        cmd = [
            "podman", "inspect", 
            "-f", f'{{{{index .Config.Labels "{label_name}"}}}}', 
            self.full_name
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def get_ip_address(self) -> Optional[str]:
        """Get the IP address of the container on the orchestrator network"""
        # Assuming the network is named 'podman' or similar, but typically inspect .NetworkSettings.IPAddress 
        # works for the default network, or .NetworkSettings.Networks.<name>.IPAddress
        # Let's try the generic one first
        cmd = ["podman", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", self.full_name]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        return None

    def exec_command(self, command: List[str]) -> bool:
        """Execute a command inside the container"""
        cmd = ["podman", "exec", self.full_name] + command
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Exec failed: {result.stderr}")
            return False
        return True


# =============================================================================
# Resource Store
# =============================================================================


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
                # Return all resources across all namespaces
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


# =============================================================================
# Controllers
# =============================================================================


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

# The controller ONLY uses two things:
# 1. Source of the truth - STORE
# 2. Current state - Podman
class PodController(Controller):
    """Controller for Pod resources"""

    def __init__(self, store: ResourceStore, api_client: "OrchestratorAPI"):
        super().__init__(store)
        self.api_client = api_client

        self.lock = threading.RLock()

    def _container_key(self, namespace: str, name: str) -> str:
        # Use underscores to match your Podman label formatting
        return f"{namespace}_{name}"

    def get_actual_state(self) -> Dict[str, List[Dict]]:
        cmd = ["podman", "ps", "-a", "--filter",
               "label=managed-by=my-orchestrator",
               "--format", "json"]
        
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
                
                # Store enough info to decide if we need to REPAIR
                actual_state[pod_key].append({
                    "full_name": info["Names"][0],
                    "is_running": info["State"] == "running",
                    "c_name": labels.get("orchestrator-container-name")
                })
                
        return actual_state

    def reconcile(self):
        """Ensure host state matches store state"""
        with self.lock:
            # 1. Gather Desired State (Store)
            desired_pods = {self._container_key(p.namespace, p.name): p 
                           for p in self.store.list("Pod")}
            
            # 2. Gather Actual State (Podman)
            actual_state = self.get_actual_state() 
            
            # 3. Stop containers that are not in desired state! (Orphans)
            # NOTE: We can add here container granularity, instead of Pod
            for pod_key, containers_info in actual_state.items():
                if pod_key not in desired_pods:
                    print(f"Cleaning up orphaned pod: {pod_key}")
                    for c in containers_info:
                        Container(full_name=c["full_name"]).stop()

            # 4. PHASE: SYNC(containers that are in the desired state and missing) & REPAIR(Not running containers) (Desired Pods)
            for key, pod in desired_pods.items():
                # Get what's actually on the host for this pod
                host_containers = actual_state.get(key, [])
                # lookup map: { "container_name": info_dict }
                host_map = {c["c_name"]: c for c in host_containers}

                for spec in pod.containers: # !! Can be changed to first container!
                    c_name = spec.get("name")
                    c_info = host_map.get(c_name)

                    # CASE A: Container is completely missing from Podman 
                    if not c_info:
                        print(f"Starting missing container {c_name} for pod {key}")
                        Container(pod=pod, container_name=c_name).start()
                    
                    # CASE B: Container exists but is NOT running (Exited)
                    elif not c_info["is_running"]:
                        # Force stop/cleanup then start fresh
                        Container(full_name=c_info["full_name"]).stop()
                        Container(pod=pod, container_name=c_name).start()
                        
            #5. PHASE: pod spec changed
            for key, pod in desired_pods.items():
                shadow = Container(pod,pod.first_container)
                actual_hash = shadow.get_label("pod-hash-number")
                if actual_hash != pod.spec_hash:
                    shadow.stop()
                    shadow.run()
            #   pod.status = {"phase": "Running"}  do we need this rly?


    def get_container(
        self, name: str, namespace: str = "default"
    ) -> Optional[Container]:
        """Get a running container by pod name"""
        # 1. Find the Pod in the Store
        pod = self.store.get("Pod", name, namespace)
        if not pod:
            return None
        
        # 2. Assume first container (or we could look for a specific one)
        if not pod.containers:
            return None
        c_name = pod.containers[0]["name"]
        
        # 3. Construct the Container object and check if it's alive
        container = Container(pod=pod, container_name=c_name)
        if container.is_alive():
            return container
        return None

    def list_containers(self, namespace: str = None) -> List[Container]:
        """List all running containers, optionally filtered by namespace"""
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
        # 1. Find matching Pods in Store
        matching_pods = []
        for pod in self.store.list("Pod", namespace):
            # Check if all selector keys match the pod's labels
            matches = all(pod.labels.get(k) == v for k, v in selector.items())
            if matches:
                matching_pods.append(pod)
        
        # 2. Get their running containers
        containers = []
        for pod in matching_pods:
            for spec in pod.containers:
                c = Container(pod=pod, container_name=spec["name"])
                if c.is_alive():
                    containers.append(c)
        return containers


class ServiceController(Controller):
    """
    Controller for Service resources.
    Implements the 'Service Proxy' pattern using Nginx.
    """

    def __init__(self, store: ResourceStore, pod_controller: PodController):
        super().__init__(store)
        self.pod_controller = pod_controller

    def reconcile(self):
        """
        Reconciliation Loop:
        1. List all Services
        2. For each Service:
           a. Find backend Pods (and their IPs)
           b. Generate nginx.conf
           c. Check if Proxy exists
           d. If not, start it
           e. Update config in container and reload
        """
        services = self.store.list("Service")
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
            # Create the Pod Resource - PodController will pick it up
            pod = PodResource(
                name=proxy_pod_name,
                namespace=service.namespace,
                spec=service.proxy_pod_spec,
                metadata={
                    "labels": {"role": "service-proxy", "service-name": service.name}
                }
            )
            self.store.create(pod)
            return  # Wait for next loop for PodController to start it

        # 2. Get the running proxy container from PodController
        # PodController manages it, we just need a reference to inject config
        proxy_container = self.pod_controller.get_container(proxy_pod_name, service.namespace)
        
        if not proxy_container:
            # PodController hasn't started it yet, wait
            return

        # 3. Find backend pods (the ones with matching labels)
        backend_containers = self.pod_controller.get_containers_by_labels(
            service.selector, service.namespace
        )
        
        # Collect IPs
        upstreams = []
        for container in backend_containers:
            ip = container.get_ip_address()
            if ip:
                upstreams.append(ip)

        # 4. Generate Nginx Config
        nginx_conf = self._generate_nginx_config(service, upstreams)
        
        # 5. Inject Config & Reload
        self._inject_config(proxy_container, nginx_conf)
        proxy_container.exec_command(["nginx", "-s", "reload"])


    def _generate_nginx_config(self, service: ServiceResource, upstreams: List[str]) -> str:
        """Generates the Nginx configuration"""
        
        # Map service ports. For now assuming single port or taking the first one.
        # User said "Service Port (e.g. 5000) -> Target Port (e.g. 8080)"
        if not service.ports:
            return "" # Or default
            
        port_mapping = service.ports[0] # List[Dict]
        listen_port = port_mapping.get("port", 80)
        target_port = port_mapping.get("targetPort", 80)

        # Upstream block
        upstream_block = ""
        if upstreams:
            upstream_block = f"upstream {service.name}_backend {{\n"
            for ip in upstreams:
                upstream_block += f"        server {ip}:{target_port};\n"
            upstream_block += "    }"
        else:
            # Fallback if no upstreams? Nginx will fail to start if upstream is empty.
            # We can put a dummy server or localhost down
            # Or simplified: return 503
             upstream_block = f"upstream {service.name}_backend {{\n server 127.0.0.1:65535 down; \n}}"

        config = f"""
server {{
    listen {listen_port};
    
    location / {{
        proxy_pass http://{service.name}_backend;
    }}
}}
{upstream_block}
"""
        return config

    def _inject_config(self, container: Container, config_content: str):
        """Injects config into the container"""
        # We can pipe via subprocess
        cmd = ["podman", "exec", "-i", container.full_name, "tee", "/etc/nginx/conf.d/default.conf"]
        subprocess.run(cmd, input=config_content, text=True, capture_output=True)


# =============================================================================
# API
# =============================================================================


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

        # Find pods matching the service selector
        containers = self.pod_controller.get_containers_by_labels(
            service.selector, namespace
        )

        if not containers:
            raise ValueError(f"No pods match service {service_name}")

        # Random load balancing
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


# =============================================================================
# Cluster
# =============================================================================


class OrchestratorCluster:
    """Main cluster orchestrator"""

    def __init__(self):
        self.store = ResourceStore()
        self.pod_controller = None
        self.service_controller = None
        self.api = None
        self._started = False

    def start(self):
        """Start the cluster"""
        if self._started:
            return

        # Initialize API
        self.pod_controller = PodController(self.store, None)
        self.api = OrchestratorAPI(self.pod_controller, self.store)
        self.pod_controller.api_client = self.api

        # Initialize controllers
        self.service_controller = ServiceController(self.store, self.pod_controller)

        # Start controllers
        self.pod_controller.start()
        self.service_controller.start()

        self._started = True
        print("Orchestrator cluster started")

    def stop(self):
        """Stop the cluster"""
        if self.pod_controller:
            self.pod_controller.stop()
        if self.service_controller:
            self.service_controller.stop()
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

        # Extract labels from metadata
        clean_metadata = {
            k: v for k, v in metadata.items() if k not in ("name", "namespace")
        }

        if not kind or not name:
            raise ValueError(f"Invalid resource: missing kind or name: {resource_dict}")

        # Create resource object
        if kind == "Pod":
            resource = PodResource(name, spec, clean_metadata, ns)
        elif kind == "Service":
            resource = ServiceResource(name, spec, clean_metadata, ns)
        else:
            raise ValueError(f"Unknown resource kind: {kind}")

        # Create or update
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


# =============================================================================
# FastAPI REST API
# =============================================================================

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


# Helper to convert resource to response dict
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
    
    if await cluster.store.get("Pod",namespace,resource.metadata):
        return JSONResponse(status_code=409, content={"error": "Conflict: Pod exists"})
    
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
    existing = await cluster.store.get("Pod",namespace,resource.metadata)
    if not existing:
        return JSONResponse(
            status_code=404, 
            content={"error": f"Pod {name} not found in namespace {namespace}"}
        )
        
    success = await cluster.store.delete("Pod", namespace, name)
    if success:
        return {
            "status": "Success", 
            "message": f"Pod {namespace}/{name} has been removed from the orchestrator."
        }
    
    raise HTTPException(status_code=500, detail="Failed to update the ResourceStore.")




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

    updated = cluster.apply_resource(resource_dict, namespace)
    return resource_response(updated)


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


# =============================================================================
# Messaging Endpoints (Extension to Kubernetes API)
# =============================================================================


@app.post("/api/v1/namespaces/{namespace}/pods/{name}/send")
async def send_to_pod(namespace: str, name: str, message: Dict[str, Any]):
    """Send a message to a Pod"""
    try:
        cluster.api.send_to_pod(name, message.get("data"), namespace)
        return {"status": "Success", "message": "Message sent"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/namespaces/{namespace}/pods/{name}/call")
async def call_pod(namespace: str, name: str, message: Dict[str, Any]):
    """Send a message to a Pod and wait for response"""
    try:
        future = cluster.api.send_to_pod(
            name, message.get("data"), namespace, expect_response=True
        )
        result = future.result(timeout=message.get("timeout", 30))
        return {"status": "Success", "result": result}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except TimeoutError:
        raise HTTPException(408, "Request timeout")


@app.post("/api/v1/namespaces/{namespace}/services/{name}/send")
async def send_to_service(namespace: str, name: str, message: Dict[str, Any]):
    """Send a message to a Service (load balanced to a pod)"""
    try:
        cluster.api.send_to_service(name, message.get("data"), namespace)
        return {"status": "Success", "message": "Message sent"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/v1/namespaces/{namespace}/services/{name}/call")
async def call_service(namespace: str, name: str, message: Dict[str, Any]):
    """Send a message to a Service and wait for response"""
    try:
        future = cluster.api.send_to_service(
            name, message.get("data"), namespace, expect_response=True
        )
        result = future.result(timeout=message.get("timeout", 30))
        return {"status": "Success", "result": result}
    except ValueError as e:
        raise HTTPException(404, str(e))
    except TimeoutError:
        raise HTTPException(408, "Request timeout")


# =============================================================================
# Health and Info Endpoints
# =============================================================================


@app.get("/healthz")
async def healthz():
    """Health check endpoint"""
    return {"status": "ok"}


@app.get("/api/v1/namespaces")
async def list_namespaces():
    """List all namespaces (returns default only for this implementation)"""
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
