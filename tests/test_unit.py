"""
Layer 1: Unit Tests for Mini-K8s Orchestrator
Tests for Resource models, Store logic, and YAML parsing.
No external dependencies - pure logic testing.
"""

import pytest
import sys
import os
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from resources import (
    Resource,
    ResourceType,
    PodResource,
    ServiceResource,
    ReplicaSetResource,
)
from store import ResourceStore


# =============================================================================
# POD RESOURCE TESTS
# =============================================================================


class TestPodResourceCreation:
    """Tests for PodResource instantiation and basic properties."""

    def test_creates_pod_with_minimal_spec(self, sample_pod_spec):
        """Test creating a pod with minimal required fields."""
        pod = PodResource(name="test-pod", spec=sample_pod_spec)

        assert pod.name == "test-pod"
        assert pod.kind == "Pod"
        assert pod.api_version == "v1"
        assert pod.namespace == "default"

    def test_creates_pod_with_custom_namespace(self, sample_pod_spec):
        """Test creating a pod in a custom namespace."""
        pod = PodResource(
            name="test-pod",
            spec=sample_pod_spec,
            namespace="production",
        )

        assert pod.namespace == "production"

    def test_creates_pod_with_metadata(self, sample_pod_spec):
        """Test creating a pod with labels and metadata."""
        pod = PodResource(
            name="labeled-pod",
            spec=sample_pod_spec,
            metadata={"labels": {"app": "web", "env": "test"}},
        )

        assert pod.labels == {"app": "web", "env": "test"}

    def test_generates_spec_hash(self, sample_pod_spec):
        """Test that spec_hash is generated deterministically."""
        pod1 = PodResource(name="pod1", spec=sample_pod_spec)
        pod2 = PodResource(name="pod2", spec=sample_pod_spec)

        # Same spec should produce same hash
        assert pod1.spec_hash == pod2.spec_hash
        assert len(pod1.spec_hash) == 64  # SHA256 hex length

    def test_different_specs_produce_different_hashes(self):
        """Test that different specs produce different hashes."""
        pod1 = PodResource(
            name="pod1",
            spec={"containers": [{"name": "a", "image": "nginx"}]},
        )
        pod2 = PodResource(
            name="pod2",
            spec={"containers": [{"name": "b", "image": "alpine"}]},
        )

        assert pod1.spec_hash != pod2.spec_hash


class TestPodResourceProperties:
    """Tests for PodResource property accessors."""

    def test_containers_property(self):
        """Test accessing containers from spec."""
        pod = PodResource(
            name="multi-container",
            spec={
                "containers": [
                    {"name": "app", "image": "nginx"},
                    {"name": "sidecar", "image": "busybox"},
                ]
            },
        )

        assert len(pod.containers) == 2
        assert pod.containers[0]["name"] == "app"
        assert pod.containers[1]["name"] == "sidecar"

    def test_containers_empty_when_missing(self):
        """Test containers returns empty list when not specified."""
        pod = PodResource(name="empty-pod", spec={})

        assert pod.containers == []

    def test_first_container_property(self):
        """Test accessing first container."""
        pod = PodResource(
            name="pod",
            spec={"containers": [{"name": "main", "image": "nginx"}]},
        )

        assert pod.first_container["name"] == "main"

    def test_first_container_none_when_empty(self):
        """Test first_container returns None when no containers."""
        pod = PodResource(name="empty", spec={})

        assert pod.first_container is None

    def test_image_property(self):
        """Test image property returns first container's image."""
        pod = PodResource(
            name="pod",
            spec={"containers": [{"name": "main", "image": "nginx:alpine"}]},
        )

        assert pod.image == "nginx:alpine"

    def test_image_none_when_no_containers(self):
        """Test image returns None when no containers."""
        pod = PodResource(name="empty", spec={})

        assert pod.image is None

    def test_env_property_with_list_format(self):
        """Test env property with Kubernetes-style list format."""
        pod = PodResource(
            name="pod",
            spec={
                "containers": [
                    {
                        "name": "app",
                        "image": "nginx",
                        "env": [
                            {"name": "DB_HOST", "value": "localhost"},
                            {"name": "DB_PORT", "value": "5432"},
                        ],
                    }
                ]
            },
        )

        env = pod.env
        assert env["DB_HOST"] == "localhost"
        assert env["DB_PORT"] == "5432"

    def test_env_property_with_dict_format(self):
        """Test env property with simple dict format."""
        pod = PodResource(
            name="pod",
            spec={
                "containers": [
                    {
                        "name": "app",
                        "image": "nginx",
                        "env": {"KEY": "value"},
                    }
                ]
            },
        )

        assert pod.env == {"KEY": "value"}

    def test_env_empty_dict_when_missing(self):
        """Test env returns empty dict when not specified."""
        pod = PodResource(
            name="pod",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
        )

        assert pod.env == {}

    def test_labels_property(self):
        """Test labels property from metadata."""
        pod = PodResource(
            name="pod",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            metadata={"labels": {"app": "web", "version": "v1"}},
        )

        assert pod.labels == {"app": "web", "version": "v1"}

    def test_labels_empty_when_no_metadata(self):
        """Test labels returns empty dict when no metadata."""
        pod = PodResource(
            name="pod",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
        )

        assert pod.labels == {}

    def test_get_container_spec_found(self):
        """Test get_container_spec returns correct container."""
        pod = PodResource(
            name="pod",
            spec={
                "containers": [
                    {"name": "app", "image": "nginx"},
                    {"name": "sidecar", "image": "busybox"},
                ]
            },
        )

        sidecar = pod.get_container_spec("sidecar")
        assert sidecar is not None
        assert sidecar["image"] == "busybox"

    def test_get_container_spec_not_found(self):
        """Test get_container_spec returns None for unknown container."""
        pod = PodResource(
            name="pod",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
        )

        assert pod.get_container_spec("nonexistent") is None


class TestPodResourceSerialization:
    """Tests for PodResource serialization."""

    def test_to_dict_structure(self, sample_pod_spec):
        """Test to_dict produces correct Kubernetes-style structure."""
        pod = PodResource(
            name="my-pod",
            spec=sample_pod_spec,
            metadata={"labels": {"app": "test"}},
            namespace="production",
        )

        result = pod.to_dict()

        assert result["apiVersion"] == "v1"
        assert result["kind"] == "Pod"
        assert result["metadata"]["name"] == "my-pod"
        assert result["metadata"]["namespace"] == "production"
        assert result["metadata"]["labels"] == {"app": "test"}
        assert result["spec"] == sample_pod_spec

    def test_to_dict_json_serializable(self, sample_pod_spec):
        """Test that to_dict output is JSON serializable."""
        pod = PodResource(name="pod", spec=sample_pod_spec)

        # Should not raise
        json_str = json.dumps(pod.to_dict())
        assert json_str is not None


class TestPodResourceValidation:
    """Tests for PodResource edge cases and schema poisoning."""

    def test_empty_spec_allowed(self):
        """Test that empty spec is technically allowed (no validation)."""
        pod = PodResource(name="empty", spec={})
        assert pod.spec == {}

    def test_spec_with_extra_fields(self):
        """Test spec with unknown fields is preserved."""
        spec = {
            "containers": [{"name": "app", "image": "nginx"}],
            "volumes": [{"name": "data", "emptyDir": {}}],
            "custom_field": "custom_value",
        }
        pod = PodResource(name="pod", spec=spec)

        assert pod.spec["custom_field"] == "custom_value"
        assert pod.spec["volumes"] == [{"name": "data", "emptyDir": {}}]

    def test_deeply_nested_spec(self):
        """Test handling of deeply nested spec structures."""
        spec = {
            "containers": [
                {
                    "name": "app",
                    "image": "nginx",
                    "volumeMounts": [
                        {
                            "name": "config",
                            "mountPath": "/etc/config",
                            "subPath": "app.conf",
                        }
                    ],
                    "resources": {
                        "limits": {"cpu": "100m", "memory": "128Mi"},
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                    },
                }
            ]
        }
        pod = PodResource(name="nested", spec=spec)

        assert pod.first_container["resources"]["limits"]["cpu"] == "100m"


# =============================================================================
# SERVICE RESOURCE TESTS
# =============================================================================


class TestServiceResourceCreation:
    """Tests for ServiceResource instantiation."""

    def test_creates_service_with_minimal_spec(self, sample_service_spec):
        """Test creating a service with minimal required fields."""
        svc = ServiceResource(name="test-svc", spec=sample_service_spec)

        assert svc.name == "test-svc"
        assert svc.kind == "Service"
        assert svc.api_version == "v1"

    def test_creates_service_with_custom_namespace(self, sample_service_spec):
        """Test creating a service in custom namespace."""
        svc = ServiceResource(
            name="svc",
            spec=sample_service_spec,
            namespace="production",
        )

        assert svc.namespace == "production"


class TestServiceResourceProperties:
    """Tests for ServiceResource property accessors."""

    def test_selector_property(self):
        """Test selector property access."""
        svc = ServiceResource(
            name="svc",
            spec={"selector": {"app": "web", "tier": "frontend"}, "ports": []},
        )

        assert svc.selector == {"app": "web", "tier": "frontend"}

    def test_selector_empty_when_missing(self):
        """Test selector returns empty dict when not specified."""
        svc = ServiceResource(name="svc", spec={"ports": []})

        assert svc.selector == {}

    def test_ports_property(self):
        """Test ports property access."""
        svc = ServiceResource(
            name="svc",
            spec={
                "selector": {},
                "ports": [
                    {"port": 80, "targetPort": 8080},
                    {"port": 443, "targetPort": 8443},
                ],
            },
        )

        assert len(svc.ports) == 2
        assert svc.ports[0]["port"] == 80

    def test_ports_empty_when_missing(self):
        """Test ports returns empty list when not specified."""
        svc = ServiceResource(name="svc", spec={"selector": {}})

        assert svc.ports == []

    def test_service_type_default_clusterip(self):
        """Test service_type defaults to ClusterIP."""
        svc = ServiceResource(name="svc", spec={})

        assert svc.service_type == "ClusterIP"

    def test_service_type_custom(self):
        """Test service_type with custom value."""
        svc = ServiceResource(name="svc", spec={"type": "LoadBalancer"})

        assert svc.service_type == "LoadBalancer"


class TestServiceProxyPodSpec:
    """Tests for ServiceResource proxy_pod_spec generation."""

    def test_proxy_pod_spec_structure(self):
        """Test proxy_pod_spec generates correct structure."""
        svc = ServiceResource(
            name="my-service",
            spec={
                "selector": {"app": "web"},
                "ports": [{"port": 80, "targetPort": 8080}],
            },
        )

        spec = svc.proxy_pod_spec

        assert "containers" in spec
        assert len(spec["containers"]) == 1
        assert spec["containers"][0]["name"] == "nginx"
        assert spec["containers"][0]["image"] == "nginx:alpine"

    def test_proxy_pod_spec_network_aliases(self):
        """Test proxy_pod_spec includes service name as network alias."""
        svc = ServiceResource(
            name="web-service",
            spec={"selector": {}, "ports": [{"port": 3000}]},
        )

        spec = svc.proxy_pod_spec
        aliases = spec["containers"][0]["network_aliases"]

        assert "web-service" in aliases

    def test_proxy_pod_spec_ports(self):
        """Test proxy_pod_spec includes host ports."""
        svc = ServiceResource(
            name="svc",
            spec={
                "selector": {},
                "ports": [
                    {"port": 80},
                    {"port": 443},
                ],
            },
        )

        spec = svc.proxy_pod_spec
        ports = spec["containers"][0]["ports"]

        assert 80 in ports
        assert 443 in ports


# =============================================================================
# REPLICASET RESOURCE TESTS
# =============================================================================


class TestReplicaSetResourceCreation:
    """Tests for ReplicaSetResource instantiation."""

    def test_creates_replicaset_with_minimal_spec(self, sample_rs_spec):
        """Test creating a ReplicaSet with minimal required fields."""
        rs = ReplicaSetResource(name="test-rs", spec=sample_rs_spec)

        assert rs.name == "test-rs"
        assert rs.kind == "ReplicaSet"
        assert rs.api_version == "apps/v1"

    def test_creates_replicaset_with_custom_namespace(self, sample_rs_spec):
        """Test creating a ReplicaSet in custom namespace."""
        rs = ReplicaSetResource(
            name="rs",
            spec=sample_rs_spec,
            namespace="production",
        )

        assert rs.namespace == "production"


class TestReplicaSetResourceProperties:
    """Tests for ReplicaSetResource property accessors."""

    def test_replicas_property(self, sample_rs_spec):
        """Test replicas property access."""
        rs = ReplicaSetResource(name="rs", spec=sample_rs_spec)

        assert rs.replicas == 3

    def test_replicas_default_one(self):
        """Test replicas defaults to 1 when not specified."""
        rs = ReplicaSetResource(name="rs", spec={})

        assert rs.replicas == 1

    def test_replicas_zero(self):
        """Test replicas can be zero."""
        rs = ReplicaSetResource(name="rs", spec={"replicas": 0})

        assert rs.replicas == 0

    def test_selector_property(self, sample_rs_spec):
        """Test selector property access."""
        rs = ReplicaSetResource(name="rs", spec=sample_rs_spec)

        assert rs.selector == {"app": "web"}

    def test_selector_empty_when_missing(self):
        """Test selector returns empty dict when not specified."""
        rs = ReplicaSetResource(name="rs", spec={})

        assert rs.selector == {}

    def test_template_property(self, sample_rs_spec):
        """Test template property access."""
        rs = ReplicaSetResource(name="rs", spec=sample_rs_spec)

        template = rs.template
        assert "metadata" in template
        assert "spec" in template
        assert template["metadata"]["labels"] == {"app": "web"}

    def test_template_empty_when_missing(self):
        """Test template returns empty dict when not specified."""
        rs = ReplicaSetResource(name="rs", spec={})

        assert rs.template == {}


class TestReplicaSetResourceValidation:
    """Tests for ReplicaSet edge cases and schema poisoning."""

    def test_negative_replicas_stored_as_is(self):
        """Test that negative replicas are stored (no validation at model level)."""
        # Note: Validation should happen at API/controller level, not model
        rs = ReplicaSetResource(name="rs", spec={"replicas": -5})

        assert rs.replicas == -5

    def test_non_integer_replicas(self):
        """Test handling of non-integer replicas."""
        # The property just returns what's in the spec
        rs = ReplicaSetResource(name="rs", spec={"replicas": "three"})

        assert rs.replicas == "three"  # No validation at model level


# =============================================================================
# STORE CRUD TESTS
# =============================================================================


class TestStoreCRUD:
    """Tests for ResourceStore basic CRUD operations."""

    def test_create_pod(self, store, sample_pod_spec):
        """Test creating a pod in the store."""
        pod = PodResource(name="test-pod", spec=sample_pod_spec)

        result = store.create(pod)

        assert result.name == "test-pod"
        assert store.get("Pod", "test-pod", "default") is not None

    def test_create_duplicate_raises_error(self, store, sample_pod_spec):
        """Test that creating a duplicate resource raises ValueError."""
        pod = PodResource(name="dupe-pod", spec=sample_pod_spec)
        store.create(pod)

        with pytest.raises(ValueError, match="already exists"):
            store.create(pod)

    def test_get_existing_resource(self, store, sample_pod_spec):
        """Test getting an existing resource."""
        pod = PodResource(name="get-test", spec=sample_pod_spec)
        store.create(pod)

        result = store.get("Pod", "get-test", "default")

        assert result is not None
        assert result.name == "get-test"

    def test_get_nonexistent_returns_none(self, store):
        """Test getting a nonexistent resource returns None."""
        result = store.get("Pod", "nonexistent", "default")

        assert result is None

    def test_get_wrong_kind_returns_none(self, store, sample_pod_spec):
        """Test getting with wrong kind returns None."""
        pod = PodResource(name="pod", spec=sample_pod_spec)
        store.create(pod)

        result = store.get("Service", "pod", "default")

        assert result is None

    def test_list_resources_by_kind(self, store):
        """Test listing resources by kind."""
        for i in range(3):
            pod = PodResource(
                name=f"pod-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
            )
            store.create(pod)

        pods = store.list("Pod", "default")

        assert len(pods) == 3

    def test_list_filters_by_namespace(self, store):
        """Test that list filters by namespace when specified."""
        pod1 = PodResource(
            name="pod1",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            namespace="default",
        )
        pod2 = PodResource(
            name="pod2",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            namespace="production",
        )
        store.create(pod1)
        store.create(pod2)

        default_pods = store.list("Pod", "default")
        prod_pods = store.list("Pod", "production")

        assert len(default_pods) == 1
        assert len(prod_pods) == 1
        assert default_pods[0].name == "pod1"
        assert prod_pods[0].name == "pod2"

    def test_list_all_namespaces(self, store):
        """Test listing across all namespaces."""
        pod1 = PodResource(
            name="pod1",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            namespace="default",
        )
        pod2 = PodResource(
            name="pod2",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            namespace="production",
        )
        store.create(pod1)
        store.create(pod2)

        all_pods = store.list("Pod", namespace=None)

        assert len(all_pods) == 2

    def test_list_empty_kind(self, store):
        """Test listing a kind with no resources."""
        pods = store.list("Pod", "default")

        assert pods == []

    def test_update_existing_resource(self, store, sample_pod_spec):
        """Test updating an existing resource."""
        pod = PodResource(name="update-test", spec=sample_pod_spec)
        store.create(pod)

        updated_pod = PodResource(
            name="update-test",
            spec={"containers": [{"name": "new", "image": "alpine"}]},
        )
        result = store.update(updated_pod)

        assert result.spec["containers"][0]["image"] == "alpine"
        stored = store.get("Pod", "update-test", "default")
        assert stored.spec["containers"][0]["image"] == "alpine"

    def test_update_nonexistent_raises_error(self, store, sample_pod_spec):
        """Test that updating a nonexistent resource raises ValueError."""
        pod = PodResource(name="nonexistent", spec=sample_pod_spec)

        with pytest.raises(ValueError, match="does not exist"):
            store.update(pod)

    def test_delete_existing_resource(self, store, sample_pod_spec):
        """Test deleting an existing resource."""
        pod = PodResource(name="delete-test", spec=sample_pod_spec)
        store.create(pod)

        result = store.delete("Pod", "delete-test", "default")

        assert result is True
        assert store.get("Pod", "delete-test", "default") is None

    def test_delete_nonexistent_returns_false(self, store):
        """Test deleting a nonexistent resource returns False."""
        result = store.delete("Pod", "nonexistent", "default")

        assert result is False

    def test_create_or_update_creates_new(self, store, sample_pod_spec):
        """Test create_or_update creates when resource doesn't exist."""
        pod = PodResource(name="new-pod", spec=sample_pod_spec)

        result = store.create_or_update(pod)

        assert result.name == "new-pod"
        assert store.get("Pod", "new-pod", "default") is not None

    def test_create_or_update_updates_existing(self, store, sample_pod_spec):
        """Test create_or_update updates when resource exists."""
        pod = PodResource(name="existing", spec=sample_pod_spec)
        store.create(pod)

        updated = PodResource(
            name="existing",
            spec={"containers": [{"name": "updated", "image": "alpine"}]},
        )
        result = store.create_or_update(updated)

        assert result.spec["containers"][0]["name"] == "updated"


class TestStoreNamespaceIsolation:
    """Tests for store namespace isolation."""

    def test_same_name_different_namespaces(self, store, sample_pod_spec):
        """Test that same name in different namespaces are separate."""
        pod1 = PodResource(name="my-pod", spec=sample_pod_spec, namespace="ns1")
        pod2 = PodResource(name="my-pod", spec=sample_pod_spec, namespace="ns2")

        store.create(pod1)
        store.create(pod2)

        assert store.get("Pod", "my-pod", "ns1") is not None
        assert store.get("Pod", "my-pod", "ns2") is not None
        assert store.get("Pod", "my-pod", "ns1") != store.get("Pod", "my-pod", "ns2")

    def test_delete_only_affects_target_namespace(self, store, sample_pod_spec):
        """Test that delete only affects the target namespace."""
        pod1 = PodResource(name="my-pod", spec=sample_pod_spec, namespace="ns1")
        pod2 = PodResource(name="my-pod", spec=sample_pod_spec, namespace="ns2")
        store.create(pod1)
        store.create(pod2)

        store.delete("Pod", "my-pod", "ns1")

        assert store.get("Pod", "my-pod", "ns1") is None
        assert store.get("Pod", "my-pod", "ns2") is not None


class TestStoreThreadSafety:
    """Tests for store thread safety."""

    def test_concurrent_creates_no_data_loss(self, store):
        """Test that concurrent creates don't lose data."""
        num_pods = 50
        created = []
        errors = []

        def create_pod(index):
            try:
                pod = PodResource(
                    name=f"concurrent-pod-{index}",
                    spec={"containers": [{"name": "app", "image": "nginx"}]},
                )
                store.create(pod)
                created.append(index)
            except Exception as e:
                errors.append((index, e))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(create_pod, i) for i in range(num_pods)]
            for f in futures:
                f.result()

        assert len(errors) == 0
        assert len(store.list("Pod", "default")) == num_pods

    def test_concurrent_reads_safe(self, populated_store):
        """Test that concurrent reads are safe."""
        results = []
        errors = []

        def read_pod():
            try:
                pod = populated_store.get("Pod", "test-pod", "default")
                results.append(pod is not None)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(read_pod) for _ in range(100)]
            for f in futures:
                f.result()

        assert len(errors) == 0
        assert all(results)

    def test_concurrent_create_delete_safe(self, store):
        """Test concurrent create and delete operations are safe."""
        errors = []

        def create_and_delete(index):
            try:
                pod = PodResource(
                    name=f"volatile-{index}",
                    spec={"containers": [{"name": "app", "image": "nginx"}]},
                )
                store.create(pod)
                time.sleep(0.001)  # Small delay
                store.delete("Pod", f"volatile-{index}", "default")
            except ValueError:
                pass  # Expected if timing causes conflicts
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(create_and_delete, i) for i in range(30)]
            for f in futures:
                f.result()

        assert len(errors) == 0


# =============================================================================
# STRESS TESTS
# =============================================================================


@pytest.mark.stress
class TestStoreLargePayloads:
    """Stress tests for large resource payloads."""

    def test_pod_with_many_labels(self, store, large_labels_spec):
        """Test storing a pod with 100+ labels."""
        spec, metadata = large_labels_spec
        pod = PodResource(name="many-labels", spec=spec, metadata=metadata)

        store.create(pod)
        retrieved = store.get("Pod", "many-labels", "default")

        assert retrieved is not None
        assert len(retrieved.labels) == 150

    def test_pod_with_many_env_vars(self, store, large_env_spec):
        """Test storing a pod with 100+ environment variables."""
        pod = PodResource(name="many-env", spec=large_env_spec)

        store.create(pod)
        retrieved = store.get("Pod", "many-env", "default")

        assert retrieved is not None
        assert len(retrieved.env) == 150

    def test_many_resources_in_store(self, store):
        """Test storing hundreds of resources."""
        num_resources = 200

        for i in range(num_resources):
            pod = PodResource(
                name=f"bulk-pod-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
                metadata={"labels": {"index": str(i)}},
            )
            store.create(pod)

        pods = store.list("Pod", "default")
        assert len(pods) == num_resources

    def test_list_performance_many_resources(self, store):
        """Test that listing is reasonably fast with many resources."""
        num_resources = 100

        for i in range(num_resources):
            pod = PodResource(
                name=f"perf-pod-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
            )
            store.create(pod)

        start = time.time()
        pods = store.list("Pod", "default")
        elapsed = time.time() - start

        assert len(pods) == num_resources
        assert elapsed < 1.0  # Should complete in under 1 second


# =============================================================================
# SCHEMA POISONING TESTS
# =============================================================================


@pytest.mark.stress
class TestSchemaPoisoning:
    """Tests for handling malformed or malicious input."""

    def test_empty_container_name(self, store):
        """Test handling of empty container name."""
        pod = PodResource(
            name="empty-container-name",
            spec={"containers": [{"name": "", "image": "nginx"}]},
        )
        store.create(pod)

        retrieved = store.get("Pod", "empty-container-name", "default")
        assert retrieved.first_container["name"] == ""

    def test_missing_image_field(self, store):
        """Test handling of container without image field."""
        pod = PodResource(
            name="no-image",
            spec={"containers": [{"name": "app"}]},  # No image
        )
        store.create(pod)

        retrieved = store.get("Pod", "no-image", "default")
        assert retrieved.image is None

    def test_null_values_in_spec(self, store):
        """Test handling of null values in spec."""
        pod = PodResource(
            name="null-values",
            spec={
                "containers": [{"name": "app", "image": None}],
            },
        )
        store.create(pod)

        retrieved = store.get("Pod", "null-values", "default")
        assert retrieved.image is None

    def test_special_characters_in_name(self, store):
        """Test handling of special characters in resource names."""
        # These might be valid or invalid depending on policy
        names = ["pod-with-dash", "pod.with.dot", "pod_with_underscore"]

        for name in names:
            pod = PodResource(
                name=name,
                spec={"containers": [{"name": "app", "image": "nginx"}]},
            )
            store.create(pod)
            assert store.get("Pod", name, "default") is not None

    def test_very_long_name(self, store):
        """Test handling of very long resource names."""
        long_name = "a" * 253  # Kubernetes max name length

        pod = PodResource(
            name=long_name,
            spec={"containers": [{"name": "app", "image": "nginx"}]},
        )
        store.create(pod)

        retrieved = store.get("Pod", long_name, "default")
        assert retrieved is not None
        assert retrieved.name == long_name

    def test_unicode_in_labels(self, store):
        """Test handling of unicode characters in labels."""
        pod = PodResource(
            name="unicode-labels",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            metadata={"labels": {"greeting": "你好", "emoji": "🚀"}},
        )
        store.create(pod)

        retrieved = store.get("Pod", "unicode-labels", "default")
        assert retrieved.labels["greeting"] == "你好"
        assert retrieved.labels["emoji"] == "🚀"

    def test_deeply_nested_malformed_spec(self, store):
        """Test handling of deeply nested malformed structures."""
        spec = {
            "containers": [
                {
                    "name": "app",
                    "image": "nginx",
                    "nested": {"level1": {"level2": {"level3": {"data": [1, 2, 3]}}}},
                }
            ]
        }
        pod = PodResource(name="nested-spec", spec=spec)
        store.create(pod)

        retrieved = store.get("Pod", "nested-spec", "default")
        assert retrieved.spec["containers"][0]["nested"]["level1"]["level2"]["level3"][
            "data"
        ] == [1, 2, 3]
