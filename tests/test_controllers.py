"""
Unit Tests for Controllers (No Podman).

Tests ReplicaSet and Service controller logic at the store level.
For PodController tests with mocked Podman, see test_controllers_mock.py.
"""

from resources import PodResource, ReplicaSetResource, ServiceResource
from store import ResourceStore
from controllers import ReplicaSetController, ServiceController


# =============================================================================
# REPLICASET CONTROLLER TESTS
# =============================================================================


class TestReplicaSetControllerAddPods:
    """Tests for adding missing pods to reach desired replica count."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_creates_pods_when_none_exist(self):
        """Test that controller creates pods when replicas > 0 and no pods exist."""

        # Create a ReplicaSet requesting 3 replicas.
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

        # Reconcile should create 3 pods to match desired state.
        self.controller.reconcile()

        # Verify correct number of pods created.
        pods = self.store.list("Pod", "default")
        assert len(pods) == 3

        # Verify pods have correct labels and owner reference.
        for pod in pods:
            assert pod.labels.get("app") == "web"
            assert pod.metadata.get("owner-replicaset") == "web-rs"

    def test_creates_missing_pods_when_below_desired(self):
        """Test that controller creates only missing pods."""

        # RS wants 5 replicas.
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

        # Pre-create only 2 pods (3 missing).
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

        # Reconcile should create only the 3 missing pods.
        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        assert len(pods) == 5

    def test_fills_gaps_in_indices(self):
        """Test that controller fills gaps when some indices are missing."""

        # RS wants 3 replicas (indices 0, 1, 2).
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

        # Only create pod at index 1 (gaps at 0 and 2).
        pod = PodResource(
            name="gap-rs-1",
            spec={"containers": [{"name": "app", "image": "nginx"}]},
            metadata={"labels": {"app": "gap"}, "owner-replicaset": "gap-rs"},
            namespace="default",
        )
        self.store.create(pod)

        # Reconcile should fill gaps at indices 0 and 2.
        self.controller.reconcile()

        pods = self.store.list("Pod", "default")
        names = [p.name for p in pods]

        assert len(pods) == 3
        assert "gap-rs-0" in names
        assert "gap-rs-1" in names
        assert "gap-rs-2" in names


class TestReplicaSetControllerDeletePods:
    """Tests for deleting excess pods when above desired replica count."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_deletes_excess_pods_when_above_desired(self):
        """Test that controller deletes pods when count > desired replicas."""

        # RS only wants 2 replicas.
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

        # Create 5 pods (3 more than desired).
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
    """Tests for cleaning up pods whose owner ReplicaSet was deleted."""

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
    """Tests for handling replica count changes (scale up/down)."""

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
    """Tests that multiple reconciles with stable state are idempotent."""

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
# SERVICE CONTROLLER TESTS
# =============================================================================


class TestServiceControllerProxyPod:
    """Tests for creating nginx proxy pods for Services."""

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
    """Tests for cleaning up proxy pods whose Service was deleted."""

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
# MULTIPLE REPLICASETS TESTS
# =============================================================================


class TestMultipleReplicaSets:
    """Tests for multiple ReplicaSets managing pods independently."""

    def setup_method(self):
        """Create a fresh store and controller for each test."""
        self.store = ResourceStore()
        self.controller = ReplicaSetController(self.store)

    def test_multiple_replicasets_independent(self):
        """Test that multiple ReplicaSets manage their pods independently."""

        # Create two ReplicaSets with different selectors.
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

        # Single reconcile should create pods for both RS.
        self.controller.reconcile()

        # Verify total pod count.
        all_pods = self.store.list("Pod", "default")
        assert len(all_pods) == 5  # 2 + 3

        # Verify each RS manages its own pods independently.
        alpha_pods = [p for p in all_pods if p.labels.get("app") == "alpha"]
        beta_pods = [p for p in all_pods if p.labels.get("app") == "beta"]

        assert len(alpha_pods) == 2
        assert len(beta_pods) == 3

    def test_deleting_one_rs_doesnt_affect_other(self):
        """Test that deleting one RS doesn't affect another RS's pods."""

        # Create two ReplicaSets.
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
        assert len(self.store.list("Pod", "default")) == 4  # 2 + 2

        # Delete one RS - its pods should be cleaned up.
        self.store.delete("ReplicaSet", "rs-delete", "default")

        self.controller.reconcile()

        # Only rs-keep pods should remain.
        pods = self.store.list("Pod", "default")
        assert len(pods) == 2
        for pod in pods:
            assert pod.labels.get("app") == "keep"
