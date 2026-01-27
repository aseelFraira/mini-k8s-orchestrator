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


# Pytest marker to skip tests when podman is not available
requires_podman = pytest.mark.skipif(
    not podman_available(), reason="Podman is not available"
)
