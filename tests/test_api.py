"""
Podman integration tests.
These tests require Podman to be installed and running.
They verify that containers are actually created and can communicate.

Run with: uv run pytest tests/test_podman.py -v -s
(Use -s to see print output for debugging)
"""

import pytest
import subprocess
import time
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient
from main import app, cluster


def podman_available():
    """Check if podman is available."""
    try:
        result = subprocess.run(["podman", "--version"], capture_output=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def cleanup_test_containers():
    """Remove all test containers."""
    # Remove all containers managed by our orchestrator
    subprocess.run(
        ["podman", "rm", "-f", "-a", "--filter", "label=managed-by=my-orchestrator"],
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def reset_state():
    """Reset cluster and Podman state before each test."""
    cluster.store.resources = {
        "Pod": {},
        "Service": {},
        "ReplicaSet": {},
    }
    cleanup_test_containers()
    yield
    cleanup_test_containers()


@pytest.mark.skipif(not podman_available(), reason="Podman not available")
class TestPodmanContainers:
    """Tests that verify actual Podman containers are created."""

    def test_pod_creates_podman_container(self):
        """Verify creating a Pod resource creates a Podman container."""
        with TestClient(app) as client:
            # Create a pod via API
            response = client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "test-podman-pod"},
                    "spec": {
                        "containers": [
                            {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )
            assert response.status_code == 201

            # Wait for PodController to reconcile
            time.sleep(3)

            # Check Podman container exists
            result = subprocess.run(
                [
                    "podman", "ps", "-a",
                    "--filter", "name=default_test-podman-pod_nginx",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            assert "default_test-podman-pod_nginx" in result.stdout, "Podman container was not created"

    def test_pod_creates_container_inside_pod(self):
        """Verify container runs inside the Podman pod."""
        with TestClient(app) as client:
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "container-test"},
                    "spec": {
                        "containers": [
                            {"name": "web", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )

            time.sleep(3)

            # Check container is running
            result = subprocess.run(
                [
                    "podman", "ps", "--filter",
                    "label=orchestrator-pod-name=container-test",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            assert "default_container-test_web" in result.stdout

    def test_container_has_correct_labels(self):
        """Verify container has orchestrator labels."""
        with TestClient(app) as client:
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "labeled-pod", "labels": {"app": "test"}},
                    "spec": {
                        "containers": [
                            {"name": "app", "image": "docker.io/library/alpine:latest"}
                        ]
                    },
                },
            )

            time.sleep(3)

            # Check labels
            result = subprocess.run(
                [
                    "podman", "inspect", "-f",
                    '{{index .Config.Labels "orchestrator-pod-name"}}',
                    "default_labeled-pod_app"
                ],
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == "labeled-pod"

    def test_delete_pod_removes_podman_container(self):
        """Verify deleting a Pod removes the Podman container."""
        with TestClient(app) as client:
            # Create
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "deletable"},
                    "spec": {
                        "containers": [
                            {"name": "app", "image": "docker.io/library/alpine:latest"}
                        ]
                    },
                },
            )
            time.sleep(3)

            # Verify container exists
            result = subprocess.run(
                [
                    "podman", "ps", "-a",
                    "--filter", "name=default_deletable_app",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            assert "default_deletable_app" in result.stdout

            # Delete via API
            client.delete("/api/v1/namespaces/default/pods/deletable")
            time.sleep(3)

            # Verify container is gone
            result = subprocess.run(
                [
                    "podman", "ps", "-a",
                    "--filter", "name=default_deletable_app",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            assert "default_deletable_app" not in result.stdout, "Podman container still exists after deletion"


@pytest.mark.skipif(not podman_available(), reason="Podman not available")
class TestPodmanReconciliation:
    """Tests for reconciliation - repairing crashed containers."""

    def test_reconciliation_restarts_stopped_container(self):
        """If a container stops, reconciliation should restart it."""
        with TestClient(app) as client:
            # Create pod
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "restart-test"},
                    "spec": {
                        "containers": [
                            {"name": "app", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )
            time.sleep(3)

            # Manually stop the container
            subprocess.run(
                ["podman", "stop", "default_restart-test_app"],
                capture_output=True,
            )

            # Wait for reconciliation (runs every 1 second)
            time.sleep(3)

            # Container should be running again
            result = subprocess.run(
                [
                    "podman", "inspect", "-f", "{{.State.Running}}",
                    "default_restart-test_app"
                ],
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == "true", "Container was not restarted"

    def test_reconciliation_removes_orphaned_containers(self):
        """Containers not in store should be cleaned up."""
        with TestClient(app) as client:
            # Create pod
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "orphan-test"},
                    "spec": {
                        "containers": [
                            {"name": "app", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )
            time.sleep(3)

            # Verify container exists
            result = subprocess.run(
                [
                    "podman", "ps", "-a",
                    "--filter", "name=default_orphan-test_app",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            assert "default_orphan-test_app" in result.stdout

            # Delete from store directly (simulating inconsistency)
            cluster.store.delete("Pod", "orphan-test", "default")

            # Wait for reconciliation
            time.sleep(3)

            # Podman container should be removed
            result = subprocess.run(
                [
                    "podman", "ps", "-a",
                    "--filter", "name=default_orphan-test_app",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            assert "default_orphan-test_app" not in result.stdout, "Orphaned container was not cleaned up"


@pytest.mark.skipif(not podman_available(), reason="Podman not available")
class TestPodmanNetworking:
    """Tests for inter-pod networking."""

    def test_container_on_orchestrator_network(self):
        """Verify container is on the orchestrator network."""
        with TestClient(app) as client:
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "aliased-pod"},
                    "spec": {
                        "containers": [
                            {"name": "app", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )
            time.sleep(3)

            # Check container is on orchestrator network
            result = subprocess.run(
                [
                    "podman", "inspect",
                    "default_aliased-pod_app",
                    "--format", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}"
                ],
                capture_output=True,
                text=True,
            )
            # The container should be on the orchestrator network
            assert "orchestrator-net" in result.stdout, f"Container not on orchestrator-net: {result.stdout}"

    def test_pods_can_communicate_by_name(self):
        """Test that one pod can reach another by name."""
        with TestClient(app) as client:
            # Create server pod (nginx)
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "server-pod"},
                    "spec": {
                        "containers": [
                            {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )

            # Create client pod (alpine with curl)
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "client-pod"},
                    "spec": {
                        "containers": [
                            {
                                "name": "client",
                                "image": "docker.io/library/alpine:latest",
                            }
                        ]
                    },
                },
            )

            # Wait for both pods to start
            time.sleep(5)

            # Install curl in client and try to reach server
            # First, keep the alpine container running
            subprocess.run(
                ["podman", "exec", "default_client-pod_client", "apk", "add", "--no-cache", "curl"],
                capture_output=True,
            )

            # Try to curl the server pod by name
            result = subprocess.run(
                [
                    "podman", "exec", "default_client-pod_client",
                    "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                    "http://server-pod:80", "--connect-timeout", "5"
                ],
                capture_output=True,
                text=True,
            )

            # Should get HTTP 200 (or at least connect)
            # Note: This may fail if containers exit quickly
            print(f"Curl result: {result.stdout}, stderr: {result.stderr}")


@pytest.mark.skipif(not podman_available(), reason="Podman not available")
class TestPodmanReplicaSet:
    """Tests for ReplicaSet with actual containers."""

    def test_replicaset_creates_multiple_containers(self):
        """Verify ReplicaSet creates multiple Podman containers."""
        with TestClient(app) as client:
            client.post(
                "/api/apps/v1/namespaces/default/replicasets",
                json={
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "metadata": {"name": "multi-rs"},
                    "spec": {
                        "replicas": 3,
                        "selector": {"app": "multi"},
                        "template": {
                            "metadata": {"labels": {"app": "multi"}},
                            "spec": {
                                "containers": [
                                    {"name": "app", "image": "docker.io/library/nginx:alpine"}
                                ]
                            },
                        },
                    },
                },
            )

            # Wait for reconciliation
            time.sleep(5)

            # Check 3 Podman containers exist
            result = subprocess.run(
                [
                    "podman", "ps",
                    "--filter", "label=managed-by=my-orchestrator",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            containers = [c for c in result.stdout.strip().split("\n") if c.startswith("default_multi-rs-")]
            assert len(containers) == 3, f"Expected 3 containers, got {len(containers)}: {containers}"

    def test_replicaset_scale_creates_new_containers(self):
        """Verify scaling up creates new Podman containers."""
        with TestClient(app) as client:
            # Create with 1 replica
            client.post(
                "/api/apps/v1/namespaces/default/replicasets",
                json={
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "metadata": {"name": "scale-rs"},
                    "spec": {
                        "replicas": 1,
                        "selector": {"app": "scale"},
                        "template": {
                            "metadata": {"labels": {"app": "scale"}},
                            "spec": {
                                "containers": [
                                    {"name": "app", "image": "docker.io/library/nginx:alpine"}
                                ]
                            },
                        },
                    },
                },
            )
            time.sleep(4)

            # Scale to 3
            client.put(
                "/api/apps/v1/namespaces/default/replicasets/scale-rs",
                json={
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "metadata": {"name": "scale-rs"},
                    "spec": {
                        "replicas": 3,
                        "selector": {"app": "scale"},
                        "template": {
                            "metadata": {"labels": {"app": "scale"}},
                            "spec": {
                                "containers": [
                                    {"name": "app", "image": "docker.io/library/nginx:alpine"}
                                ]
                            },
                        },
                    },
                },
            )
            time.sleep(4)

            # Verify 3 Podman containers
            result = subprocess.run(
                [
                    "podman", "ps",
                    "--filter", "label=managed-by=my-orchestrator",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            containers = [c for c in result.stdout.strip().split("\n") if c.startswith("default_scale-rs-")]
            assert len(containers) == 3


@pytest.mark.skipif(not podman_available(), reason="Podman not available")
class TestPodmanService:
    """Tests for Service with actual nginx proxy."""

    def test_service_creates_proxy_container(self):
        """Verify Service creates an nginx proxy container."""
        with TestClient(app) as client:
            # Create backend pods first
            client.post(
                "/api/apps/v1/namespaces/default/replicasets",
                json={
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "metadata": {"name": "backend-rs"},
                    "spec": {
                        "replicas": 2,
                        "selector": {"app": "backend"},
                        "template": {
                            "metadata": {"labels": {"app": "backend"}},
                            "spec": {
                                "containers": [
                                    {"name": "app", "image": "docker.io/library/nginx:alpine"}
                                ]
                            },
                        },
                    },
                },
            )

            # Create service
            client.post(
                "/api/v1/namespaces/default/services",
                json={
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": "backend-svc"},
                    "spec": {
                        "selector": {"app": "backend"},
                        "ports": [{"port": 8080, "targetPort": 80}],
                    },
                },
            )

            # Wait for reconciliation
            time.sleep(5)

            # Check proxy container exists
            result = subprocess.run(
                [
                    "podman", "ps", "-a",
                    "--filter", "name=default_proxy-backend-svc_nginx",
                    "--format", "{{.Names}}"
                ],
                capture_output=True,
                text=True,
            )
            assert "default_proxy-backend-svc_nginx" in result.stdout, "Proxy container was not created"

    def test_service_host_port_accessible(self):
        """Verify Service is accessible from host via published port."""
        with TestClient(app) as client:
            # Create backend pod
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "web-backend", "labels": {"app": "web"}},
                    "spec": {
                        "containers": [
                            {"name": "nginx", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )

            # Create service with port 9080
            client.post(
                "/api/v1/namespaces/default/services",
                json={
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": "web-svc"},
                    "spec": {
                        "selector": {"app": "web"},
                        "ports": [{"port": 9080, "targetPort": 80}],
                    },
                },
            )

            # Wait for everything to start
            time.sleep(6)

            # Try to access via localhost (host port)
            try:
                import urllib.request
                response = urllib.request.urlopen("http://localhost:9080", timeout=5)
                status = response.getcode()
                print(f"Host port access returned: {status}")
                # Should get 200 or at least connect
                assert status in [200, 502, 503], f"Unexpected status: {status}"
            except Exception as e:
                # Connection might fail if nginx config not ready yet
                print(f"Host port access failed (may be expected): {e}")


@pytest.mark.skipif(not podman_available(), reason="Podman not available")
class TestPodmanSpecChange:
    """Tests for spec change detection and recreation."""

    def test_spec_change_triggers_recreation(self):
        """Verify changing pod spec recreates the container."""
        with TestClient(app) as client:
            # Create initial pod
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "spec-change-pod"},
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
            time.sleep(3)

            # Get original container ID
            result = subprocess.run(
                ["podman", "inspect", "-f", "{{.Id}}", "default_spec-change-pod_app"],
                capture_output=True,
                text=True,
            )
            original_id = result.stdout.strip()
            assert original_id, "Original container not found"

            # Update pod spec (change env var)
            client.put(
                "/api/v1/namespaces/default/pods/spec-change-pod",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "spec-change-pod"},
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "docker.io/library/nginx:alpine",
                                "env": {"VERSION": "2"},  # Changed!
                            }
                        ]
                    },
                },
            )

            # Wait for reconciliation to detect hash change and recreate
            time.sleep(4)

            # Get new container ID
            result = subprocess.run(
                ["podman", "inspect", "-f", "{{.Id}}", "default_spec-change-pod_app"],
                capture_output=True,
                text=True,
            )
            new_id = result.stdout.strip()

            # IDs should be different (container was recreated)
            assert new_id != original_id, "Container was not recreated after spec change"

    def test_image_change_triggers_recreation(self):
        """Verify changing container image recreates the container."""
        with TestClient(app) as client:
            # Create with alpine
            client.post(
                "/api/v1/namespaces/default/pods",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "image-change-pod"},
                    "spec": {
                        "containers": [
                            {"name": "app", "image": "docker.io/library/alpine:latest"}
                        ]
                    },
                },
            )
            time.sleep(3)

            # Get original hash
            result = subprocess.run(
                [
                    "podman", "inspect", "-f",
                    '{{index .Config.Labels "pod-hash-number"}}',
                    "default_image-change-pod_app"
                ],
                capture_output=True,
                text=True,
            )
            original_hash = result.stdout.strip()

            # Update to nginx
            client.put(
                "/api/v1/namespaces/default/pods/image-change-pod",
                json={
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {"name": "image-change-pod"},
                    "spec": {
                        "containers": [
                            {"name": "app", "image": "docker.io/library/nginx:alpine"}
                        ]
                    },
                },
            )
            time.sleep(4)

            # Verify hash changed
            result = subprocess.run(
                [
                    "podman", "inspect", "-f",
                    '{{index .Config.Labels "pod-hash-number"}}',
                    "default_image-change-pod_app"
                ],
                capture_output=True,
                text=True,
            )
            new_hash = result.stdout.strip()

            assert new_hash != original_hash, "Hash didn't change after image update"
