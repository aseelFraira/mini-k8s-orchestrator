"""
Basic tests for Pod CRUD operations.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from resources import PodResource
from store import ResourceStore


class TestPodCRUD:
    """Tests for Pod create, read, update, delete operations."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_create_pod(self):
        """Test creating a pod."""
        pod = PodResource(
            name="test-pod",
            spec={"containers": [{"name": "web", "image": "nginx"}]},
            metadata={"labels": {"app": "test"}},
            namespace="default",
        )

        created = self.store.create(pod)

        assert created is not None
        assert created.name == "test-pod"
        assert created.namespace == "default"

    def test_get_pod(self):
        """Test retrieving a pod by name."""
        pod = PodResource(
            name="my-pod",
            spec={"containers": [{"name": "app", "image": "alpine"}]},
            namespace="default",
        )
        self.store.create(pod)

        retrieved = self.store.get("Pod", "my-pod", "default")

        assert retrieved is not None
        assert retrieved.name == "my-pod"

    def test_get_nonexistent_pod(self):
        """Test retrieving a pod that doesn't exist."""
        retrieved = self.store.get("Pod", "nonexistent", "default")

        assert retrieved is None

    def test_list_pods(self):
        """Test listing all pods in a namespace."""
        # Create multiple pods
        for i in range(3):
            pod = PodResource(
                name=f"pod-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
                namespace="default",
            )
            self.store.create(pod)

        pods = self.store.list("Pod", "default")

        assert len(pods) == 3
        names = [p.name for p in pods]
        assert "pod-0" in names
        assert "pod-1" in names
        assert "pod-2" in names

    def test_list_pods_different_namespaces(self):
        """Test that pods are isolated by namespace."""
        pod1 = PodResource(name="pod-a", spec={}, namespace="ns1")
        pod2 = PodResource(name="pod-b", spec={}, namespace="ns2")

        self.store.create(pod1)
        self.store.create(pod2)

        ns1_pods = self.store.list("Pod", "ns1")
        ns2_pods = self.store.list("Pod", "ns2")

        assert len(ns1_pods) == 1
        assert len(ns2_pods) == 1
        assert ns1_pods[0].name == "pod-a"
        assert ns2_pods[0].name == "pod-b"

    def test_update_pod(self):
        """Test updating an existing pod."""
        pod = PodResource(
            name="updatable-pod",
            spec={"containers": [{"name": "v1", "image": "nginx:1.0"}]},
            namespace="default",
        )
        self.store.create(pod)

        # Update with new spec
        updated_pod = PodResource(
            name="updatable-pod",
            spec={"containers": [{"name": "v2", "image": "nginx:2.0"}]},
            namespace="default",
        )
        self.store.create_or_update(updated_pod)

        retrieved = self.store.get("Pod", "updatable-pod", "default")
        assert retrieved.containers[0]["image"] == "nginx:2.0"

    def test_delete_pod(self):
        """Test deleting a pod."""
        pod = PodResource(
            name="deletable-pod",
            spec={"containers": [{"name": "app", "image": "alpine"}]},
            namespace="default",
        )
        self.store.create(pod)

        # Verify it exists
        assert self.store.get("Pod", "deletable-pod", "default") is not None

        # Delete it
        result = self.store.delete("Pod", "deletable-pod", "default")

        assert result is True
        assert self.store.get("Pod", "deletable-pod", "default") is None

    def test_delete_nonexistent_pod(self):
        """Test deleting a pod that doesn't exist."""
        result = self.store.delete("Pod", "ghost-pod", "default")

        assert result is False

    def test_pod_spec_hash(self):
        """Test that pod spec hash is generated."""
        pod = PodResource(
            name="hashed-pod",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            namespace="default",
        )

        assert pod.spec_hash is not None
        assert len(pod.spec_hash) > 0

    def test_pod_labels(self):
        """Test that pod labels are accessible."""
        pod = PodResource(
            name="labeled-pod",
            spec={},
            metadata={"labels": {"app": "web", "tier": "frontend"}},
            namespace="default",
        )

        assert pod.labels["app"] == "web"
        assert pod.labels["tier"] == "frontend"
