"""
Basic tests for ReplicaSet CRUD operations.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from resources import ReplicaSetResource, PodResource
from store import ResourceStore


class TestReplicaSetCRUD:
    """Tests for ReplicaSet create, read, update, delete operations."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_create_replicaset(self):
        """Test creating a replicaset."""
        rs = ReplicaSetResource(
            name="my-rs",
            spec={
                "replicas": 3,
                "selector": {"app": "web"},
                "template": {
                    "metadata": {"labels": {"app": "web"}},
                    "spec": {"containers": [{"name": "web", "image": "nginx"}]},
                },
            },
            namespace="default",
        )

        created = self.store.create(rs)

        assert created is not None
        assert created.name == "my-rs"
        assert created.namespace == "default"

    def test_get_replicaset(self):
        """Test retrieving a replicaset by name."""
        rs = ReplicaSetResource(
            name="backend-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "backend"},
                "template": {"spec": {}},
            },
            namespace="default",
        )
        self.store.create(rs)

        retrieved = self.store.get("ReplicaSet", "backend-rs", "default")

        assert retrieved is not None
        assert retrieved.name == "backend-rs"

    def test_get_nonexistent_replicaset(self):
        """Test retrieving a replicaset that doesn't exist."""
        retrieved = self.store.get("ReplicaSet", "ghost-rs", "default")

        assert retrieved is None

    def test_list_replicasets(self):
        """Test listing all replicasets in a namespace."""
        for i in range(3):
            rs = ReplicaSetResource(
                name=f"rs-{i}",
                spec={"replicas": i + 1, "selector": {}, "template": {}},
                namespace="default",
            )
            self.store.create(rs)

        replicasets = self.store.list("ReplicaSet", "default")

        assert len(replicasets) == 3

    def test_replicaset_replicas(self):
        """Test that replicas count is accessible."""
        rs = ReplicaSetResource(
            name="scaled-rs",
            spec={"replicas": 5, "selector": {"app": "scaled"}, "template": {}},
            namespace="default",
        )

        assert rs.replicas == 5

    def test_replicaset_selector(self):
        """Test that selector is accessible."""
        rs = ReplicaSetResource(
            name="selector-rs",
            spec={
                "replicas": 1,
                "selector": {"app": "web", "tier": "frontend"},
                "template": {},
            },
            namespace="default",
        )

        assert rs.selector == {"app": "web", "tier": "frontend"}

    def test_replicaset_template(self):
        """Test that template is accessible."""
        template = {
            "metadata": {"labels": {"app": "web"}},
            "spec": {"containers": [{"name": "web", "image": "nginx:alpine"}]},
        }
        rs = ReplicaSetResource(
            name="template-rs",
            spec={"replicas": 2, "selector": {"app": "web"}, "template": template},
            namespace="default",
        )

        assert rs.template == template

    def test_delete_replicaset(self):
        """Test deleting a replicaset."""
        rs = ReplicaSetResource(
            name="deletable-rs",
            spec={"replicas": 1, "selector": {}, "template": {}},
            namespace="default",
        )
        self.store.create(rs)

        # Verify it exists
        assert self.store.get("ReplicaSet", "deletable-rs", "default") is not None

        # Delete it
        result = self.store.delete("ReplicaSet", "deletable-rs", "default")

        assert result is True
        assert self.store.get("ReplicaSet", "deletable-rs", "default") is None

    def test_delete_nonexistent_replicaset(self):
        """Test deleting a replicaset that doesn't exist."""
        result = self.store.delete("ReplicaSet", "ghost-rs", "default")

        assert result is False

    def test_update_replicaset_replicas(self):
        """Test updating replica count."""
        rs = ReplicaSetResource(
            name="scalable-rs",
            spec={"replicas": 2, "selector": {"app": "scale"}, "template": {}},
            namespace="default",
        )
        self.store.create(rs)

        # Update with new replica count
        updated_rs = ReplicaSetResource(
            name="scalable-rs",
            spec={"replicas": 5, "selector": {"app": "scale"}, "template": {}},
            namespace="default",
        )
        self.store.create_or_update(updated_rs)

        retrieved = self.store.get("ReplicaSet", "scalable-rs", "default")
        assert retrieved.replicas == 5

    def test_replicaset_namespace_isolation(self):
        """Test that replicasets are isolated by namespace."""
        rs1 = ReplicaSetResource(
            name="rs-a",
            spec={"replicas": 1, "selector": {}, "template": {}},
            namespace="ns1",
        )
        rs2 = ReplicaSetResource(
            name="rs-b",
            spec={"replicas": 1, "selector": {}, "template": {}},
            namespace="ns2",
        )

        self.store.create(rs1)
        self.store.create(rs2)

        ns1_rs = self.store.list("ReplicaSet", "ns1")
        ns2_rs = self.store.list("ReplicaSet", "ns2")

        assert len(ns1_rs) == 1
        assert len(ns2_rs) == 1
        assert ns1_rs[0].name == "rs-a"
        assert ns2_rs[0].name == "rs-b"

    def test_replicaset_default_replicas(self):
        """Test that replicas defaults to 1 if not specified."""
        rs = ReplicaSetResource(
            name="default-rs",
            spec={"selector": {"app": "default"}, "template": {}},
            namespace="default",
        )

        assert rs.replicas == 1
