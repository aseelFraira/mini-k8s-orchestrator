"""
Unit Tests for Controllers with Mocked Podman.

Tests PodController and controller coordination using a mocked Podman runtime.
No real containers are created - allows fast, isolated testing.
"""

import pytest
from unittest.mock import patch

from resources import PodResource, ReplicaSetResource, ServiceResource
from store import ResourceStore
from controllers import ReplicaSetController, ServiceController, PodController


# =============================================================================
# MOCK PODMAN RUNTIME
# =============================================================================


class MockPodmanRuntime:
    """Mock Podman runtime that intercepts subprocess.run calls."""

    def __init__(self):
        self.containers = {}  # name -> container_info dict
        self.call_log = []
        self.fail_on_container = None

    def handle_subprocess(self, cmd, **kwargs):
        """Handle a subprocess.run call, return a mock result."""
        self.call_log.append(cmd)
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd

        # podman ps -a --filter ... --format json
        if "podman" in cmd and "ps" in cmd and "json" in cmd:
            return self._handle_ps()

        # podman run -d --name ...
        if "podman" in cmd and "run" in cmd:
            return self._handle_run(cmd)

        # podman rm -f ...
        if "podman" in cmd and "rm" in cmd:
            return self._handle_rm(cmd)

        # podman inspect -f {{.State.Running}} ...
        if "podman" in cmd and "inspect" in cmd and "Running" in cmd_str:
            return self._handle_inspect_running(cmd)

        # podman inspect (other)
        if "podman" in cmd and "inspect" in cmd:
            return self._handle_inspect(cmd)

        # Default: return success
        return self._mock_result(0, "")

    def _handle_ps(self):
        """Return JSON list of containers."""
        import json
        container_list = []
        for name, info in self.containers.items():
            container_list.append({
                "Names": [name],
                "State": "running" if info.get("running", True) else "exited",
                "Labels": info.get("labels", {}),
            })
        return self._mock_result(0, json.dumps(container_list))

    def _handle_run(self, cmd):
        """Handle podman run command."""
        # Extract container name from --name flag
        name = None
        labels = {}
        for i, arg in enumerate(cmd):
            if arg == "--name" and i + 1 < len(cmd):
                name = cmd[i + 1]
            if arg == "--label" and i + 1 < len(cmd):
                label = cmd[i + 1]
                if "=" in label:
                    k, v = label.split("=", 1)
                    labels[k] = v

        if name:
            if self.fail_on_container and name == self.fail_on_container:
                return self._mock_result(1, "", "Simulated failure")

            self.containers[name] = {
                "name": name,
                "labels": labels,
                "running": True,
            }
        return self._mock_result(0, name or "")

    def _handle_rm(self, cmd):
        """Handle podman rm command."""
        # Get container name (last arg or after -f)
        name = cmd[-1] if cmd[-1] != "-f" else None
        if name and name in self.containers:
            del self.containers[name]
        return self._mock_result(0, "")

    def _handle_inspect_running(self, cmd):
        """Handle podman inspect for running state."""
        name = cmd[-1]
        if name in self.containers and self.containers[name].get("running"):
            return self._mock_result(0, "true")
        return self._mock_result(1, "false")

    def _handle_inspect(self, cmd):
        """Handle other podman inspect commands."""
        cmd_str = " ".join(cmd)
        name = cmd[-1]

        # Handle label inspection: podman inspect -f '{{index .Config.Labels "label_name"}}' container
        if ".Config.Labels" in cmd_str and name in self.containers:
            # Extract label name from format string
            for arg in cmd:
                if ".Config.Labels" in arg:
                    # Parse: {{index .Config.Labels "label_name"}}
                    import re
                    match = re.search(r'Labels\s+"([^"]+)"', arg)
                    if match:
                        label_name = match.group(1)
                        label_value = self.containers[name].get("labels", {}).get(label_name, "")
                        return self._mock_result(0, label_value)

        if name in self.containers:
            return self._mock_result(0, "10.88.0.5")  # Fake IP
        return self._mock_result(1, "")

    def _mock_result(self, returncode, stdout, stderr=""):
        """Create a mock subprocess result."""
        class MockResult:
            pass
        result = MockResult()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result


@pytest.fixture
def mock_podman():
    """
    Fixture that patches subprocess.run to use MockPodmanRuntime.
    Returns the mock runtime for assertions.
    """
    runtime = MockPodmanRuntime()

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd and cmd[0] == "podman":
            return runtime.handle_subprocess(cmd, **kwargs)
        # For non-podman commands, return a default success
        return runtime._mock_result(0, "")

    # Patch subprocess.run in both controllers and container modules.
    with patch("controllers.subprocess.run", side_effect=patched_run), \
         patch("container.subprocess.run", side_effect=patched_run):
        yield runtime


# =============================================================================
# POD CONTROLLER TESTS
# =============================================================================


class TestPodControllerReconcile:
    """Tests for container creation, deletion, and spec drift detection."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_creates_container_for_new_pod(self, mock_podman):
        """Test that PodController creates a container for a new pod."""
        controller = PodController(self.store)

        # Add a pod to the store (no container exists yet).
        pod = PodResource(
            name="new-pod",
            spec={"containers": [{"name": "main", "image": "nginx:latest"}]},
            namespace="default",
        )
        self.store.create(pod)

        # Reconcile should create the missing container.
        controller.reconcile()

        # Verify container was "created" in mock runtime.
        # Container name format: {namespace}_{pod-name}_{container-name}
        assert "default_new-pod_main" in mock_podman.containers

    def test_deletes_orphan_container(self, mock_podman):
        """Test that PodController deletes containers not in store."""
        controller = PodController(self.store)

        # Simulate an orphan container in runtime (no matching Pod in store).
        # This can happen if Pod was deleted but container cleanup failed.
        mock_podman.containers["default_orphan-pod_main"] = {
            "name": "default_orphan-pod_main",
            "labels": {
                "managed-by": "my-orchestrator",
                "orchestrator-namespace": "default",
                "orchestrator-pod-name": "orphan-pod",
            },
            "running": True,
        }

        # Reconcile should detect orphan and delete it.
        controller.reconcile()

        # Orphan should be removed from runtime.
        assert "default_orphan-pod_main" not in mock_podman.containers

    def test_recreates_container_on_spec_drift(self, mock_podman):
        """Test that PodController recreates container when spec hash changes."""
        controller = PodController(self.store)

        # Create pod in store with current spec.
        pod = PodResource(
            name="drift-pod",
            spec={"containers": [{"name": "main", "image": "nginx:v1"}]},
            namespace="default",
        )
        self.store.create(pod)

        # Simulate existing container with OLD hash (spec drift).
        # This happens when pod spec is updated but container wasn't recreated.
        mock_podman.containers["default_drift-pod_main"] = {
            "name": "default_drift-pod_main",
            "labels": {
                "managed-by": "my-orchestrator",
                "orchestrator-namespace": "default",
                "orchestrator-pod-name": "drift-pod",
                "pod-hash-number": "old-hash-value",  # Doesn't match current spec
            },
            "running": True,
        }

        # Reconcile should detect hash mismatch and recreate container.
        controller.reconcile()

        # Container should still exist (was recreated with new spec).
        assert "default_drift-pod_main" in mock_podman.containers


class TestPodControllerIdempotency:
    """Tests that synced state doesn't trigger unnecessary container ops."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_no_action_when_synced(self, mock_podman):
        """Test that no actions are taken when store and runtime are synced."""
        controller = PodController(self.store)

        # Create pod in store.
        pod = PodResource(
            name="synced-pod",
            spec={"containers": [{"name": "main", "image": "nginx"}]},
            namespace="default",
        )
        self.store.create(pod)

        # Initial reconcile creates container.
        controller.reconcile()

        # Simulate that container has correct hash (in sync with store).
        container_key = "default_synced-pod_main"
        if container_key in mock_podman.containers:
            mock_podman.containers[container_key]["labels"][
                "pod-hash-number"
            ] = pod.spec_hash

        # Count "podman run" commands before additional reconciles.
        run_commands_before = sum(1 for cmd in mock_podman.call_log if "run" in cmd)

        # Run 5 more reconciles - should be no-ops since state is synced.
        for _ in range(5):
            controller.reconcile()

        # Count "podman run" commands after.
        run_commands_after = sum(1 for cmd in mock_podman.call_log if "run" in cmd)

        # No new containers should be created (idempotent behavior).
        assert run_commands_after == run_commands_before, (
            f"Expected no new container creations, but found "
            f"{run_commands_after - run_commands_before} new 'run' commands"
        )


# =============================================================================
# CONTROLLER COORDINATION TESTS
# =============================================================================


class TestCascadingReconciliation:
    """Tests for RS creating pods that Service can select."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_rs_creates_pods_for_service(self, mock_podman):
        """Test RS creates pods that can be selected by Service."""
        rs_controller = ReplicaSetController(self.store)
        svc_controller = ServiceController(self.store, None)

        # Create ReplicaSet.
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

        # Create Service targeting the same label.
        svc = ServiceResource(
            name="web-svc",
            spec={
                "selector": {"app": "web"},
                "ports": [{"port": 80, "targetPort": 80}],
            },
            namespace="default",
        )
        self.store.create(svc)

        # RS reconcile creates pods.
        rs_controller.reconcile()

        # Verify pods exist with correct labels.
        pods = self.store.list("Pod", "default")
        web_pods = [p for p in pods if p.labels.get("app") == "web"]
        assert len(web_pods) == 2

        # Service reconcile creates proxy (will fail on actual container ops).
        try:
            svc_controller.reconcile()
        except Exception:
            pass

        # Verify proxy pod was created.
        proxy = self.store.get("Pod", "proxy-web-svc", "default")
        assert proxy is not None


class TestPartialReconcileFailure:
    """Tests for graceful handling when some operations fail."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_store_reflects_partial_state_on_failure(self, mock_podman):
        """Test that store correctly reflects partial state when some ops fail."""
        controller = PodController(self.store)

        # Create 3 pods in store.
        for i in range(3):
            pod = PodResource(
                name=f"partial-pod-{i}",
                spec={"containers": [{"name": "main", "image": "nginx"}]},
                namespace="default",
            )
            self.store.create(pod)

        # Configure mock to fail on the second container.
        # This simulates a partial failure scenario.
        mock_podman.fail_on_container = "default_partial-pod-1_main"

        # Reconcile - should handle partial failure gracefully.
        # Successful containers should still be created.
        try:
            controller.reconcile()
        except Exception:
            pass

        # At least some containers should exist (partial success).
        assert len(mock_podman.containers) >= 1


class TestControllerCoordination:
    """Tests for full reconciliation cycle across all controllers."""

    def setup_method(self):
        """Create a fresh store for each test."""
        self.store = ResourceStore()

    def test_full_reconciliation_cycle(self, mock_podman):
        """Test a full cycle: RS creates pods, Service creates proxy."""

        # Create all three controllers.
        rs_controller = ReplicaSetController(self.store)
        pod_controller = PodController(self.store)
        svc_controller = ServiceController(self.store, pod_controller)

        # Create a ReplicaSet and Service targeting the same app.
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

        # Run controllers in sequence (simulating the reconciliation loop).
        # 1. RS controller creates Pod resources in store.
        rs_controller.reconcile()

        # 2. Pod controller creates Podman containers for pods.
        pod_controller.reconcile()

        # 3. Service controller creates proxy pod + container.
        try:
            svc_controller.reconcile()
        except Exception:
            pass  # May fail on some operations in mock

        # Verify final state.
        pods = self.store.list("Pod", "default")
        assert len(pods) >= 2  # At least the RS pods

        # Check containers were created.
        app_containers = [c for c in mock_podman.containers.keys() if "app-rs" in c]
        assert len(app_containers) >= 1
