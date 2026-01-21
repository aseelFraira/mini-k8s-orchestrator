"""
Integration tests using API calls only.
Tests the full request/response cycle through the FastAPI endpoints.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
from main import app, cluster


@pytest.fixture(autouse=True)
def reset_cluster():
    """Reset cluster state before each test."""
    # Clear all resources
    cluster.store.resources = {
        "Pod": {},
        "Service": {},
        "ReplicaSet": {},
    }
    yield


class TestPodAPI:
    """API tests for Pod endpoints."""

    def test_create_pod_returns_201(self):
        """Test POST /pods returns 201 and created resource."""
        client = TestClient(app)

        response = client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "test-pod"},
                "spec": {"containers": [{"name": "app", "image": "nginx"}]},
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["metadata"]["name"] == "test-pod"
        assert data["kind"] == "Pod"

        # VERIFY STATE CHANGED: Pod should now be retrievable
        get_response = client.get("/api/v1/namespaces/default/pods/test-pod")
        assert get_response.status_code == 200
        assert get_response.json()["metadata"]["name"] == "test-pod"

        # VERIFY IN LIST: Pod should appear in list
        list_response = client.get("/api/v1/namespaces/default/pods")
        pod_names = [p["metadata"]["name"] for p in list_response.json()["items"]]
        assert "test-pod" in pod_names

    def test_get_pod_returns_200(self):
        """Test GET /pods/{name} returns the pod."""
        client = TestClient(app)

        # Create first
        client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "my-pod"},
                "spec": {"containers": [{"name": "app", "image": "alpine"}]},
            },
        )

        # Get
        response = client.get("/api/v1/namespaces/default/pods/my-pod")

        assert response.status_code == 200
        assert response.json()["metadata"]["name"] == "my-pod"

    def test_get_nonexistent_pod_returns_404(self):
        """Test GET /pods/{name} returns 404 for missing pod."""
        client = TestClient(app)

        response = client.get("/api/v1/namespaces/default/pods/ghost")

        assert response.status_code == 404

    def test_list_pods_returns_list(self):
        """Test GET /pods returns list of pods."""
        client = TestClient(app)

        # Create multiple pods
        for i in range(3):
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": f"pod-{i}"},
                    "spec": {},
                },
            )

        response = client.get("/api/v1/namespaces/default/pods")

        assert response.status_code == 200
        data = response.json()
        assert data["kind"] == "PodList"
        assert len(data["items"]) == 3

    def test_delete_pod_returns_success(self):
        """Test DELETE /pods/{name} deletes the pod."""
        client = TestClient(app)

        # Create
        client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "delete-me"},
                "spec": {},
            },
        )

        # Delete
        response = client.delete("/api/v1/namespaces/default/pods/delete-me")
        assert response.status_code == 200

        # Verify gone
        response = client.get("/api/v1/namespaces/default/pods/delete-me")
        assert response.status_code == 404


class TestServiceAPI:
    """API tests for Service endpoints."""

    def test_create_service_returns_201(self):
        """Test POST /services returns 201."""
        client = TestClient(app)

        response = client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "my-svc"},
                "spec": {
                    "selector": {"app": "web"},
                    "ports": [{"port": 80, "targetPort": 8080}],
                },
            },
        )

        assert response.status_code == 201
        assert response.json()["metadata"]["name"] == "my-svc"

        # VERIFY STATE CHANGED: Service should now be retrievable
        get_response = client.get("/api/v1/namespaces/default/services/my-svc")
        assert get_response.status_code == 200
        assert get_response.json()["metadata"]["name"] == "my-svc"

    def test_get_service_returns_200(self):
        """Test GET /services/{name} returns the service."""
        client = TestClient(app)

        # Create
        client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "backend"},
                "spec": {"selector": {}, "ports": []},
            },
        )

        response = client.get("/api/v1/namespaces/default/services/backend")

        assert response.status_code == 200

    def test_list_services_returns_list(self):
        """Test GET /services returns list."""
        client = TestClient(app)

        for i in range(2):
            client.post(
                "/api/v1/namespaces/default/services",
                json={
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": f"svc-{i}"},
                    "spec": {},
                },
            )

        response = client.get("/api/v1/namespaces/default/services")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_delete_service_returns_success(self):
        """Test DELETE /services/{name} deletes."""
        client = TestClient(app)

        client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "temp-svc"},
                "spec": {},
            },
        )

        response = client.delete("/api/v1/namespaces/default/services/temp-svc")
        assert response.status_code == 200


class TestReplicaSetAPI:
    """API tests for ReplicaSet endpoints."""

    def test_create_replicaset_returns_201(self):
        """Test POST /replicasets returns 201."""
        client = TestClient(app)

        response = client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "my-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "web"},
                    "template": {
                        "metadata": {"labels": {"app": "web"}},
                        "spec": {"containers": [{"name": "web", "image": "nginx"}]},
                    },
                },
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["metadata"]["name"] == "my-rs"
        assert data["spec"]["replicas"] == 3

        # VERIFY STATE CHANGED: ReplicaSet should now be retrievable
        get_response = client.get("/api/v1/namespaces/default/replicasets/my-rs")
        assert get_response.status_code == 200
        assert get_response.json()["spec"]["replicas"] == 3

    def test_get_replicaset_returns_200(self):
        """Test GET /replicasets/{name} returns the replicaset."""
        client = TestClient(app)

        client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "backend-rs"},
                "spec": {"replicas": 2, "selector": {}, "template": {}},
            },
        )

        response = client.get("/api/v1/namespaces/default/replicasets/backend-rs")

        assert response.status_code == 200

    def test_list_replicasets_returns_list(self):
        """Test GET /replicasets returns list."""
        client = TestClient(app)

        for i in range(2):
            client.post(
                "/api/v1/namespaces/default/replicasets",
                json={
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "metadata": {"name": f"rs-{i}"},
                    "spec": {"replicas": 1, "selector": {}, "template": {}},
                },
            )

        response = client.get("/api/v1/namespaces/default/replicasets")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_update_replicaset_changes_replicas(self):
        """Test PUT /replicasets/{name} updates the resource."""
        client = TestClient(app)

        # Create with 2 replicas
        client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scalable"},
                "spec": {"replicas": 2, "selector": {"app": "scale"}, "template": {}},
            },
        )

        # Update to 5 replicas
        response = client.put(
            "/api/v1/namespaces/default/replicasets/scalable",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scalable"},
                "spec": {"replicas": 5, "selector": {"app": "scale"}, "template": {}},
            },
        )

        assert response.status_code == 200
        assert response.json()["spec"]["replicas"] == 5

        # VERIFY STATE CHANGED: GET should also return updated value
        get_response = client.get("/api/v1/namespaces/default/replicasets/scalable")
        assert get_response.json()["spec"]["replicas"] == 5

    def test_delete_replicaset_returns_success(self):
        """Test DELETE /replicasets/{name} deletes."""
        client = TestClient(app)

        client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "temp-rs"},
                "spec": {"replicas": 1, "selector": {}, "template": {}},
            },
        )

        response = client.delete("/api/v1/namespaces/default/replicasets/temp-rs")
        assert response.status_code == 200


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_healthz_returns_ok(self):
        """Test /healthz returns ok."""
        client = TestClient(app)

        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestServiceReplicaSetIntegration:
    """Tests for Service + ReplicaSet integration via API."""

    def test_service_and_replicaset_share_selector(self):
        """Test creating a Service and ReplicaSet with matching selectors."""
        client = TestClient(app)

        # 1. Create ReplicaSet with app=backend label
        rs_response = client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "backend-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "backend"},
                    "template": {
                        "metadata": {"labels": {"app": "backend"}},
                        "spec": {
                            "containers": [{"name": "api", "image": "api:latest"}]
                        },
                    },
                },
            },
        )
        assert rs_response.status_code == 201

        # 2. Create Service targeting app=backend
        svc_response = client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "backend-svc"},
                "spec": {
                    "selector": {"app": "backend"},
                    "ports": [{"port": 80, "targetPort": 8080}],
                },
            },
        )
        assert svc_response.status_code == 201

        # 3. Verify both exist
        rs_get = client.get("/api/v1/namespaces/default/replicasets/backend-rs")
        svc_get = client.get("/api/v1/namespaces/default/services/backend-svc")

        assert rs_get.status_code == 200
        assert svc_get.status_code == 200

        # 4. Verify selectors match
        assert rs_get.json()["spec"]["selector"] == {"app": "backend"}
        assert svc_get.json()["spec"]["selector"] == {"app": "backend"}

    def test_replicaset_creates_pods_for_service(self):
        """Test ReplicaSet creates pods that a Service can target."""
        client = TestClient(app)

        # Create ReplicaSet
        client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "web-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "web"},
                    "template": {
                        "metadata": {"labels": {"app": "web"}},
                        "spec": {"containers": [{"name": "nginx", "image": "nginx"}]},
                    },
                },
            },
        )

        # Trigger ReplicaSet controller to create pods
        # (In a real test with running controllers, this would happen automatically)
        # For this test, we manually trigger reconciliation
        from controllers import ReplicaSetController

        controller = ReplicaSetController(cluster.store)
        rs = cluster.store.get("ReplicaSet", "web-rs", "default")
        controller._reconcile_replicaset(rs)

        # Verify pods were created
        pods_response = client.get("/api/v1/namespaces/default/pods")
        pods = pods_response.json()["items"]

        # Filter pods matching the selector
        matching_pods = [
            p
            for p in pods
            if p.get("metadata", {}).get("labels", {}).get("app") == "web"
        ]

        assert len(matching_pods) == 3

        # Create Service
        client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "web-svc"},
                "spec": {
                    "selector": {"app": "web"},
                    "ports": [{"port": 80, "targetPort": 80}],
                },
            },
        )

        # Verify Service exists and has correct selector
        svc = client.get("/api/v1/namespaces/default/services/web-svc")
        assert svc.json()["spec"]["selector"] == {"app": "web"}

    def test_scaling_replicaset_changes_pod_count(self):
        """Test scaling a ReplicaSet changes the number of pods."""
        client = TestClient(app)
        from controllers import ReplicaSetController

        # Create ReplicaSet with 2 replicas
        client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scale-test"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "scale-test"},
                    "template": {
                        "metadata": {"labels": {"app": "scale-test"}},
                        "spec": {"containers": [{"name": "app", "image": "app"}]},
                    },
                },
            },
        )

        # Reconcile to create initial pods
        controller = ReplicaSetController(cluster.store)
        rs = cluster.store.get("ReplicaSet", "scale-test", "default")
        controller._reconcile_replicaset(rs)

        # Verify 2 pods
        pods = [
            p
            for p in cluster.store.list("Pod", "default")
            if p.labels.get("app") == "scale-test"
        ]
        assert len(pods) == 2

        # Scale up to 5
        client.put(
            "/api/v1/namespaces/default/replicasets/scale-test",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scale-test"},
                "spec": {
                    "replicas": 5,
                    "selector": {"app": "scale-test"},
                    "template": {
                        "metadata": {"labels": {"app": "scale-test"}},
                        "spec": {"containers": [{"name": "app", "image": "app"}]},
                    },
                },
            },
        )

        # Reconcile again
        rs = cluster.store.get("ReplicaSet", "scale-test", "default")
        controller._reconcile_replicaset(rs)

        # Verify 5 pods now
        pods = [
            p
            for p in cluster.store.list("Pod", "default")
            if p.labels.get("app") == "scale-test"
        ]
        assert len(pods) == 5

    def test_multiple_services_different_pods(self):
        """Test multiple Services can target different pod groups."""
        client = TestClient(app)
        from controllers import ReplicaSetController

        # Create two ReplicaSets
        client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "frontend-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"tier": "frontend"},
                    "template": {
                        "metadata": {"labels": {"tier": "frontend"}},
                        "spec": {},
                    },
                },
            },
        )
        client.post(
            "/api/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "backend-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"tier": "backend"},
                    "template": {
                        "metadata": {"labels": {"tier": "backend"}},
                        "spec": {},
                    },
                },
            },
        )

        # Reconcile both
        controller = ReplicaSetController(cluster.store)
        for rs in cluster.store.list("ReplicaSet", "default"):
            controller._reconcile_replicaset(rs)

        # Create two Services
        client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "frontend-svc"},
                "spec": {"selector": {"tier": "frontend"}, "ports": [{"port": 80}]},
            },
        )
        client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "backend-svc"},
                "spec": {"selector": {"tier": "backend"}, "ports": [{"port": 3000}]},
            },
        )

        # Verify correct pod counts
        all_pods = cluster.store.list("Pod", "default")
        frontend_pods = [p for p in all_pods if p.labels.get("tier") == "frontend"]
        backend_pods = [p for p in all_pods if p.labels.get("tier") == "backend"]

        assert len(frontend_pods) == 2
        assert len(backend_pods) == 3
