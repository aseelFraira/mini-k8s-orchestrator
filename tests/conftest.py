"""
Common pytest fixtures and configuration for Mini-K8s orchestrator tests.
Provides isolation, mocking utilities, and sample resource factories.
"""

import pytest
import sys
import os
import shutil
import subprocess
from typing import Dict, Any, List
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from resources import PodResource, ServiceResource, ReplicaSetResource
from store import ResourceStore


# =============================================================================
# STORE FIXTURES
# =============================================================================


@pytest.fixture
def store() -> ResourceStore:
    """Provide a fresh, isolated ResourceStore for each test."""
    return ResourceStore()


@pytest.fixture
def populated_store(store) -> ResourceStore:
    """Store pre-populated with sample resources for testing."""
    # Add a pod
    pod = PodResource(
        name="test-pod",
        spec={"containers": [{"name": "app", "image": "nginx:latest"}]},
        metadata={"labels": {"app": "test"}},
        namespace="default",
    )
    store.create(pod)

    # Add a service
    svc = ServiceResource(
        name="test-service",
        spec={
            "selector": {"app": "test"},
            "ports": [{"port": 80, "targetPort": 8080}],
        },
        namespace="default",
    )
    store.create(svc)

    # Add a replicaset
    rs = ReplicaSetResource(
        name="test-rs",
        spec={
            "replicas": 3,
            "selector": {"app": "test"},
            "template": {
                "metadata": {"labels": {"app": "test"}},
                "spec": {"containers": [{"name": "app", "image": "nginx:latest"}]},
            },
        },
        namespace="default",
    )
    store.create(rs)

    return store


# =============================================================================
# SAMPLE RESOURCE SPEC FACTORIES
# =============================================================================


@pytest.fixture
def sample_pod_spec() -> Dict[str, Any]:
    """Minimal valid pod spec."""
    return {"containers": [{"name": "main", "image": "nginx:latest"}]}


@pytest.fixture
def sample_pod_spec_with_env() -> Dict[str, Any]:
    """Pod spec with environment variables."""
    return {
        "containers": [
            {
                "name": "app",
                "image": "python:3.11",
                "env": [
                    {"name": "DATABASE_URL", "value": "postgres://localhost:5432"},
                    {"name": "DEBUG", "value": "true"},
                ],
            }
        ]
    }


@pytest.fixture
def sample_service_spec() -> Dict[str, Any]:
    """Minimal valid service spec."""
    return {
        "selector": {"app": "web"},
        "ports": [{"port": 80, "targetPort": 8080}],
    }


@pytest.fixture
def sample_rs_spec() -> Dict[str, Any]:
    """Minimal valid ReplicaSet spec."""
    return {
        "replicas": 3,
        "selector": {"app": "web"},
        "template": {
            "metadata": {"labels": {"app": "web"}},
            "spec": {"containers": [{"name": "app", "image": "nginx:latest"}]},
        },
    }


# =============================================================================
# STRESS TEST FACTORIES
# =============================================================================


@pytest.fixture
def large_labels_spec() -> Dict[str, Any]:
    """Pod spec with 100+ labels for stress testing."""
    labels = {f"label-{i}": f"value-{i}" for i in range(150)}
    return {
        "containers": [{"name": "app", "image": "nginx:latest"}],
    }, {"labels": labels}


@pytest.fixture
def large_env_spec() -> Dict[str, Any]:
    """Pod spec with 100+ environment variables."""
    env_vars = [{"name": f"ENV_VAR_{i}", "value": f"value_{i}"} for i in range(150)]
    return {
        "containers": [
            {
                "name": "app",
                "image": "nginx:latest",
                "env": env_vars,
            }
        ]
    }


# =============================================================================
# MOCK PODMAN FIXTURES
# =============================================================================


class MockPodmanResult:
    """Mock subprocess result for Podman commands."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class MockPodmanProvider:
    """
    Mock Podman provider that tracks all container operations.
    Simulates the container runtime without actual Podman calls.
    """

    def __init__(self):
        self.containers: Dict[str, Dict[str, Any]] = {}
        self.call_log: List[Dict[str, Any]] = []
        self.should_fail_next: bool = False
        self.fail_on_container: str = None

    def run(self, cmd, **kwargs):
        """Mock subprocess.run for Podman commands."""
        self.call_log.append({"cmd": cmd, "kwargs": kwargs})

        if not cmd or cmd[0] != "podman":
            return MockPodmanResult(1, "", "Not a podman command")

        action = cmd[1] if len(cmd) > 1 else None

        # Handle failures
        if self.should_fail_next:
            self.should_fail_next = False
            raise subprocess.CalledProcessError(1, cmd)

        if action == "run":
            return self._handle_run(cmd)
        elif action == "rm":
            return self._handle_rm(cmd)
        elif action == "ps":
            return self._handle_ps(cmd)
        elif action == "inspect":
            return self._handle_inspect(cmd)
        elif action == "exec":
            return self._handle_exec(cmd)
        elif action == "network":
            return self._handle_network(cmd)

        return MockPodmanResult(0)

    def _handle_run(self, cmd):
        """Handle 'podman run' command."""
        # Parse container name from --name flag
        name = None
        labels = {}
        for i, arg in enumerate(cmd):
            if arg == "--name" and i + 1 < len(cmd):
                name = cmd[i + 1]
            if arg == "--label" and i + 1 < len(cmd):
                label_val = cmd[i + 1]
                if "=" in label_val:
                    k, v = label_val.split("=", 1)
                    labels[k] = v

        if name:
            if self.fail_on_container and name == self.fail_on_container:
                self.fail_on_container = None
                raise subprocess.CalledProcessError(1, cmd)

            self.containers[name] = {
                "name": name,
                "labels": labels,
                "running": True,
                "image": cmd[-1] if cmd else "unknown",
            }
        return MockPodmanResult(0, name or "")

    def _handle_rm(self, cmd):
        """Handle 'podman rm' command."""
        # Get container name (last argument)
        name = cmd[-1]
        if name in self.containers:
            del self.containers[name]
        return MockPodmanResult(0)

    def _handle_ps(self, cmd):
        """Handle 'podman ps' command."""
        # Check for format flag
        format_flag = None
        filter_labels = []

        for i, arg in enumerate(cmd):
            if arg == "--format" and i + 1 < len(cmd):
                format_flag = cmd[i + 1]
            if arg == "--filter" and i + 1 < len(cmd):
                filter_labels.append(cmd[i + 1])

        containers = list(self.containers.values())

        # Apply label filters
        for f in filter_labels:
            if f.startswith("label="):
                label_filter = f[6:]
                if "=" in label_filter:
                    k, v = label_filter.split("=", 1)
                    containers = [
                        c for c in containers if c.get("labels", {}).get(k) == v
                    ]
                else:
                    containers = [
                        c for c in containers if label_filter in c.get("labels", {})
                    ]

        if format_flag == "json":
            import json

            output = json.dumps(
                [
                    {
                        "Names": [c["name"]],
                        "Labels": c.get("labels", {}),
                        "State": "running" if c.get("running") else "stopped",
                    }
                    for c in containers
                ]
            )
            return MockPodmanResult(0, output)

        return MockPodmanResult(0, "\n".join(c["name"] for c in containers))

    def _handle_inspect(self, cmd):
        """Handle 'podman inspect' command."""
        name = cmd[-1]
        container = self.containers.get(name)

        if not container:
            return MockPodmanResult(1, "", f"Error: no such container: {name}")

        # Handle format flags
        for i, arg in enumerate(cmd):
            if arg == "-f" and i + 1 < len(cmd):
                fmt = cmd[i + 1]
                if "Running" in fmt:
                    return MockPodmanResult(
                        0, "true" if container.get("running") else "false"
                    )
                if "Labels" in fmt:
                    # Extract specific label
                    import re

                    match = re.search(r'"([^"]+)"', fmt)
                    if match:
                        label_name = match.group(1)
                        return MockPodmanResult(
                            0, container.get("labels", {}).get(label_name, "")
                        )
                if "IPAddress" in fmt:
                    return MockPodmanResult(0, "10.88.0.100")

        return MockPodmanResult(0, "{}")

    def _handle_exec(self, cmd):
        """Handle 'podman exec' command."""
        return MockPodmanResult(0)

    def _handle_network(self, cmd):
        """Handle 'podman network' commands."""
        return MockPodmanResult(0)

    def reset(self):
        """Reset all state."""
        self.containers.clear()
        self.call_log.clear()
        self.should_fail_next = False
        self.fail_on_container = None


@pytest.fixture
def mock_podman():
    """
    Fixture that patches subprocess.run to use MockPodmanProvider.
    Returns the mock provider for inspection and configuration.
    """
    provider = MockPodmanProvider()

    with patch("subprocess.run", side_effect=provider.run):
        yield provider


@pytest.fixture
def mock_container_subprocess():
    """
    Fixture specifically for Container class tests.
    Patches subprocess in the container module.
    """
    provider = MockPodmanProvider()

    with patch("container.subprocess.run", side_effect=provider.run):
        yield provider


# =============================================================================
# E2E UTILITIES
# =============================================================================


def is_podman_available() -> bool:
    """Check if Podman is available on the system."""
    try:
        result = subprocess.run(
            ["podman", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


# Skip marker for e2e tests when Podman is unavailable
skip_if_no_podman = pytest.mark.skipif(
    not is_podman_available(),
    reason="Podman is not available on this system",
)


@pytest.fixture
def e2e_cleanup():
    """
    Nuclear cleanup fixture for e2e tests.
    Tracks created containers and ensures cleanup even on test failure.
    """
    containers_to_cleanup: List[str] = []

    def register(container_name: str):
        """Register a container for cleanup."""
        containers_to_cleanup.append(container_name)

    yield register

    # Nuclear cleanup - force remove all registered containers
    for name in containers_to_cleanup:
        try:
            subprocess.run(
                ["podman", "rm", "-f", name],
                capture_output=True,
                check=False,
            )
        except Exception:
            pass  # Best effort cleanup


@pytest.fixture
def cleanup_orchestrator_containers():
    """
    Cleanup all containers managed by the orchestrator.
    Uses the 'managed-by=my-orchestrator' label.
    """
    yield

    # Post-test cleanup
    try:
        result = subprocess.run(
            [
                "podman",
                "ps",
                "-a",
                "--filter",
                "label=managed-by=my-orchestrator",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0 and result.stdout.strip():
            containers = result.stdout.strip().split("\n")
            for container in containers:
                if container:
                    subprocess.run(
                        ["podman", "rm", "-f", container],
                        capture_output=True,
                        check=False,
                    )
    except Exception:
        pass


# =============================================================================
# PYTEST CONFIGURATION
# =============================================================================


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring Podman")
    config.addinivalue_line("markers", "slow: slow running tests")
    config.addinivalue_line("markers", "stress: stress/load tests")
