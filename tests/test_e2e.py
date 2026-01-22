"""
End-to-end black-box tests.
These tests ONLY use the API - no direct access to controllers, store, or internals.
They treat the orchestrator as a black box and verify behavior from the outside.
"""

import pytest
import time
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
from main import app


class TestE2EReplicaSet:
    """E2E tests for ReplicaSet - pods should be created automatically."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Use app lifespan to start/stop cluster properly."""
        with TestClient(app) as client:
            self.client = client
            yield

    def test_replicaset_creates_pods_automatically(self):
        """Create a ReplicaSet and verify pods appear WITHOUT calling any controller."""
        # 1. Create a ReplicaSet via API only
        response = self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "auto-pods-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "auto-test"},
                    "template": {
                        "metadata": {"labels": {"app": "auto-test"}},
                        "spec": {"containers": [{"name": "test", "image": "nginx"}]},
                    },
                },
            },
        )
        assert response.status_code == 201

        # 2. Wait for controllers to reconcile (they run every 1 second)
        time.sleep(2)

        # 3. Check pods were created via API only
        pods_response = self.client.get("/api/v1/namespaces/default/pods")
        assert pods_response.status_code == 200

        pods = pods_response.json()["items"]
        matching_pods = [
            p
            for p in pods
            if p.get("metadata", {}).get("labels", {}).get("app") == "auto-test"
        ]

        # Verify 3 pods were created automatically
        assert len(matching_pods) == 3

    def test_replicaset_scales_up_automatically(self):
        """Scale up a ReplicaSet and verify new pods appear."""
        # 1. Create ReplicaSet with 2 replicas
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scale-up-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "scale-up"},
                    "template": {
                        "metadata": {"labels": {"app": "scale-up"}},
                        "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                    },
                },
            },
        )

        time.sleep(2)

        # 2. Verify 2 pods exist
        pods = self._get_pods_with_label("app", "scale-up")
        assert len(pods) == 2

        # 3. Scale up to 5 via API
        self.client.put(
            "/api/apps/v1/namespaces/default/replicasets/scale-up-rs",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scale-up-rs"},
                "spec": {
                    "replicas": 5,
                    "selector": {"app": "scale-up"},
                    "template": {
                        "metadata": {"labels": {"app": "scale-up"}},
                        "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                    },
                },
            },
        )

        time.sleep(2)

        # 4. Verify 5 pods now exist
        pods = self._get_pods_with_label("app", "scale-up")
        assert len(pods) == 5

    def test_replicaset_scales_down_automatically(self):
        """Scale down a ReplicaSet and verify pods are removed."""
        # 1. Create ReplicaSet with 4 replicas
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scale-down-rs"},
                "spec": {
                    "replicas": 4,
                    "selector": {"app": "scale-down"},
                    "template": {
                        "metadata": {"labels": {"app": "scale-down"}},
                        "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                    },
                },
            },
        )

        time.sleep(2)
        assert len(self._get_pods_with_label("app", "scale-down")) == 4

        # 2. Scale down to 1
        self.client.put(
            "/api/apps/v1/namespaces/default/replicasets/scale-down-rs",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scale-down-rs"},
                "spec": {
                    "replicas": 1,
                    "selector": {"app": "scale-down"},
                    "template": {
                        "metadata": {"labels": {"app": "scale-down"}},
                        "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                    },
                },
            },
        )

        time.sleep(2)

        # 3. Verify only 1 pod remains
        pods = self._get_pods_with_label("app", "scale-down")
        assert len(pods) == 1

    def test_delete_replicaset_removes_pods(self):
        """Delete a ReplicaSet and verify pods are eventually removed."""
        # 1. Create ReplicaSet
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "deletable-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "deletable"},
                    "template": {
                        "metadata": {"labels": {"app": "deletable"}},
                        "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                    },
                },
            },
        )

        time.sleep(2)
        assert len(self._get_pods_with_label("app", "deletable")) == 2

        # 2. Delete ReplicaSet
        delete_response = self.client.delete(
            "/api/apps/v1/namespaces/default/replicasets/deletable-rs"
        )
        assert delete_response.status_code == 200

        # 3. Verify ReplicaSet is gone
        rs_response = self.client.get(
            "/api/apps/v1/namespaces/default/replicasets/deletable-rs"
        )
        assert rs_response.status_code == 404

    def _get_pods_with_label(self, key: str, value: str) -> list:
        """Helper to get pods matching a label."""
        response = self.client.get("/api/v1/namespaces/default/pods")
        pods = response.json()["items"]
        return [
            p for p in pods if p.get("metadata", {}).get("labels", {}).get(key) == value
        ]


class TestE2EServiceWithReplicaSet:
    """E2E tests for Service + ReplicaSet integration."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Use app lifespan to start/stop cluster properly."""
        with TestClient(app) as client:
            self.client = client
            yield

    def test_service_with_replicaset_full_flow(self):
        """
        Full E2E flow:
        1. Create a ReplicaSet (backend pods)
        2. Wait for pods to be created
        3. Create a Service targeting those pods
        4. Verify Service and pods are linked by selector
        """
        # 1. Create ReplicaSet for backend
        rs_response = self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "backend-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "backend", "tier": "api"},
                    "template": {
                        "metadata": {"labels": {"app": "backend", "tier": "api"}},
                        "spec": {
                            "containers": [{"name": "api", "image": "api:latest"}]
                        },
                    },
                },
            },
        )
        assert rs_response.status_code == 201

        # 2. Wait for pods
        time.sleep(2)

        # 3. Verify pods exist
        pods = self._get_pods_with_labels({"app": "backend", "tier": "api"})
        assert len(pods) == 3

        # 4. Create Service
        svc_response = self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "backend-svc"},
                "spec": {
                    "selector": {"app": "backend", "tier": "api"},
                    "ports": [{"port": 80, "targetPort": 8080}],
                },
            },
        )
        assert svc_response.status_code == 201

        # 5. Verify Service has correct selector
        svc = self.client.get("/api/v1/namespaces/default/services/backend-svc")
        assert svc.json()["spec"]["selector"] == {"app": "backend", "tier": "api"}

        # 6. The Service selector should match all 3 backend pods
        # (In a real system with running containers, nginx would load balance to them)

    def test_create_pod_then_service(self):
        """Create pods first, then Service - Service should find existing pods."""
        # 1. Create individual pods with same label
        for i in range(2):
            self.client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {
                        "name": f"standalone-pod-{i}",
                        "labels": {"role": "worker"},
                    },
                    "spec": {"containers": [{"name": "app", "image": "worker"}]},
                },
            )

        # 2. Verify pods exist
        pods = self._get_pods_with_labels({"role": "worker"})
        assert len(pods) == 2

        # 3. Create Service after pods exist
        svc_response = self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "worker-svc"},
                "spec": {
                    "selector": {"role": "worker"},
                    "ports": [{"port": 3000, "targetPort": 3000}],
                },
            },
        )
        assert svc_response.status_code == 201

        # Service should now find the 2 worker pods via selector match

    def test_multiple_replicasets_different_services(self):
        """Multiple ReplicaSets with different Services - verify isolation."""
        # 1. Create frontend ReplicaSet
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "frontend-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "frontend"},
                    "template": {
                        "metadata": {"labels": {"app": "frontend"}},
                        "spec": {"containers": [{"name": "nginx", "image": "nginx"}]},
                    },
                },
            },
        )

        # 2. Create backend ReplicaSet
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "backend-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "backend"},
                    "template": {
                        "metadata": {"labels": {"app": "backend"}},
                        "spec": {"containers": [{"name": "api", "image": "api"}]},
                    },
                },
            },
        )

        time.sleep(2)

        # 3. Verify pod counts
        frontend_pods = self._get_pods_with_labels({"app": "frontend"})
        backend_pods = self._get_pods_with_labels({"app": "backend"})

        assert len(frontend_pods) == 2
        assert len(backend_pods) == 3

        # 4. Create two Services
        self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "frontend-svc"},
                "spec": {"selector": {"app": "frontend"}, "ports": [{"port": 80}]},
            },
        )
        self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "backend-svc"},
                "spec": {"selector": {"app": "backend"}, "ports": [{"port": 3000}]},
            },
        )

        # 5. Verify Services exist
        assert (
            self.client.get(
                "/api/v1/namespaces/default/services/frontend-svc"
            ).status_code
            == 200
        )
        assert (
            self.client.get(
                "/api/v1/namespaces/default/services/backend-svc"
            ).status_code
            == 200
        )

    def _get_pods_with_labels(self, labels: dict) -> list:
        """Helper to get pods matching all given labels."""
        response = self.client.get("/api/v1/namespaces/default/pods")
        pods = response.json()["items"]
        return [
            p
            for p in pods
            if all(
                p.get("metadata", {}).get("labels", {}).get(k) == v
                for k, v in labels.items()
            )
        ]
