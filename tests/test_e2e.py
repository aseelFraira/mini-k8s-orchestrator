"""
End-to-End (E2E) Tests for the Mini-K8s Orchestrator.
"""

import pytest
import time
import urllib.request

from fastapi.testclient import TestClient
from main import app, cluster
from conftest import (
    WAIT_POD_READY,
    WAIT_MULTI_POD,
    WAIT_CLEANUP,
    cleanup_test_containers,
    requires_podman,
    podman_exec,
)

pytestmark = requires_podman


# =============================================================================
# POD E2E TESTS
# =============================================================================


class TestE2EPod:
    """
    E2E tests for Pod Endpoints.
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

    def test_pod_becomes_running(self):
        """Verify a created pod transitions to Running state."""

        # Create a pod with a single container.
        response = self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "e2e-running-pod"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )
        assert response.status_code == 201

        time.sleep(WAIT_POD_READY)

        # Get the pod and verify it is in Running state.
        pod = self.client.get("/api/v1/namespaces/default/pods/e2e-running-pod").json()
        assert pod["status"]["phase"] == "Running", f"Pod not running: {pod['status']}"

    def test_pod_container_status_ready(self):
        """Verify container status shows ready=True when container is running."""

        # Create a pod with a single container.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "e2e-ready-pod"},
                "spec": {
                    "containers": [
                        {"name": "app", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Get the pod and verify the container is in Running state.
        pod = self.client.get("/api/v1/namespaces/default/pods/e2e-ready-pod").json()
        container_statuses = pod["status"].get("containerStatuses", [])
        assert len(container_statuses) == 1
        assert container_statuses[0]["ready"] is True
        assert container_statuses[0]["state"] == "running"

    def test_pod_with_environment_variables(self):
        """Verify pods with environment variables start correctly."""

        # Create a pod with a single container and environment variables.
        response = self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "e2e-env-pod"},
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "docker.io/library/nginx:alpine",
                            "env": {
                                "MY_VAR": "test-value",
                                "ANOTHER_VAR": "another-value",
                            },
                        }
                    ]
                },
            },
        )
        assert response.status_code == 201

        time.sleep(WAIT_POD_READY)

        # Get the pod and verify it is in Running state.
        pod = self.client.get("/api/v1/namespaces/default/pods/e2e-env-pod").json()
        assert pod["status"]["phase"] == "Running"

    def test_pod_labels_preserved(self):
        """Verify pod labels are stored and retrievable via API."""

        # Create a pod with labels.
        response = self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "e2e-labeled-pod",
                    "labels": {"app": "test-app", "version": "v1", "env": "test"},
                },
                "spec": {
                    "containers": [
                        {"name": "app", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )
        assert response.status_code == 201

        time.sleep(WAIT_POD_READY)

        # Get the pod and verify the labels are stored and retrievable via API.
        pod = self.client.get("/api/v1/namespaces/default/pods/e2e-labeled-pod").json()
        assert pod["metadata"]["labels"]["app"] == "test-app"
        assert pod["metadata"]["labels"]["version"] == "v1"
        assert pod["metadata"]["labels"]["env"] == "test"
        assert pod["status"]["phase"] == "Running"

    def test_delete_pod_stops_container(self):
        """Verify deleting a pod stops and removes the container."""

        # Create a pod with a single container.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "e2e-delete-pod"},
                "spec": {
                    "containers": [
                        {"name": "app", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )

        time.sleep(WAIT_POD_READY)

        # Get the pod and verify it is in Running state.
        pod = self.client.get("/api/v1/namespaces/default/pods/e2e-delete-pod").json()
        assert pod["status"]["phase"] == "Running"

        # Delete the pod and verify the container is stopped and removed.
        self.client.delete("/api/v1/namespaces/default/pods/e2e-delete-pod")
        time.sleep(WAIT_CLEANUP)

        # Verify the pod is not found.
        response = self.client.get("/api/v1/namespaces/default/pods/e2e-delete-pod")
        assert response.status_code == 404

    def test_pods_can_reach_each_other_by_name(self):
        """Verify pods in the same network can communicate using pod names."""

        # Create server pod with a single container.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "e2e-server"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )
        time.sleep(WAIT_POD_READY)

        # Create client pod with a single container.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "e2e-client"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )
        time.sleep(WAIT_POD_READY)

        # Get the pods and verify they are in Running state.
        server_pod = self.client.get(
            "/api/v1/namespaces/default/pods/e2e-server"
        ).json()
        client_pod = self.client.get(
            "/api/v1/namespaces/default/pods/e2e-client"
        ).json()
        assert server_pod["status"]["phase"] == "Running"
        assert client_pod["status"]["phase"] == "Running"

        # From client, fetch from server using pod name as hostname
        returncode, stdout, stderr = podman_exec(
            "default_e2e-client_nginx",
            ["wget", "-q", "-O", "-", "-T", "5", "http://e2e-server:80"]
        )
        assert returncode == 0, f"Failed to reach server: {stderr}"

        # Verifies the response contains nginx's default page content (which includes "Welcome to nginx!").
        assert "nginx" in stdout.lower() or "welcome" in stdout.lower()

    def test_pod_can_reach_service_by_name(self):
        """Verify a pod can reach a Service using the service name as hostname."""

        # Create backend pods via ReplicaSet.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-svc-backend-rs"},
                "spec": {
                    "replicas": 1,
                    "selector": {"app": "e2e-svc-backend"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-svc-backend"}},
                        "spec": {
                            "containers": [
                                {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                            ]
                        },
                    },
                },
            },
        )
        time.sleep(WAIT_POD_READY)

        # Create a Service that selects the backend pods.
        self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "e2e-target-svc"},
                "spec": {
                    "selector": {"app": "e2e-svc-backend"},
                    "ports": [{"port": 8888, "targetPort": 80}],
                },
            },
        )
        time.sleep(WAIT_POD_READY)

        # Create a client pod.
        self.client.post(
            "/api/v1/namespaces/default/pods",
            json={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "e2e-svc-client"},
                "spec": {
                    "containers": [
                        {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                    ]
                },
            },
        )
        time.sleep(WAIT_POD_READY)

        # From the client pod, fetch from the service using service name as hostname.
        returncode, stdout, stderr = podman_exec(
            "default_e2e-svc-client_nginx",
            ["wget", "-q", "-O", "-", "-T", "5", "http://e2e-target-svc:8888"]
        )
        assert returncode == 0, f"Failed to reach service: {stderr}"

        # Verify the response contains nginx's default page content.
        assert "nginx" in stdout.lower() or "welcome" in stdout.lower()


# =============================================================================
# REPLICASET E2E TESTS
# =============================================================================


class TestE2EReplicaSet:
    """
    E2E tests for ReplicaSet resources.
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

    def _get_running_pods_with_label(self, key: str, value: str) -> list:
        """Helper: Get pods with a specific label that are in Running state."""
        response = self.client.get("/api/v1/namespaces/default/pods")
        pods = response.json()["items"]
        return [
            p
            for p in pods
            if p.get("metadata", {}).get("labels", {}).get(key) == value
            and p.get("status", {}).get("phase") == "Running"
        ]

    def test_replicaset_creates_running_pods(self):
        """Verify ReplicaSet creates the specified number of running pods."""

        # Create a ReplicaSet with 3 replicas.
        response = self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "e2e-test"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-test"}},
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
        assert response.status_code == 201

        time.sleep(WAIT_MULTI_POD)

        # Get the running pods and verify they are in Running state.
        running_pods = self._get_running_pods_with_label("app", "e2e-test")
        assert len(running_pods) == 3, f"Expected 3 running pods, got {len(running_pods)}"

    def test_replicaset_scale_up(self):
        """Verify scaling up a ReplicaSet creates additional running pods."""

        # Create a ReplicaSet with 1 replica.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-scale-rs"},
                "spec": {
                    "replicas": 1,
                    "selector": {"app": "e2e-scale"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-scale"}},
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

        # Get the running pods and verify they are in Running state.
        assert len(self._get_running_pods_with_label("app", "e2e-scale")) == 1

        # Scale up to 3 replicas.
        self.client.put(
            "/api/apps/v1/namespaces/default/replicasets/e2e-scale-rs",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-scale-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "e2e-scale"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-scale"}},
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

        # Get the running pods and verify they are in Running state.
        assert len(self._get_running_pods_with_label("app", "e2e-scale")) == 3

    def test_replicaset_scale_down(self):
        """Verify scaling down a ReplicaSet terminates excess pods."""

        # Create a ReplicaSet with 3 replicas.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-scaledown-rs"},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "e2e-scaledown"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-scaledown"}},
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

        # Get the running pods and verify they are in Running state.
        assert len(self._get_running_pods_with_label("app", "e2e-scaledown")) == 3

        # Scale down to 1 replica.
        self.client.put(
            "/api/apps/v1/namespaces/default/replicasets/e2e-scaledown-rs",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-scaledown-rs"},
                "spec": {
                    "replicas": 1,
                    "selector": {"app": "e2e-scaledown"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-scaledown"}},
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

        time.sleep(WAIT_CLEANUP)

        # Get the running pods and verify they are in Running state.
        assert len(self._get_running_pods_with_label("app", "e2e-scaledown")) == 1

    def test_delete_replicaset_removes_pods(self):
        """Verify deleting a ReplicaSet removes all managed pods."""

        # Create a ReplicaSet with 2 replicas.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-delete-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "e2e-delete"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-delete"}},
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

        # Get the running pods and verify they are in Running state.
        assert len(self._get_running_pods_with_label("app", "e2e-delete")) == 2

        # Delete the ReplicaSet and verify the pods are removed.
        self.client.delete("/api/apps/v1/namespaces/default/replicasets/e2e-delete-rs")
        time.sleep(WAIT_CLEANUP)

        # Get the running pods and verify they are not in Running state.
        assert len(self._get_running_pods_with_label("app", "e2e-delete")) == 0

    def test_replicaset_self_healing(self):
        """Verify ReplicaSet recreates pods that are manually deleted."""

        # Create a ReplicaSet with 2 replicas.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-heal-rs"},
                "spec": {
                    "replicas": 2,
                    "selector": {"app": "e2e-heal"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-heal"}},
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

        # Get the running pods and verify they are in Running state.
        running_pods = self._get_running_pods_with_label("app", "e2e-heal")
        assert len(running_pods) == 2

        # Delete one pod manually.
        pod_to_delete = running_pods[0]["metadata"]["name"]
        self.client.delete(f"/api/v1/namespaces/default/pods/{pod_to_delete}")

        # Wait for the ReplicaSet controller to recreate the deleted pod.
        time.sleep(WAIT_MULTI_POD)

        # Verify the ReplicaSet recreated the pod and has 2 running pods again.
        running_pods = self._get_running_pods_with_label("app", "e2e-heal")
        assert len(running_pods) == 2, f"Self-healing failed: expected 2 pods, got {len(running_pods)}"


# =============================================================================
# SERVICE E2E TESTS
# =============================================================================


class TestE2EService:
    """
    E2E tests for Service Endpoints.
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

    def test_service_accessible_on_host(self):
        """Verify Service is accessible from host at its exposed port."""

        # Create a ReplicaSet with 1 backend pod.
        self.client.post(
            "/api/apps/v1/namespaces/default/replicasets",
            json={
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {"name": "e2e-host-rs"},
                "spec": {
                    "replicas": 1,
                    "selector": {"app": "e2e-host-backend"},
                    "template": {
                        "metadata": {"labels": {"app": "e2e-host-backend"}},
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

        test_port = 28080
        # Create a Service with host port.
        self.client.post(
            "/api/v1/namespaces/default/services",
            json={
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "e2e-host-svc"},
                "spec": {
                    "selector": {"app": "e2e-host-backend"},
                    "ports": [{"port": test_port, "targetPort": 80}],
                },
            },
        )
        time.sleep(WAIT_MULTI_POD)

        # Verify the Service is accessible from the host.
        try:
            with urllib.request.urlopen(f"http://localhost:{test_port}", timeout=5) as response:
                content = response.read().decode()
                assert response.status == 200

                # Verify the response contains nginx's default page content.
                assert "nginx" in content.lower() or "welcome" in content.lower()

        except Exception as e:
            pytest.fail(f"Service not accessible on host at port {test_port}: {e}")
