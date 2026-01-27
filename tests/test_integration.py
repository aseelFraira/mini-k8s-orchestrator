"""
Integration Tests for the Mini-K8s Orchestrator.
"""

import pytest
import time

from fastapi.testclient import TestClient
from main import app, cluster
from conftest import (
    WAIT_POD_READY,
    WAIT_MULTI_POD,
    cleanup_test_containers,
    requires_podman,
)

pytestmark = requires_podman


# =============================================================================
# STORE AND API CONSISTENCY TESTS
# =============================================================================


class TestStoreAPIConsistency:
    """Tests for Store and API consistency."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_api_list_reflects_store_state(self):
        """Verify GET /pods returns all pods in store."""

        # Create multiple pods via API.
        for i in range(3):
            self.client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": f"int-list-pod-{i}"},
                    "spec": {
                        "containers": [
                            {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )

        # Verify list returns all created pods.
        response = self.client.get("/api/v1/namespaces/default/pods")
        pods = response.json()["items"]
        names = [p["metadata"]["name"] for p in pods]

        assert "int-list-pod-0" in names
        assert "int-list-pod-1" in names
        assert "int-list-pod-2" in names

    def test_api_get_returns_updated_status(self):
        """Verify GET /pods/{name} returns updated status from reconciliation."""

        # Create a pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "int-status-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Verify status is updated to Running.
        response = self.client.get("/api/v1/namespaces/default/pods/int-status-pod")
        status = response.json().get("status", {})
        assert status.get("phase") == "Running", f"Expected Running, got: {status}"

    def test_api_delete_removes_from_store(self):
        """Verify DELETE removes resource from store."""

        # Create a pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "int-delete-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        # Verify it exists.
        response = self.client.get("/api/v1/namespaces/default/pods/int-delete-pod")
        assert response.status_code == 200

        # Delete the pod.
        self.client.delete("/api/v1/namespaces/default/pods/int-delete-pod")

        # Verify it's gone.
        response = self.client.get("/api/v1/namespaces/default/pods/int-delete-pod")
        assert response.status_code == 404


# =============================================================================
# REPLICASET CONTROLLER INTEGRATION TESTS
# =============================================================================


class TestReplicaSetControllerIntegration:
    """Tests for ReplicaSet Controller behavior."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_replicaset_pod_naming_convention(self):
        """Verify pods created by ReplicaSet follow naming convention."""

        # Create a ReplicaSet with 3 replicas.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "int-naming-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "int-naming"},
                    "template": {
                        "metadata": {"labels": {"app": "int-naming"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "nginx",
                                    "image": "docker.io/library/nginx:alpine",
                                }
                            ]
                        },
                    },
                },
            },
        )

        time.sleep(WAIT_MULTI_POD)

        # Verify pod names follow convention: {rs-name}-{index}.
        response = self.client.get("/api/v1/namespaces/default/pods")
        pods = response.json()["items"]
        pod_names = [p["metadata"]["name"] for p in pods]

        assert "int-naming-rs-0" in pod_names
        assert "int-naming-rs-1" in pod_names
        assert "int-naming-rs-2" in pod_names

    def test_replicaset_owner_references(self):
        """Verify pods have correct owner references to ReplicaSet."""

        # Create a ReplicaSet.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "int-owner-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "int-owner"},
                    "template": {
                        "metadata": {"labels": {"app": "int-owner"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "nginx",
                                    "image": "docker.io/library/nginx:alpine",
                                }
                            ]
                        },
                    },
                },
            },
        )

        time.sleep(WAIT_MULTI_POD)

        # Verify pods have owner-replicaset metadata.
        response = self.client.get("/api/v1/namespaces/default/pods")
        pods = response.json()["items"]

        for pod in pods:
            if "int-owner-rs" in pod["metadata"]["name"]:
                assert pod["metadata"].get("owner-replicaset") == "int-owner-rs"


# =============================================================================
# SERVICE CONTROLLER INTEGRATION TESTS
# =============================================================================


class TestServiceControllerIntegration:
    """Tests for Service Controller behavior."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_service_tracks_ready_backends(self):
        """Verify Service controller updates status with correct backend count."""

        # Create a ReplicaSet with 2 backend pods.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "int-backend-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "int-backend"},
                    "template": {
                        "metadata": {"labels": {"app": "int-backend"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "nginx",
                                    "image": "docker.io/library/nginx:alpine",
                                }
                            ]
                        },
                    },
                },
            },
        )

        time.sleep(WAIT_MULTI_POD)

        # Create a Service that selects the backend pods.
        self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "int-backend-svc"},
                "spec": {
                    "selector": {"app": "int-backend"},
                    "ports": [{"port": 18081, "targetPort": 80}],
                },
            },
        )

        time.sleep(WAIT_MULTI_POD)

        # Verify the controller updated the status with ready backends.
        svc = self.client.get("/api/v1/namespaces/default/services/int-backend-svc").json()
        assert svc["status"]["readyBackends"] == 2
        assert svc["status"]["phase"] == "Active"

    def test_service_updates_backends_on_scale(self):
        """Verify Service status updates when backend count changes."""

        # Create a ReplicaSet with 1 backend pod.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "int-scale-rs"},
                "spec": {
                    "replicas": 1,
                    "selector": {"app": "int-scale"},
                    "template": {
                        "metadata": {"labels": {"app": "int-scale"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "nginx",
                                    "image": "docker.io/library/nginx:alpine",
                                }
                            ]
                        },
                    },
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Create a Service.
        self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "int-scale-svc"},
                "spec": {
                    "selector": {"app": "int-scale"},
                    "ports": [{"port": 18082, "targetPort": 80}],
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Verify initial backend count.
        svc = self.client.get("/api/v1/namespaces/default/services/int-scale-svc").json()
        assert svc["status"]["readyBackends"] == 1

        # Scale up to 3 replicas.
        self.client.put(
            "/api/apps/v1/namespaces/default/replicasets/int-scale-rs",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "int-scale-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "int-scale"},
                    "template": {
                        "metadata": {"labels": {"app": "int-scale"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "nginx",
                                    "image": "docker.io/library/nginx:alpine",
                                }
                            ]
                        },
                    },
                },
            },
        )

        time.sleep(WAIT_MULTI_POD)

        # Verify updated backend count.
        svc = self.client.get("/api/v1/namespaces/default/services/int-scale-svc").json()
        assert svc["status"]["readyBackends"] == 3


# =============================================================================
# NAMESPACE ISOLATION TESTS
# =============================================================================


class TestNamespaceIsolation:
    """Tests for namespace isolation behavior."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset cluster state and clean up containers before/after each test."""
        cluster.store.resources = {"Pod": {}, "Service": {}, "ReplicaSet": {}}
        cleanup_test_containers()
        with TestClient(app) as client:
            self.client = client
            yield
        cleanup_test_containers()

    def test_same_name_different_namespaces(self):
        """Verify same name in different namespaces are separate resources."""

        # Create pod in default namespace.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "int-ns-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        # Create pod with same name in production namespace.
        self.client.post(
            "/api/v1/namespaces/production/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "int-ns-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        # Verify both exist independently.
        response = self.client.get("/api/v1/namespaces/default/pods/int-ns-pod")
        assert response.status_code == 200

        response = self.client.get("/api/v1/namespaces/production/pods/int-ns-pod")
        assert response.status_code == 200

    def test_delete_only_affects_target_namespace(self):
        """Verify delete only affects target namespace."""

        # Create pods in both namespaces.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "int-ns-delete"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )
        self.client.post(
            "/api/v1/namespaces/production/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "int-ns-delete"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        # Delete from default only.
        self.client.delete("/api/v1/namespaces/default/pods/int-ns-delete")

        # Verify default is gone but production remains.
        response = self.client.get("/api/v1/namespaces/default/pods/int-ns-delete")
        assert response.status_code == 404

        response = self.client.get("/api/v1/namespaces/production/pods/int-ns-delete")
        assert response.status_code == 200

    def test_service_only_selects_same_namespace_pods(self):
        """Verify Service only selects pods in the same namespace."""

        # Create pod in default namespace.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "int-ns-backend", "labels": {"app": "int-ns"}},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Create Service in production namespace (should NOT select default's pod).
        self.client.post(
            "/api/v1/namespaces/production/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "int-ns-svc"},
                "spec": {
                    "selector": {"app": "int-ns"},
                    "ports": [{"port": 18084, "targetPort": 80}],
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Verify Service has 0 backends (pod is in different namespace).
        svc = self.client.get("/api/v1/namespaces/production/services/int-ns-svc").json()
        assert svc["status"]["readyBackends"] == 0
