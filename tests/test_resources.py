"""
Unit Tests for Resource Models.
"""

import pytest
import json

from resources import PodResource, ServiceResource, ReplicaSetResource


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def sample_pod_spec():
    """Sample minimal pod spec for testing."""
    return {"containers": [{"name": "nginx", "image": "docker.io/library/nginx:alpine"}]}


@pytest.fixture
def sample_service_spec():
    """Sample minimal service spec for testing."""
    return {
        "selector": {"app": "web"},
        "ports": [{"port": 80, "targetPort": 8080}],
    }


@pytest.fixture
def sample_rs_spec():
    """Sample minimal ReplicaSet spec for testing."""
    return {
        "replicas": 3,
        "selector": {"app": "web"},
        "template": {
            "metadata": {"labels": {"app": "web"}},
            "spec": {"containers": [{"name": "nginx", "image": "nginx:alpine"}]},
        },
    }


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

        assert pod1.spec_hash == pod2.spec_hash
        assert len(pod1.spec_hash) == 64

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

    def test_containers_missing_raises_error(self):
        """Test that missing containers raises ValueError."""
        with pytest.raises(ValueError, match="container"):
            PodResource(name="empty-pod", spec={})

    def test_first_container_property(self):
        """Test accessing first container."""
        pod = PodResource(
            name="pod",
            spec={"containers": [{"name": "main", "image": "nginx"}]},
        )

        assert pod.first_container["name"] == "main"

    def test_empty_containers_list_raises_error(self):
        """Test that empty containers list raises ValueError."""
        with pytest.raises(ValueError, match="container"):
            PodResource(name="empty", spec={"containers": []})

    def test_image_property(self):
        """Test image property returns first container's image."""
        pod = PodResource(
            name="pod",
            spec={"containers": [{"name": "main", "image": "nginx:alpine"}]},
        )

        assert pod.image == "nginx:alpine"

    def test_container_without_name_raises_error(self):
        """Test that container without name raises ValueError."""
        with pytest.raises(ValueError, match="name"):
            PodResource(name="pod", spec={"containers": [{"image": "nginx"}]})

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

        json_str = json.dumps(pod.to_dict())
        assert json_str is not None


class TestPodResourceValidation:
    """Tests for PodResource validation errors."""

    def test_empty_spec_raises_error(self):
        """Test that empty spec (no containers) raises ValueError."""
        with pytest.raises(ValueError, match="container"):
            PodResource(name="empty", spec={})

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
            spec={
                "selector": {"app": "web", "tier": "frontend"},
                "ports": [{"port": 80}],
            },
        )

        assert svc.selector == {"app": "web", "tier": "frontend"}

    def test_selector_empty_allowed(self):
        """Test that empty selector is allowed (headless service)."""
        svc = ServiceResource(name="svc", spec={"ports": [{"port": 80}]})

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

    def test_ports_missing_raises_error(self):
        """Test that missing ports raises ValueError."""
        with pytest.raises(ValueError, match="ports"):
            ServiceResource(name="svc", spec={"selector": {}})

    def test_service_type_default_clusterip(self):
        """Test service_type defaults to ClusterIP."""
        svc = ServiceResource(
            name="svc",
            spec={"selector": {"app": "web"}, "ports": [{"port": 80}]},
        )

        assert svc.service_type == "ClusterIP"

    def test_service_type_custom(self):
        """Test service_type with custom value."""
        svc = ServiceResource(
            name="svc",
            spec={
                "selector": {"app": "web"},
                "ports": [{"port": 80}],
                "type": "LoadBalancer",
            },
        )

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

    def test_replicas_default_one(self, sample_rs_spec):
        """Test replicas defaults to 1 when not specified."""
        del sample_rs_spec["replicas"]
        rs = ReplicaSetResource(name="rs", spec=sample_rs_spec)

        assert rs.replicas == 1

    def test_replicas_zero(self, sample_rs_spec):
        """Test replicas can be zero."""
        sample_rs_spec["replicas"] = 0
        rs = ReplicaSetResource(name="rs", spec=sample_rs_spec)

        assert rs.replicas == 0

    def test_selector_property(self, sample_rs_spec):
        """Test selector property access."""
        rs = ReplicaSetResource(name="rs", spec=sample_rs_spec)

        assert rs.selector == {"app": "web"}

    def test_selector_missing_raises_error(self):
        """Test that missing selector raises ValueError."""
        with pytest.raises(ValueError, match="selector"):
            ReplicaSetResource(name="rs", spec={"replicas": 1, "template": {}})

    def test_template_property(self, sample_rs_spec):
        """Test template property access."""
        rs = ReplicaSetResource(name="rs", spec=sample_rs_spec)

        template = rs.template
        assert "metadata" in template
        assert "spec" in template
        assert template["metadata"]["labels"] == {"app": "web"}

    def test_template_missing_raises_error(self):
        """Test that missing template raises ValueError."""
        with pytest.raises(ValueError, match="template"):
            ReplicaSetResource(name="rs", spec={"replicas": 1, "selector": {}})


class TestReplicaSetResourceValidation:
    """Tests for ReplicaSet validation errors."""

    def test_negative_replicas_raises_error(self):
        """Test that negative replicas raises ValueError."""
        with pytest.raises(ValueError, match="replicas"):
            ReplicaSetResource(
                name="rs",
                spec={
                    "replicas": -5,
                    "selector": {"app": "test"},
                    "template": {"spec": {}},
                },
            )

    def test_non_integer_replicas_raises_error(self):
        """Test that non-integer replicas raises TypeError."""
        with pytest.raises((TypeError, ValueError)):
            ReplicaSetResource(
                name="rs",
                spec={
                    "replicas": "three",
                    "selector": {"app": "test"},
                    "template": {"spec": {}},
                },
            )
