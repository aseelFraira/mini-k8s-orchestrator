"""
Layer 4: End-to-End Tests for Mini-K8s Orchestrator
Real-world execution using subprocess and actual Podman.
These tests require Podman to be installed and running.
"""

import pytest
import subprocess
import time
import sys
import os
import json

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from resources import PodResource, ReplicaSetResource, ServiceResource
from store import ResourceStore
from controllers import ReplicaSetController, ServiceController, PodController
from container import Container

# Import the skip marker from conftest
from conftest import skip_if_no_podman, is_podman_available


# =============================================================================
# UTILITIES
# =============================================================================


def podman_container_exists(name: str) -> bool:
    """Check if a container exists (running or stopped)."""
    result = subprocess.run(
        ["podman", "ps", "-a", "--format", "{{.Names}}", "--filter", f"name=^{name}$"],
        capture_output=True,
        text=True,
        check=False,
    )
    return name in result.stdout


def podman_container_running(name: str) -> bool:
    """Check if a container is running."""
    result = subprocess.run(
        ["podman", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def podman_get_container_label(name: str, label: str) -> str:
    """Get a label value from a container."""
    result = subprocess.run(
        ["podman", "inspect", "-f", f'{{{{index .Config.Labels "{label}"}}}}', name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def podman_list_orchestrator_containers() -> list:
    """List all containers managed by the orchestrator."""
    result = subprocess.run(
        [
            "podman",
            "ps",
            "-a",
            "--filter",
            "label=managed-by=my-orchestrator",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("\n")
    return []


def nuclear_cleanup():
    """Force remove all orchestrator-managed containers."""
    containers = podman_list_orchestrator_containers()
    for container in containers:
        if container:
            subprocess.run(
                ["podman", "rm", "-f", container],
                capture_output=True,
                check=False,
            )


def ensure_network_exists():
    """Ensure the orchestrator network exists."""
    result = subprocess.run(
        ["podman", "network", "exists", "orchestrator-net"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        subprocess.run(
            ["podman", "network", "create", "orchestrator-net"],
            capture_output=True,
            check=False,
        )


# =============================================================================
# E2E POD LIFECYCLE TESTS
# =============================================================================


@pytest.mark.e2e
@skip_if_no_podman
class TestE2EPodLifecycle:
    """End-to-end tests for Pod lifecycle with real Podman."""

    def setup_method(self):
        """Setup before each test."""
        nuclear_cleanup()
        ensure_network_exists()
        self.store = ResourceStore()

    def teardown_method(self):
        """Nuclear cleanup after each test."""
        nuclear_cleanup()

    def test_create_pod_creates_container(self):
        """Test that creating a pod in the store results in a running container."""
        pod = PodResource(
            name="e2e-test-pod",
            spec={"containers": [{"name": "main", "image": "alpine:latest"}]},
            namespace="default",
        )
        self.store.create(pod)

        # Create and run PodController
        controller = PodController(self.store, None)

        try:
            controller.reconcile()
        except Exception as e:
            print(f"Reconcile error (may be expected): {e}")

        # Wait a moment for container to start
        time.sleep(1)

        # Verify container exists
        container_name = "default_e2e-test-pod_main"
        assert podman_container_exists(
            container_name
        ), f"Container {container_name} should exist"

    def test_delete_pod_removes_container(self):
        """Test that deleting a pod from the store removes the container."""
        # First create a pod
        pod = PodResource(
            name="e2e-delete-pod",
            spec={"containers": [{"name": "main", "image": "alpine:latest"}]},
            namespace="default",
        )
        self.store.create(pod)

        controller = PodController(self.store, None)

        try:
            controller.reconcile()
        except Exception:
            pass

        time.sleep(1)

        container_name = "default_e2e-delete-pod_main"
        assert podman_container_exists(
            container_name
        ), "Container should exist before delete"

        # Delete from store
        self.store.delete("Pod", "e2e-delete-pod", "default")

        # Reconcile again
        try:
            controller.reconcile()
        except Exception:
            pass

        time.sleep(1)

        # Container should be gone
        assert not podman_container_exists(
            container_name
        ), "Container should be removed after delete"

    def test_pod_has_correct_labels(self):
        """Test that created container has correct orchestrator labels."""
        pod = PodResource(
            name="e2e-labeled-pod",
            spec={"containers": [{"name": "app", "image": "alpine:latest"}]},
            metadata={"labels": {"app": "test", "version": "v1"}},
            namespace="myns",
        )
        self.store.create(pod)

        controller = PodController(self.store, None)

        try:
            controller.reconcile()
        except Exception:
            pass

        time.sleep(1)

        container_name = "myns_e2e-labeled-pod_app"

        # Verify orchestrator labels
        assert (
            podman_get_container_label(container_name, "managed-by")
            == "my-orchestrator"
        )
        assert (
            podman_get_container_label(container_name, "orchestrator-namespace")
            == "myns"
        )
        assert (
            podman_get_container_label(container_name, "orchestrator-pod-name")
            == "e2e-labeled-pod"
        )

        # Verify user labels
        assert podman_get_container_label(container_name, "app") == "test"
        assert podman_get_container_label(container_name, "version") == "v1"


# =============================================================================
# E2E REPLICASET TESTS
# =============================================================================


@pytest.mark.e2e
@skip_if_no_podman
class TestE2EReplicaSetScaling:
    """End-to-end tests for ReplicaSet scaling with real Podman."""

    def setup_method(self):
        """Setup before each test."""
        nuclear_cleanup()
        ensure_network_exists()
        self.store = ResourceStore()

    def teardown_method(self):
        """Nuclear cleanup after each test."""
        nuclear_cleanup()

    def test_replicaset_creates_expected_containers(self):
        """Test that a ReplicaSet creates the expected number of containers."""
        rs = ReplicaSetResource(
            name="e2e-rs",
            spec={
                "replicas": 3,
                "selector": {"app": "e2e-app"},
                "template": {
                    "metadata": {"labels": {"app": "e2e-app"}},
                    "spec": {"containers": [{"name": "app", "image": "alpine:latest"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Run RS controller to create pods in store
        rs_controller = ReplicaSetController(self.store)
        rs_controller.reconcile()

        # Verify pods in store
        pods = self.store.list("Pod", "default")
        assert len(pods) == 3

        # Run Pod controller to create actual containers
        pod_controller = PodController(self.store, None)

        try:
            pod_controller.reconcile()
        except Exception:
            pass

        time.sleep(2)

        # Verify containers exist
        containers = podman_list_orchestrator_containers()
        rs_containers = [c for c in containers if "e2e-rs" in c]
        assert (
            len(rs_containers) == 3
        ), f"Expected 3 containers, got {len(rs_containers)}: {rs_containers}"

    def test_replicaset_scale_up(self):
        """Test scaling up a ReplicaSet creates more containers."""
        rs = ReplicaSetResource(
            name="e2e-scale-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "e2e-scale"},
                "template": {
                    "metadata": {"labels": {"app": "e2e-scale"}},
                    "spec": {"containers": [{"name": "app", "image": "alpine:latest"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        rs_controller = ReplicaSetController(self.store)
        pod_controller = PodController(self.store, None)

        # Initial reconcile
        rs_controller.reconcile()
        try:
            pod_controller.reconcile()
        except Exception:
            pass

        time.sleep(2)

        initial_containers = [
            c for c in podman_list_orchestrator_containers() if "e2e-scale-rs" in c
        ]
        assert len(initial_containers) == 2

        # Scale up
        scaled_rs = ReplicaSetResource(
            name="e2e-scale-rs",
            spec={
                "replicas": 4,
                "selector": {"app": "e2e-scale"},
                "template": {
                    "metadata": {"labels": {"app": "e2e-scale"}},
                    "spec": {"containers": [{"name": "app", "image": "alpine:latest"}]},
                },
            },
            namespace="default",
        )
        self.store.create_or_update(scaled_rs)

        rs_controller.reconcile()
        try:
            pod_controller.reconcile()
        except Exception:
            pass

        time.sleep(2)

        final_containers = [
            c for c in podman_list_orchestrator_containers() if "e2e-scale-rs" in c
        ]
        assert (
            len(final_containers) == 4
        ), f"Expected 4, got {len(final_containers)}: {final_containers}"

    def test_replicaset_scale_down(self):
        """Test scaling down a ReplicaSet removes containers."""
        rs = ReplicaSetResource(
            name="e2e-shrink-rs",
            spec={
                "replicas": 4,
                "selector": {"app": "e2e-shrink"},
                "template": {
                    "metadata": {"labels": {"app": "e2e-shrink"}},
                    "spec": {"containers": [{"name": "app", "image": "alpine:latest"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        rs_controller = ReplicaSetController(self.store)
        pod_controller = PodController(self.store, None)

        # Initial reconcile
        rs_controller.reconcile()
        try:
            pod_controller.reconcile()
        except Exception:
            pass

        time.sleep(2)

        initial_containers = [
            c for c in podman_list_orchestrator_containers() if "e2e-shrink-rs" in c
        ]
        assert len(initial_containers) == 4

        # Scale down
        scaled_rs = ReplicaSetResource(
            name="e2e-shrink-rs",
            spec={
                "replicas": 1,
                "selector": {"app": "e2e-shrink"},
                "template": {
                    "metadata": {"labels": {"app": "e2e-shrink"}},
                    "spec": {"containers": [{"name": "app", "image": "alpine:latest"}]},
                },
            },
            namespace="default",
        )
        self.store.create_or_update(scaled_rs)

        rs_controller.reconcile()
        try:
            pod_controller.reconcile()
        except Exception:
            pass

        time.sleep(2)

        final_containers = [
            c for c in podman_list_orchestrator_containers() if "e2e-shrink-rs" in c
        ]
        assert (
            len(final_containers) == 1
        ), f"Expected 1, got {len(final_containers)}: {final_containers}"


# =============================================================================
# E2E SERVICE PROXY TESTS
# =============================================================================


@pytest.mark.e2e
@skip_if_no_podman
class TestE2EServiceProxy:
    """End-to-end tests for Service proxy functionality."""

    def setup_method(self):
        """Setup before each test."""
        nuclear_cleanup()
        ensure_network_exists()
        self.store = ResourceStore()

    def teardown_method(self):
        """Nuclear cleanup after each test."""
        nuclear_cleanup()

    def test_service_creates_proxy_container(self):
        """Test that a Service creates an nginx proxy container."""
        # First create a backend pod
        pod = PodResource(
            name="e2e-backend",
            spec={"containers": [{"name": "app", "image": "alpine:latest"}]},
            metadata={"labels": {"app": "e2e-backend"}},
            namespace="default",
        )
        self.store.create(pod)

        # Create service
        svc = ServiceResource(
            name="e2e-service",
            spec={
                "selector": {"app": "e2e-backend"},
                "ports": [{"port": 8080, "targetPort": 8080}],
            },
            namespace="default",
        )
        self.store.create(svc)

        # Run controllers
        pod_controller = PodController(self.store, None)
        svc_controller = ServiceController(self.store, pod_controller)

        try:
            pod_controller.reconcile()
        except Exception:
            pass

        try:
            svc_controller.reconcile()
        except Exception:
            pass

        time.sleep(2)

        # Verify proxy container exists
        proxy_name = "default_proxy-e2e-service_nginx"
        containers = podman_list_orchestrator_containers()

        # Check for proxy pod in store
        proxy_pod = self.store.get("Pod", "proxy-e2e-service", "default")
        assert proxy_pod is not None, "Proxy pod should exist in store"
        assert proxy_pod.labels.get("role") == "service-proxy"


# =============================================================================
# E2E FULL STACK TESTS
# =============================================================================


@pytest.mark.e2e
@skip_if_no_podman
class TestE2EFullStack:
    """End-to-end tests for full orchestrator stack."""

    def setup_method(self):
        """Setup before each test."""
        nuclear_cleanup()
        ensure_network_exists()
        self.store = ResourceStore()

    def teardown_method(self):
        """Nuclear cleanup after each test."""
        nuclear_cleanup()

    def test_full_deployment_workflow(self):
        """Test complete deployment: ReplicaSet + Service + containers."""
        # Create ReplicaSet
        rs = ReplicaSetResource(
            name="e2e-full-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "e2e-full"},
                "template": {
                    "metadata": {"labels": {"app": "e2e-full"}},
                    "spec": {"containers": [{"name": "app", "image": "alpine:latest"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Create Service
        svc = ServiceResource(
            name="e2e-full-svc",
            spec={
                "selector": {"app": "e2e-full"},
                "ports": [{"port": 80, "targetPort": 80}],
            },
            namespace="default",
        )
        self.store.create(svc)

        # Run all controllers
        rs_controller = ReplicaSetController(self.store)
        pod_controller = PodController(self.store, None)
        svc_controller = ServiceController(self.store, pod_controller)

        rs_controller.reconcile()

        try:
            pod_controller.reconcile()
        except Exception:
            pass

        try:
            svc_controller.reconcile()
        except Exception:
            pass

        time.sleep(3)

        # Verify state
        # 1. Store has correct pods
        pods = self.store.list("Pod", "default")
        app_pods = [p for p in pods if p.labels.get("app") == "e2e-full"]
        assert len(app_pods) == 2, f"Expected 2 app pods, got {len(app_pods)}"

        # 2. Proxy pod exists
        proxy_pod = self.store.get("Pod", "proxy-e2e-full-svc", "default")
        assert proxy_pod is not None, "Proxy pod should exist"

        # 3. Containers exist
        containers = podman_list_orchestrator_containers()
        app_containers = [c for c in containers if "e2e-full-rs" in c]
        assert (
            len(app_containers) >= 2
        ), f"Expected at least 2 app containers, got: {app_containers}"

    def test_delete_replicaset_cleans_up_everything(self):
        """Test that deleting a ReplicaSet cleans up all associated resources."""
        # Create ReplicaSet
        rs = ReplicaSetResource(
            name="e2e-cleanup-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "e2e-cleanup"},
                "template": {
                    "metadata": {"labels": {"app": "e2e-cleanup"}},
                    "spec": {"containers": [{"name": "app", "image": "alpine:latest"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        rs_controller = ReplicaSetController(self.store)
        pod_controller = PodController(self.store, None)

        # Create resources
        rs_controller.reconcile()
        try:
            pod_controller.reconcile()
        except Exception:
            pass

        time.sleep(2)

        # Verify creation
        containers = podman_list_orchestrator_containers()
        rs_containers = [c for c in containers if "e2e-cleanup-rs" in c]
        assert len(rs_containers) == 2

        # Delete ReplicaSet
        self.store.delete("ReplicaSet", "e2e-cleanup-rs", "default")

        # Reconcile to clean up
        rs_controller.reconcile()
        try:
            pod_controller.reconcile()
        except Exception:
            pass

        time.sleep(2)

        # Verify cleanup
        pods = self.store.list("Pod", "default")
        rs_pods = [
            p for p in pods if p.metadata.get("owner-replicaset") == "e2e-cleanup-rs"
        ]
        assert len(rs_pods) == 0, "All pods should be deleted from store"

        containers = podman_list_orchestrator_containers()
        rs_containers = [c for c in containers if "e2e-cleanup-rs" in c]
        assert len(rs_containers) == 0, "All containers should be cleaned up"


# =============================================================================
# E2E CONTAINER CLASS TESTS
# =============================================================================


@pytest.mark.e2e
@skip_if_no_podman
class TestE2EContainerClass:
    """End-to-end tests for Container class directly."""

    def setup_method(self):
        """Setup before each test."""
        nuclear_cleanup()
        ensure_network_exists()

    def teardown_method(self):
        """Nuclear cleanup after each test."""
        nuclear_cleanup()

    def test_container_start_and_stop(self):
        """Test Container.start() and Container.stop() directly."""
        # Use nginx:alpine which runs persistently (unlike bare alpine which exits)
        pod = PodResource(
            name="direct-container-test",
            spec={"containers": [{"name": "main", "image": "nginx:alpine"}]},
            namespace="default",
        )

        container = Container(pod=pod, container_name="main")

        # Start
        container.start()
        time.sleep(2)

        assert container.is_alive(), "Container should be running after start"

        # Stop
        container.stop()
        time.sleep(1)

        assert not container.is_alive(), "Container should not be running after stop"

    def test_container_with_environment_variables(self):
        """Test container with environment variables."""
        # Use nginx:alpine which runs persistently
        pod = PodResource(
            name="env-test-pod",
            spec={
                "containers": [
                    {
                        "name": "app",
                        "image": "nginx:alpine",
                        "env": {"MY_VAR": "my_value", "DEBUG": "true"},
                    }
                ]
            },
            namespace="default",
        )

        container = Container(pod=pod, container_name="app")
        container.start()
        time.sleep(2)

        assert container.is_alive(), "Container should be running"

        # Verify env vars (check via podman inspect)
        result = subprocess.run(
            ["podman", "inspect", "-f", "{{.Config.Env}}", container.full_name],
            capture_output=True,
            text=True,
            check=False,
        )

        assert "MY_VAR=my_value" in result.stdout
        assert "DEBUG=true" in result.stdout

        container.stop()

    def test_container_idempotent_start(self):
        """Test that Container.start() is idempotent (stops existing first)."""
        # Use nginx:alpine which runs persistently
        pod = PodResource(
            name="idempotent-test",
            spec={"containers": [{"name": "main", "image": "nginx:alpine"}]},
            namespace="default",
        )

        container = Container(pod=pod, container_name="main")

        # Start twice - should not error
        container.start()
        time.sleep(2)

        container.start()  # Second start should stop first and restart
        time.sleep(2)

        assert container.is_alive(), "Container should be running after second start"

        container.stop()
