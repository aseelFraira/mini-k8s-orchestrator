"""
Shared test utilities for the Mini-K8s Orchestrator.
"""

import pytest
import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Timing constants for waiting on container operations
WAIT_POD_READY = 8
WAIT_MULTI_POD = 12
WAIT_CLEANUP = 6


def podman_available():
    """Check if podman is available on the system."""
    try:
        result = subprocess.run(["podman", "--version"], capture_output=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def cleanup_test_containers():
    """Remove all test containers and orphaned port forwarding processes."""
    subprocess.run(["pkill", "-f", "containers-rootlessport"], capture_output=True)

    result = subprocess.run(
        ["podman", "ps", "-a", "--filter", "label=managed-by=my-orchestrator", "-q"],
        capture_output=True,
        text=True,
    )
    container_ids = [cid for cid in result.stdout.strip().split("\n") if cid]
    for cid in container_ids:
        subprocess.run(["podman", "rm", "-f", cid], capture_output=True)


# =============================================================================
# PODMAN HELPER FUNCTIONS
# =============================================================================


def podman_container_exists(name: str) -> bool:
    """Check if a container exists by name."""
    result = subprocess.run(
        ["podman", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    return name in result.stdout


def podman_get_label(container: str, label: str) -> str:
    """Get a label value from a container."""
    result = subprocess.run(
        ["podman", "inspect", "-f", f'{{{{index .Config.Labels "{label}"}}}}', container],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def podman_is_running(container: str) -> bool:
    """Check if a container is running."""
    result = subprocess.run(
        ["podman", "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "true"


def podman_get_id(container: str) -> str:
    """Get container ID."""
    result = subprocess.run(
        ["podman", "inspect", "-f", "{{.Id}}", container],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def podman_list_containers(label_filter: str = "managed-by=my-orchestrator") -> list:
    """List container names with a label filter."""
    result = subprocess.run(
        ["podman", "ps", "--filter", f"label={label_filter}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    return [c for c in result.stdout.strip().split("\n") if c]


def podman_get_network(container: str) -> str:
    """Get the network(s) a container is on."""
    result = subprocess.run(
        ["podman", "inspect", container, "--format",
         "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def podman_stop(container: str):
    """Stop a container."""
    subprocess.run(["podman", "stop", container], capture_output=True)


def podman_exec(container: str, command: list) -> tuple:
    """Execute a command in a container. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["podman", "exec", container] + command,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


# Pytest marker to skip tests when podman is not available
requires_podman = pytest.mark.skipif(
    not podman_available(), reason="Podman is not available"
)
