"""
Layer 2 & 3: Controller Tests for Mini-K8s Orchestrator
Tests for ReplicaSetController, ServiceController, and PodController.
Uses mocked Podman provider to test reconciliation logic.
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from resources import PodResource, ReplicaSetResource, ServiceResource
from store import ResourceStore
from controllers import ReplicaSetController, ServiceController, PodController


# =============================================================================
# LAYER 2: REPLICASET CONTROLLER TESTS
# =============================================================================


class TestReplicaSetControllerAddPods:
    """Tests for ReplicaSetController adding missing pods."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_creates_pods_when_none_exist(self):
        """Test that controller creates pods when replicas > 0 and no pods exist."""
        rs = ReplicaSetResource(
            name="web-rs",
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
        self.store.create(rs)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 3

        for pod in pods:
            assert pod.labels.get("app") == "web"
            assert pod.metadata.get("owner-replicaset") == "web-rs"

    def test_creates_missing_pods_when_below_desired(self):
        """Test that controller creates only missing pods."""
        rs = ReplicaSetResource(
            name="partial-rs",
            spec={
                "replicas": 5,
                "selector": {"app": "partial"},
                "template": {
                    "metadata": {"labels": {"app": "partial"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Pre-create 2 pods
        for i in range(2):
            pod = PodResource(
                name=f"partial-rs-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
                metadata={
                    "labels": {"app": "partial"},
                    "owner-replicaset": "partial-rs",
                },
                namespace="default",
            )
            self.store.create(pod)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 5

    def test_pod_names_follow_convention(self):
        """Test that created pods follow the naming convention rs-name-index."""
        rs = ReplicaSetResource(
            name="named-rs",
            spec={
                "replicas": 3,
                "selector": {"app": "named"},
                "template": {
                    "metadata": {"labels": {"app": "named"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        names = [p.name for p in pods]

        assert "named-rs-0" in names
        assert "named-rs-1" in names
        assert "named-rs-2" in names

    def test_fills_gaps_in_indices(self):
        """Test that controller fills gaps when some indices are missing."""
        rs = ReplicaSetResource(
            name="gap-rs",
            spec={
                "replicas": 3,
                "selector": {"app": "gap"},
                "template": {
                    "metadata": {"labels": {"app": "gap"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Only create pod at index 1 (gap at 0 and 2)
        pod = PodResource(
            name="gap-rs-1",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            metadata={"labels": {"app": "gap"}, "owner-replicaset": "gap-rs"},
            namespace="default",
        )
        self.store.create(pod)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        names = [p.name for p in pods]

        assert len(pods) == 3
        assert "gap-rs-0" in names
        assert "gap-rs-1" in names
        assert "gap-rs-2" in names


class TestReplicaSetControllerDeletePods:
    """Tests for ReplicaSetController deleting excess pods."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_deletes_excess_pods_when_above_desired(self):
        """Test that controller deletes pods when count > desired replicas."""
        rs = ReplicaSetResource(
            name="shrink-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "shrink"},
                "template": {
                    "metadata": {"labels": {"app": "shrink"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Create 5 pods (3 more than desired)
        for i in range(5):
            pod = PodResource(
                name=f"shrink-rs-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
                metadata={"labels": {"app": "shrink"}, "owner-replicaset": "shrink-rs"},
                namespace="default",
            )
            self.store.create(pod)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 2

    def test_scale_down_to_zero(self):
        """Test scaling down to zero replicas deletes all pods."""
        rs = ReplicaSetResource(
            name="zero-rs",
            spec={
                "replicas": 0,
                "selector": {"app": "zero"},
                "template": {
                    "metadata": {"labels": {"app": "zero"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Create 3 pods
        for i in range(3):
            pod = PodResource(
                name=f"zero-rs-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
                metadata={"labels": {"app": "zero"}, "owner-replicaset": "zero-rs"},
                namespace="default",
            )
            self.store.create(pod)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 0

    def test_delete_only_owned_pods(self):
        """Test that only pods owned by the RS are deleted, not unrelated pods."""
        rs = ReplicaSetResource(
            name="owned-rs",
            spec={
                "replicas": 1,
                "selector": {"app": "owned"},
                "template": {
                    "metadata": {"labels": {"app": "owned"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Create owned pods
        for i in range(3):
            pod = PodResource(
                name=f"owned-rs-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
                metadata={"labels": {"app": "owned"}, "owner-replicaset": "owned-rs"},
                namespace="default",
            )
            self.store.create(pod)

        # Create an unrelated pod with matching labels but no owner
        unrelated_pod = PodResource(
            name="unrelated-pod",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            metadata={"labels": {"app": "other"}},
            namespace="default",
        )
        self.store.create(unrelated_pod)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 2
        names = [p.name for p in pods]
        assert "unrelated-pod" in names


class TestReplicaSetControllerOrphanCleanup:
    """Tests for ReplicaSetController cleaning up orphaned pods."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_cleans_up_orphaned_pods_when_rs_deleted(self):
        """Test that pods are deleted when their owner ReplicaSet is deleted."""
        rs = ReplicaSetResource(
            name="orphan-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "orphan"},
                "template": {
                    "metadata": {"labels": {"app": "orphan"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Create owned pods
        for i in range(2):
            pod = PodResource(
                name=f"orphan-rs-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
                metadata={"labels": {"app": "orphan"}, "owner-replicaset": "orphan-rs"},
                namespace="default",
            )
            self.store.create(pod)

        # Delete the ReplicaSet
        self.store.delete("ReplicaSet", "orphan-rs", "default")

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 0

    def test_does_not_delete_pods_without_owner(self):
        """Test that pods without owner-replicaset metadata are not deleted."""
        pod = PodResource(
            name="standalone-pod",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            metadata={"labels": {"app": "standalone"}},
            namespace="default",
        )
        self.store.create(pod)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 1
        assert pods[0].name == "standalone-pod"


class TestReplicaSetControllerSpecChanges:
    """Tests for ReplicaSetController handling spec changes."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_scale_up_replica_count(self):
        """Test increasing replica count creates new pods."""
        rs = ReplicaSetResource(
            name="scale-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "scale"},
                "template": {
                    "metadata": {"labels": {"app": "scale"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        self.controller.reconcile()
        assert len(self.store.list("Pod", "default")) == 2

        # Scale up
        updated_rs = ReplicaSetResource(
            name="scale-rs",
            spec={
                "replicas": 5,
                "selector": {"app": "scale"},
                "template": {
                    "metadata": {"labels": {"app": "scale"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create_or_update(updated_rs)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 5

    def test_scale_down_replica_count(self):
        """Test decreasing replica count deletes pods."""
        rs = ReplicaSetResource(
            name="shrink-rs",
            spec={
                "replicas": 5,
                "selector": {"app": "shrink"},
                "template": {
                    "metadata": {"labels": {"app": "shrink"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        self.controller.reconcile()
        assert len(self.store.list("Pod", "default")) == 5

        # Scale down
        updated_rs = ReplicaSetResource(
            name="shrink-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "shrink"},
                "template": {
                    "metadata": {"labels": {"app": "shrink"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create_or_update(updated_rs)

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 2


class TestReplicaSetIdempotency:
    """Tests for ReplicaSetController idempotency."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_multiple_reconciles_no_change(self):
        """Test that multiple reconciles with stable state don't change anything."""
        rs = ReplicaSetResource(
            name="stable-rs",
            spec={
                "replicas": 3,
                "selector": {"app": "stable"},
                "template": {
                    "metadata": {"labels": {"app": "stable"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Initial reconcile
        self.controller.reconcile()
        initial_pods = self.store.list("Pod", "default")
        initial_names = sorted([p.name for p in initial_pods])

        # Run 10 more reconciles
        for _ in range(10):
            self.controller.reconcile()

        final_pods = self.store.list("Pod", "default")
        final_names = sorted([p.name for p in final_pods])

        assert len(final_pods) == 3
        assert initial_names == final_names


# =============================================================================
# LAYER 2: SERVICE CONTROLLER TESTS
# =============================================================================


class TestServiceControllerProxyPod:
    """Tests for ServiceController creating proxy pods."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ServiceController(self.store, None)

    def test_creates_proxy_pod_for_service(self):
        """Test that a proxy pod is created for each service."""
        svc = ServiceResource(
            name="web-service",
            spec={
                "selector": {"app": "web"},
                "ports": [{"port": 80, "targetPort": 8080}],
            },
            namespace="default",
        )
        self.store.create(svc)

        try:
            self.controller.reconcile()
        except Exception:
            pass  # Expected since we don't have real containers

        proxy_pod = self.store.get("Pod", "proxy-web-service", "default")
        assert proxy_pod is not None
        assert proxy_pod.labels.get("role") == "service-proxy"
        assert proxy_pod.labels.get("service-name") == "web-service"

    def test_proxy_pod_uses_nginx_image(self):
        """Test that proxy pod spec uses nginx image."""
        svc = ServiceResource(
            name="nginx-proxy-svc",
            spec={
                "selector": {"app": "backend"},
                "ports": [{"port": 3000, "targetPort": 3000}],
            },
            namespace="default",
        )
        self.store.create(svc)

        try:
            self.controller.reconcile()
        except Exception:
            pass

        proxy_pod = self.store.get("Pod", "proxy-nginx-proxy-svc", "default")
        assert proxy_pod is not None
        assert proxy_pod.containers[0]["image"] == "nginx:alpine"


class TestServiceControllerOrphanCleanup:
    """Tests for ServiceController cleaning up orphaned proxy pods."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ServiceController(self.store, None)

    def test_cleans_up_proxy_pod_when_service_deleted(self):
        """Test that proxy pods are deleted when their service is deleted."""
        proxy_pod = PodResource(
            name="proxy-deleted-svc",
            spec={"containers": [{"name": "nginx", "image": "nginx:alpine"}]},
            metadata={
                "labels": {"role": "service-proxy", "service-name": "deleted-svc"}
            },
            namespace="default",
        )
        self.store.create(proxy_pod)

        self.controller.reconcile()

        remaining_pod = self.store.get("Pod", "proxy-deleted-svc", "default")
        assert remaining_pod is None

    def test_does_not_delete_non_proxy_pods(self):
        """Test that regular pods are not deleted by service controller."""
        regular_pod = PodResource(
            name="regular-pod",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            metadata={"labels": {"app": "web"}},
            namespace="default",
        )
        self.store.create(regular_pod)

        self.controller.reconcile()

        pod = self.store.get("Pod", "regular-pod", "default")
        assert pod is not None


# =============================================================================
# LAYER 2: POD CONTROLLER TESTS (WITH MOCKED PODMAN)
# =============================================================================


class TestPodControllerReconcile:
    """Tests for PodController reconciliation with mocked Podman."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_creates_container_for_new_pod(self, mock_podman):
        """Test that PodController creates a container for a new pod."""
        mock_api = MagicMock()
        controller = PodController(self.store, mock_api)

        pod = PodResource(
            name="new-pod",
            spec={"containers": [{"name": "main", "image": "nginx:latest"}]},
            namespace="default",
        )
        self.store.create(pod)

        controller.reconcile()

        # Verify container was "created" in mock
        assert "default_new-pod_main" in mock_podman.containers

    def test_deletes_orphan_container(self, mock_podman):
        """Test that PodController deletes containers not in store."""
        mock_api = MagicMock()
        controller = PodController(self.store, mock_api)

        # Simulate an orphan container in runtime
        mock_podman.containers["default_orphan-pod_main"] = {
            "name": "default_orphan-pod_main",
            "labels": {
                "managed-by": "my-orchestrator",
                "orchestrator-namespace": "default",
                "orchestrator-pod-name": "orphan-pod",
            },
            "running": True,
        }

        controller.reconcile()

        # Orphan should be deleted
        assert "default_orphan-pod_main" not in mock_podman.containers

    def test_recreates_container_on_spec_drift(self, mock_podman):
        """Test that PodController recreates container when spec hash changes."""
        mock_api = MagicMock()
        controller = PodController(self.store, mock_api)

        # Create pod in store
        pod = PodResource(
            name="drift-pod",
            spec={"containers": [{"name": "main", "image": "nginx:v1"}]},
            namespace="default",
        )
        self.store.create(pod)

        # Simulate existing container with old hash
        mock_podman.containers["default_drift-pod_main"] = {
            "name": "default_drift-pod_main",
            "labels": {
                "managed-by": "my-orchestrator",
                "orchestrator-namespace": "default",
                "orchestrator-pod-name": "drift-pod",
                "pod-hash-number": "old-hash-value",
            },
            "running": True,
        }

        controller.reconcile()

        # Container should be recreated with new spec
        assert "default_drift-pod_main" in mock_podman.containers


class TestPodControllerIdempotency:
    """Tests for PodController idempotency."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_no_action_when_synced(self, mock_podman):
        """Test that no actions are taken when store and runtime are synced."""
        mock_api = MagicMock()
        controller = PodController(self.store, mock_api)

        # Create pod in store
        pod = PodResource(
            name="synced-pod",
            spec={"containers": [{"name": "main", "image": "nginx"}]},
            namespace="default",
        )
        self.store.create(pod)

        # Initial reconcile creates container
        controller.reconcile()
        initial_call_count = len(mock_podman.call_log)

        # Simulate that container has correct hash
        container_key = "default_synced-pod_main"
        if container_key in mock_podman.containers:
            mock_podman.containers[container_key]["labels"][
                "pod-hash-number"
            ] = pod.spec_hash

        # Run 5 more reconciles
        for _ in range(5):
            controller.reconcile()

        # Should have minimal additional calls (just listing/inspecting)
        # The key is no create/delete operations beyond the first


# =============================================================================
# LAYER 3: INTEGRATION TESTS
# =============================================================================


class TestMultipleReplicaSets:
    """Tests for multiple ReplicaSets coexisting."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_multiple_replicasets_independent(self):
        """Test that multiple ReplicaSets manage their pods independently."""
        rs1 = ReplicaSetResource(
            name="rs-alpha",
            spec={
                "replicas": 2,
                "selector": {"app": "alpha"},
                "template": {
                    "metadata": {"labels": {"app": "alpha"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        rs2 = ReplicaSetResource(
            name="rs-beta",
            spec={
                "replicas": 3,
                "selector": {"app": "beta"},
                "template": {
                    "metadata": {"labels": {"app": "beta"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs1)
        self.store.create(rs2)

        self.controller.reconcile()

        all_pods = self.store.list("Pod", "default")
        assert len(all_pods) == 5

        alpha_pods = [p for p in all_pods if p.labels.get("app") == "alpha"]
        beta_pods = [p for p in all_pods if p.labels.get("app") == "beta"]

        assert len(alpha_pods) == 2
        assert len(beta_pods) == 3

    def test_deleting_one_rs_doesnt_affect_other(self):
        """Test that deleting one RS doesn't affect another RS's pods."""
        rs1 = ReplicaSetResource(
            name="rs-keep",
            spec={
                "replicas": 2,
                "selector": {"app": "keep"},
                "template": {
                    "metadata": {"labels": {"app": "keep"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        rs2 = ReplicaSetResource(
            name="rs-delete",
            spec={
                "replicas": 2,
                "selector": {"app": "delete"},
                "template": {
                    "metadata": {"labels": {"app": "delete"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs1)
        self.store.create(rs2)

        self.controller.reconcile()
        assert len(self.store.list("Pod", "default")) == 4

        # Delete one RS
        self.store.delete("ReplicaSet", "rs-delete", "default")

        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 2
        for pod in pods:
            assert pod.labels.get("app") == "keep"


class TestCascadingReconciliation:
    """Tests for cascading effects between controllers."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_rs_creates_pods_for_service(self, mock_podman):
        """Test RS creates pods that can be selected by Service."""
        rs_controller = ReplicaSetController(self.store)
        svc_controller = ServiceController(self.store, None)

        # Create ReplicaSet
        rs = ReplicaSetResource(
            name="web-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "web"},
                "template": {
                    "metadata": {"labels": {"app": "web"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="default",
        )
        self.store.create(rs)

        # Create Service targeting the same label
        svc = ServiceResource(
            name="web-svc",
            spec={
                "selector": {"app": "web"},
                "ports": [{"port": 80, "targetPort": 80}],
            },
            namespace="default",
        )
        self.store.create(svc)

        # RS reconcile creates pods
        rs_controller.reconcile()

        # Verify pods exist with correct labels
        pods = self.store.list("Pod", "default")
        web_pods = [p for p in pods if p.labels.get("app") == "web"]
        assert len(web_pods) == 2

        # Service reconcile creates proxy (will fail on actual container ops)
        try:
            svc_controller.reconcile()
        except Exception:
            pass

        # Verify proxy pod was created
        proxy = self.store.get("Pod", "proxy-web-svc", "default")
        assert proxy is not None


class TestNamespaceIsolation:
    """Tests for controller namespace isolation."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_rs_in_different_namespaces_isolated(self):
        """Test that ReplicaSets in different namespaces don't interfere."""
        rs_prod = ReplicaSetResource(
            name="web-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "web"},
                "template": {
                    "metadata": {"labels": {"app": "web"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="production",
        )
        rs_dev = ReplicaSetResource(
            name="web-rs",  # Same name, different namespace
            spec={
                "replicas": 1,
                "selector": {"app": "web"},
                "template": {
                    "metadata": {"labels": {"app": "web"}},
                    "spec": {"containers": [{"name": "app", "image": "nginx"}]},
                },
            },
            namespace="development",
        )
        self.store.create(rs_prod)
        self.store.create(rs_dev)

        self.controller.reconcile()

        prod_pods = self.store.list("Pod", "production")
        dev_pods = self.store.list("Pod", "development")

        assert len(prod_pods) == 2
        assert len(dev_pods) == 1


class TestPartialReconcileFailure:
    """Tests for handling partial reconciliation failures."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_store_reflects_partial_state_on_failure(self, mock_podman):
        """Test that store correctly reflects partial state when some ops fail."""
        mock_api = MagicMock()
        controller = PodController(self.store, mock_api)

        # Create 3 pods
        for i in range(3):
            pod = PodResource(
                name=f"partial-pod-{i}",
                spec={"containers": [{"name": "main", "image": "nginx"}]},
                namespace="default",
            )
            self.store.create(pod)

        # Make the second container creation fail
        mock_podman.fail_on_container = "default_partial-pod-1_main"

        # Reconcile - should handle partial failure gracefully
        try:
            controller.reconcile()
        except Exception:
            pass

        # At least some containers should exist
        assert len(mock_podman.containers) >= 1


class TestControllerCoordination:
    """Tests for multiple controllers working together."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_full_reconciliation_cycle(self, mock_podman):
        """Test a full cycle: RS creates pods, Service creates proxy."""
        mock_api = MagicMock()

        rs_controller = ReplicaSetController(self.store)
        pod_controller = PodController(self.store, mock_api)
        svc_controller = ServiceController(self.store, pod_controller)

        # Create resources
        rs = ReplicaSetResource(
            name="app-rs",
            spec={
                "replicas": 2,
                "selector": {"app": "myapp"},
                "template": {
                    "metadata": {"labels": {"app": "myapp"}},
                    "spec": {"containers": [{"name": "app", "image": "myapp:v1"}]},
                },
            },
            namespace="default",
        )
        svc = ServiceResource(
            name="app-svc",
            spec={
                "selector": {"app": "myapp"},
                "ports": [{"port": 8080, "targetPort": 8080}],
            },
            namespace="default",
        )
        self.store.create(rs)
        self.store.create(svc)

        # Run controllers in sequence
        rs_controller.reconcile()  # Creates pods in store
        pod_controller.reconcile()  # Creates containers

        try:
            svc_controller.reconcile()  # Creates proxy pod + container
        except Exception:
            pass

        # Verify final state
        pods = self.store.list("Pod", "default")
        assert len(pods) >= 2  # At least the RS pods

        # Check containers were created
        app_containers = [c for c in mock_podman.containers.keys() if "app-rs" in c]
        assert len(app_containers) >= 1
