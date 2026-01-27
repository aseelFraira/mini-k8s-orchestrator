"""
Podman Integration Tests for the Mini-K8s Orchestrator.
"""

import pytest
import time

from fastapi.testclient import TestClient
from main import app, cluster
from conftest import (
    WAIT_POD_READY,
    WAIT_MULTI_POD,
    WAIT_CLEANUP,
    cleanup_test_containers,
    requires_podman,
    podman_container_exists,
    podman_get_label,
    podman_is_running,
    podman_get_id,
    podman_list_containers,
    podman_get_network,
    podman_stop,
)

pytestmark = requires_podman


# =============================================================================
# CONTAINER LIFECYCLE TESTS
# =============================================================================


class TestContainerLifecycle:
    """
    Tests for container creation and deletion via podman.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_pod_creates_podman_container(self):
        """Verify creating a Pod resource creates a Podman container."""

        # Create a pod via API.
        response = self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-create-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )
        assert response.status_code == 201

        time.sleep(WAIT_POD_READY)

        # Check Podman container exists.
        assert podman_container_exists("default_podman-create-pod_nginx")

    def test_delete_pod_removes_podman_container(self):
        """Verify deleting a Pod removes the Podman container."""

        # Create a pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-delete-pod"},
                "spec": {
                    "containers": [
                        {"name": "app", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Verify container exists.
        assert podman_container_exists("default_podman-delete-pod_app")

        # Delete via API.
        self.client.delete("/api/v1/namespaces/default/pods/podman-delete-pod")
        time.sleep(WAIT_CLEANUP)

        # Verify container is gone.
        assert not podman_container_exists("default_podman-delete-pod_app")


# =============================================================================
# CONTAINER LABELS TESTS
# =============================================================================


class TestContainerLabels:
    """
    Tests for container labels and metadata.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_container_has_managed_by_label(self):
        """Verify containers have managed-by label for tracking."""

        # Create a pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-labels-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Check managed-by label.
        assert podman_get_label("default_podman-labels-pod_nginx", "managed-by") == "my-orchestrator"

    def test_container_has_pod_name_label(self):
        """Verify containers have orchestrator-pod-name label."""

        # Create a pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-podname-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Check pod name label.
        assert podman_get_label("default_podman-podname-pod_nginx", "orchestrator-pod-name") == "podman-podname-pod"


# =============================================================================
# NETWORKING TESTS
# =============================================================================


class TestContainerNetworking:
    """
    Tests for container networking configuration.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_container_on_orchestrator_network(self):
        """Verify container is on the orchestrator network."""

        # Create a pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-net-pod"},
                "spec": {
                    "containers": [
                        {"name": "app", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Check container is on orchestrator network.
        assert "orchestrator-net" in podman_get_network("default_podman-net-pod_app")


# =============================================================================
# RECONCILIATION TESTS
# =============================================================================


class TestReconciliation:
    """
    Tests for reconciliation behavior - repairing inconsistencies.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_stopped_container_is_restarted(self):
        """Verify controller restarts stopped containers."""

        # Create a pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-restart-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Stop the container manually.
        podman_stop("default_podman-restart-pod_nginx")

        # Wait for reconciliation to restart it.
        time.sleep(WAIT_POD_READY)

        # Verify container is running again.
        assert podman_is_running("default_podman-restart-pod_nginx")

    def test_orphaned_container_is_removed(self):
        """Containers not in store should be cleaned up."""

        # Create a pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-orphan-pod"},
                "spec": {
                    "containers": [
                        {"name": "app", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Verify container exists.
        assert podman_container_exists("default_podman-orphan-pod_app")

        # Delete from store directly (simulating inconsistency).
        cluster.store.delete("Pod", "podman-orphan-pod", "default")

        # Wait for reconciliation.
        time.sleep(WAIT_POD_READY)

        # Podman container should be removed.
        assert not podman_container_exists("default_podman-orphan-pod_app")


# =============================================================================
# SPEC CHANGE TESTS
# =============================================================================


class TestSpecChanges:
    """
    Tests for spec change detection and container recreation.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_env_change_triggers_recreation(self):
        """Verify changing pod env vars recreates the container."""

        # Create initial pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-env-change-pod"},
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:alpine",
                            "env": {"VERSION": "1"},
                        }
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Get original container ID.
        original_id = podman_get_id("default_podman-env-change-pod_app")
        assert original_id, "Original container not found"

        # Update pod spec (change env var).
        self.client.put(
            "/api/v1/namespaces/default/pods/podman-env-change-pod",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-env-change-pod"},
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:alpine",
                            "env": {"VERSION": "2"},
                        }
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Get new container ID.
        new_id = podman_get_id("default_podman-env-change-pod_app")

        # IDs should be different (container was recreated).
        assert new_id != original_id

    def test_image_change_triggers_recreation(self):
        """Verify changing container image recreates the container."""

        # Create with alpine.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-image-change-pod"},
                "spec": {
                    "containers": [
                        {"name": "app", "image": "docker.io/library/alpine:latest"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Get original hash.
        original_hash = podman_get_label("default_podman-image-change-pod_app", "pod-hash-number")

        # Update to nginx.
        self.client.put(
            "/api/v1/namespaces/default/pods/podman-image-change-pod",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "podman-image-change-pod"},
                "spec": {
                    "containers": [
                        {"name": "app", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Verify hash changed.
        new_hash = podman_get_label("default_podman-image-change-pod_app", "pod-hash-number")

        assert new_hash != original_hash


# =============================================================================
# REPLICASET TESTS
# =============================================================================


class TestReplicaSetPods:
    """
    Tests for ReplicaSet pod creation via podman.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_replicaset_creates_multiple_pods(self):
        """Verify ReplicaSet creates multiple Pods (and their containers)."""

        # Create a ReplicaSet.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "podman-multi-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "podman-multi"},
                    "template": {
                        "metadata": {"labels": {"app": "podman-multi"}},
                        "spec": {
                            "containers": [
                                {"name": "app", "image": "docker.io/library/nginx:alpine"}
                            ]
                        },
                    },
                },
            },
        )

        time.sleep(WAIT_MULTI_POD)

        # Verify 3 pods created (1 container each = 3 Podman containers).
        containers = [c for c in podman_list_containers() if c.startswith("default_podman-multi-rs-")]
        assert len(containers) == 3

    def test_replicaset_scale_creates_new_pods(self):
        """Verify scaling up creates new Pods."""

        # Create with 1 replica.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "podman-scale-rs"},
                "spec": {
                    "replicas": 1,
                    "selector": {"app": "podman-scale"},
                    "template": {
                        "metadata": {"labels": {"app": "podman-scale"}},
                        "spec": {
                            "containers": [
                                {"name": "app", "image": "docker.io/library/nginx:alpine"}
                            ]
                        },
                    },
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Scale to 3.
        self.client.put(
            "/api/apps/v1/namespaces/default/replicasets/podman-scale-rs",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "podman-scale-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "podman-scale"},
                    "template": {
                        "metadata": {"labels": {"app": "podman-scale"}},
                        "spec": {
                            "containers": [
                                {"name": "app", "image": "docker.io/library/nginx:alpine"}
                            ]
                        },
                    },
                },
            },
        )

        time.sleep(WAIT_MULTI_POD)

        # Verify scaled to 3 pods (1 container each = 3 Podman containers).
        containers = [c for c in podman_list_containers() if c.startswith("default_podman-scale-rs-")]
        assert len(containers) == 3


# =============================================================================
# SERVICE PROXY TESTS
# =============================================================================


class TestServiceProxy:
    """
    Tests for Service proxy container creation.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_service_creates_proxy_container(self):
        """Verify Service creates an nginx proxy container."""

        # Create backend pods first.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "podman-backend-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "podman-backend"},
                    "template": {
                        "metadata": {"labels": {"app": "podman-backend"}},
                        "spec": {
                            "containers": [
                                {"name": "app", "image": "docker.io/library/nginx:alpine"}
                            ]
                        },
                    },
                },
            },
        )

        # Create service.
        self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "podman-backend-svc"},
                "spec": {
                    "selector": {"app": "podman-backend"},
                    "ports": [{"port": 18086, "targetPort": 80}],
                },
            },
        )

        time.sleep(WAIT_MULTI_POD)

        # Check proxy container exists.
        assert podman_container_exists("default_proxy-podman-backend-svc_nginx")
