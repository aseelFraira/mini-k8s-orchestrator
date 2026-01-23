"""
Thread safety tests.
Verify that parallel API requests don't corrupt state.
"""

import pytest
import sys
import os
import concurrent.futures
import threading

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
from main import app, cluster


@pytest.fixture(autouse=True)
def reset_cluster():
    """Reset cluster state before each test."""
    cluster.store.resources = {
        "Pod": {},
        "Service": {},
        "ReplicaSet": {},
    }
    yield


class TestThreadSafety:
    """Tests for thread-safe operations."""

    def test_parallel_pod_creation(self):
        """Test creating many pods in parallel doesn't corrupt state."""
        client = TestClient(app)
        num_pods = 20
        errors = []

        def create_pod(i):
            try:
                response = client.post(
                    "/api/v1/namespaces/default/pods",
                    json={
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": f"parallel-pod-{i}"},
                        "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                    },
                )
                return response.status_code, i
            except Exception as e:
                errors.append((i, str(e)))
                return None, i

        # Create pods in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(create_pod, i) for i in range(num_pods)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Verify no errors
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # Verify all pods created (status 201)
        success_count = sum(1 for status, _ in results if status == 201)
        assert success_count == num_pods, f"Only {success_count}/{num_pods} pods created"

        # Verify all pods exist in store
        pods = cluster.store.list("Pod", "default")
        assert len(pods) == num_pods

        # Verify no duplicates
        names = [p.name for p in pods]
        assert len(names) == len(set(names)), "Duplicate pod names found!"

    def test_parallel_pod_deletion(self):
        """Test deleting many pods in parallel doesn't corrupt state."""
        client = TestClient(app)
        num_pods = 15

        # First, create pods sequentially
        for i in range(num_pods):
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": f"delete-pod-{i}"},
                    "spec": {},
                },
            )

        assert len(cluster.store.list("Pod", "default")) == num_pods

        # Delete in parallel
        def delete_pod(i):
            response = client.delete(f"/api/v1/namespaces/default/pods/delete-pod-{i}")
            return response.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(delete_pod, i) for i in range(num_pods)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All should return 200
        assert all(status == 200 for status in results)

        # Store should be empty
        assert len(cluster.store.list("Pod", "default")) == 0

    def test_parallel_create_and_delete(self):
        """Test creating and deleting pods simultaneously."""
        client = TestClient(app)
        results = {"creates": [], "deletes": []}
        lock = threading.Lock()

        # Pre-create some pods to delete
        for i in range(10):
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": f"existing-{i}"},
                    "spec": {},
                },
            )

        def create_pod(i):
            response = client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": f"new-pod-{i}"},
                    "spec": {},
                },
            )
            with lock:
                results["creates"].append(response.status_code)

        def delete_pod(i):
            response = client.delete(f"/api/v1/namespaces/default/pods/existing-{i}")
            with lock:
                results["deletes"].append(response.status_code)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            # Submit mixed creates and deletes
            futures = []
            for i in range(10):
                futures.append(executor.submit(create_pod, i))
                futures.append(executor.submit(delete_pod, i))

            concurrent.futures.wait(futures)

        # All creates should succeed
        assert all(s == 201 for s in results["creates"])
        # All deletes should succeed
        assert all(s == 200 for s in results["deletes"])

        # Only new pods should remain
        pods = cluster.store.list("Pod", "default")
        names = [p.name for p in pods]
        assert len(pods) == 10
        assert all(n.startswith("new-pod-") for n in names)

    def test_parallel_replicaset_scaling(self):
        """Test scaling ReplicaSets in parallel doesn't corrupt state."""
        client = TestClient(app)

        # Create a ReplicaSet
        client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "scale-race"},
                "spec": {
                    "replicas": 1,
                    "selector": {"app": "scale-race"},
                    "template": {
                        "metadata": {"labels": {"app": "scale-race"}},
                        "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                    },
                },
            },
        )

        # Scale up and down rapidly in parallel
        def scale_to(n):
            return client.put(
                "/api/apps/v1/namespaces/default/replicasets/scale-race",
                json={
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "metadata": {"name": "scale-race"},
                    "spec": {
                        "replicas": n,
                        "selector": {"app": "scale-race"},
                        "template": {
                            "metadata": {"labels": {"app": "scale-race"}},
                            "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                        },
                    },
                },
            ).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # Rapidly change replica count
            futures = [executor.submit(scale_to, n) for n in [5, 3, 10, 2, 7, 1, 8]]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All should succeed
        assert all(s == 200 for s in results)

        # ReplicaSet should still exist and be valid
        rs = cluster.store.get("ReplicaSet", "scale-race", "default")
        assert rs is not None
        assert rs.replicas >= 1  # Should have some valid replica count

    def test_parallel_service_operations(self):
        """Test Service operations in parallel."""
        client = TestClient(app)
        num_services = 15

        def create_service(i):
            return client.post(
                "/api/v1/namespaces/default/services",
                json={
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": f"svc-{i}"},
                    "spec": {
                        "selector": {"app": f"app-{i}"},
                        "ports": [{"port": 80 + i, "targetPort": 8080}],
                    },
                },
            ).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(create_service, i) for i in range(num_services)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert all(s == 201 for s in results)
        assert len(cluster.store.list("Service", "default")) == num_services

    def test_concurrent_read_write(self):
        """Test reading while writing doesn't cause issues."""
        client = TestClient(app)
        errors = []

        # Create some initial pods
        for i in range(5):
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": f"initial-{i}"},
                    "spec": {},
                },
            )

        def reader():
            for _ in range(20):
                try:
                    response = client.get("/api/v1/namespaces/default/pods")
                    if response.status_code != 200:
                        errors.append(f"Read failed: {response.status_code}")
                except Exception as e:
                    errors.append(f"Read exception: {e}")

        def writer(start):
            for i in range(5):
                try:
                    client.post(
                        "/api/v1/namespaces/default/pods",
                        json={
                            "apiVersion": "v1",
                            "kind": "Pod",
                            "metadata": {"name": f"written-{start}-{i}"},
                            "spec": {},
                        },
                    )
                except Exception as e:
                    errors.append(f"Write exception: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Mix readers and writers
            futures = []
            for i in range(3):
                futures.append(executor.submit(reader))
                futures.append(executor.submit(writer, i * 10))

            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Errors: {errors}"
