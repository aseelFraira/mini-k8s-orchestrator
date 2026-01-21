"""
Basic tests for Service CRUD operations.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from resources import ServiceResource
from store import ResourceStore


class TestServiceCRUD:
    """Tests for Service create, read, update, delete operations."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_create_service(self):
        """Test creating a service."""
        service = ServiceResource(
            name="my-service",
            spec={
                "selector": {"app": "web"},
                "ports": [{"port": 80, "targetPort": 8080}],
            },
            namespace="default",
        )

        created = self.store.create(service)

        assert created is not None
        assert created.name == "my-service"
        assert created.namespace == "default"

    def test_get_service(self):
        """Test retrieving a service by name."""
        service = ServiceResource(
            name="backend-svc",
            spec={
                "selector": {"app": "backend"},
                "ports": [{"port": 3000, "targetPort": 3000}],
            },
            namespace="default",
        )
        self.store.create(service)

        retrieved = self.store.get("Service", "backend-svc", "default")

        assert retrieved is not None
        assert retrieved.name == "backend-svc"

    def test_get_nonexistent_service(self):
        """Test retrieving a service that doesn't exist."""
        retrieved = self.store.get("Service", "ghost-service", "default")

        assert retrieved is None

    def test_list_services(self):
        """Test listing all services in a namespace."""
        for i in range(3):
            service = ServiceResource(
                name=f"svc-{i}",
                spec={"selector": {"app": f"app-{i}"}, "ports": []},
                namespace="default",
            )
            self.store.create(service)

        services = self.store.list("Service", "default")

        assert len(services) == 3

    def test_service_selector(self):
        """Test that service selector is accessible."""
        service = ServiceResource(
            name="selector-svc",
            spec={
                "selector": {"app": "web", "tier": "frontend"},
                "ports": [{"port": 80, "targetPort": 8080}],
            },
            namespace="default",
        )

        assert service.selector == {"app": "web", "tier": "frontend"}

    def test_service_ports(self):
        """Test that service ports are accessible."""
        service = ServiceResource(
            name="ports-svc",
            spec={
                "selector": {"app": "api"},
                "ports": [
                    {"port": 80, "targetPort": 8080},
                    {"port": 443, "targetPort": 8443},
                ],
            },
            namespace="default",
        )

        assert len(service.ports) == 2
        assert service.ports[0]["port"] == 80
        assert service.ports[1]["port"] == 443

    def test_delete_service(self):
        """Test deleting a service."""
        service = ServiceResource(
            name="deletable-svc",
            spec={"selector": {}, "ports": []},
            namespace="default",
        )
        self.store.create(service)

        # Verify it exists
        assert self.store.get("Service", "deletable-svc", "default") is not None

        # Delete it
        result = self.store.delete("Service", "deletable-svc", "default")

        assert result is True
        assert self.store.get("Service", "deletable-svc", "default") is None

    def test_delete_nonexistent_service(self):
        """Test deleting a service that doesn't exist."""
        result = self.store.delete("Service", "ghost-svc", "default")

        assert result is False

    def test_update_service(self):
        """Test updating an existing service."""
        service = ServiceResource(
            name="updatable-svc",
            spec={
                "selector": {"app": "v1"},
                "ports": [{"port": 80, "targetPort": 8080}],
            },
            namespace="default",
        )
        self.store.create(service)

        # Update with new spec
        updated_service = ServiceResource(
            name="updatable-svc",
            spec={
                "selector": {"app": "v2"},
                "ports": [{"port": 8080, "targetPort": 8080}],
            },
            namespace="default",
        )
        self.store.create_or_update(updated_service)

        retrieved = self.store.get("Service", "updatable-svc", "default")
        assert retrieved.selector == {"app": "v2"}
        assert retrieved.ports[0]["port"] == 8080

    def test_service_namespace_isolation(self):
        """Test that services are isolated by namespace."""
        svc1 = ServiceResource(name="svc-a", spec={}, namespace="ns1")
        svc2 = ServiceResource(name="svc-b", spec={}, namespace="ns2")

        self.store.create(svc1)
        self.store.create(svc2)

        ns1_services = self.store.list("Service", "ns1")
        ns2_services = self.store.list("Service", "ns2")

        assert len(ns1_services) == 1
        assert len(ns2_services) == 1
        assert ns1_services[0].name == "svc-a"
        assert ns2_services[0].name == "svc-b"
