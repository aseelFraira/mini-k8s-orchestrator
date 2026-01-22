"""
Container runtime for the orchestrator.
"""
import subprocess
from typing import List, Optional


class Container:
    """Represents a running container managed by Podman."""

    def __init__(self, pod=None, container_name=None, full_name=None):
        # Standard Case: Creating or Managing a Pod from the Store
        if pod and container_name:
            container_specs = pod.get_container_spec(container_name)

            if not container_specs:
                raise ValueError(f"Container {container_name} not found in pod {pod.name}")

            self.container_name = container_name
            self.namespace = pod.namespace
            self.pod_name = pod.name

            # The Podman ID
            self.full_name = f"{self.namespace}_{self.pod_name}_{container_name}"

            # The Spec
            self.image = container_specs.get("image")
            self.env = container_specs.get("env", {})
            self.labels = pod.metadata.get("labels", {})
            self.hash_number = pod.spec_hash
            self.network_aliases = container_specs.get("network_aliases", [])
            self.ports = container_specs.get("ports", [])

        # Cleanup Case: Creating a "Shadow" object from Podman data
        elif full_name:
            self.full_name = full_name
            self.pod = None
            self.container_name = None
            self.image = None
            self.env = {}
            self.labels = {}
            self.network_aliases = []
            self.ports = []

        else:
            raise ValueError("Must provide either (pod and container_name) or (full_name)")

        self.running = False
        

    def start(self):
        """Starts the container with full metadata and environment."""
        # Idempotency - Cleanup any existing container with this name first
        self.stop()

        # Import here to avoid circular dependency
        from cluster import ORCHESTRATOR_NETWORK

        cmd = [
            "podman",
            "run",
            "-d",
            "--name",
            self.full_name,
            "--network",
            ORCHESTRATOR_NETWORK,
        ]

        # Network aliases
        for alias in self.network_aliases:
            cmd.extend(["--network-alias", alias])

        # Pod name as alias for direct pod-to-pod communication
        cmd.extend(["--network-alias", self.pod_name])

        # Host port exposure (for Services)
        for port in self.ports:
            cmd.extend(["-p", f"{port}:{port}"])

        # Orchestrator Labels - for management
        cmd.extend(["--label", "managed-by=my-orchestrator"])
        cmd.extend(["--label", f"orchestrator-namespace={self.namespace}"])
        cmd.extend(["--label", f"orchestrator-pod-name={self.pod_name}"])
        cmd.extend(["--label", f"orchestrator-container-name={self.container_name}"])
        cmd.extend(["--label", f"pod-hash-number={self.hash_number}"])

        # Add Environment Variables
        for key, value in self.env.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Add User Labels
        for key, value in self.labels.items():
            cmd.extend(["--label", f"{key}={value}"])

        # Image and Execution
        if not self.image:
            raise ValueError("Cannot start container without image")
        cmd.append(self.image)

        print(f"Executing: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    def stop(self):
        cmd = ["podman", "rm", "-f", self.full_name]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                print(f"Successfully removed container: {self.full_name}")
        except Exception as e:
            print(f"Critical error during stop: {e}")

    def is_alive(self):
        """Check the ground truth via podman inspect"""
        cmd = ["podman", "inspect", "-f", "{{.State.Running}}", self.full_name]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def get_label(self, label_name: str):
        """Get a label value from the container."""
        cmd = [
            "podman", "inspect", 
            "-f", f'{{{{index .Config.Labels "{label_name}"}}}}', 
            self.full_name
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def get_ip_address(self) -> Optional[str]:
        """Get the IP address of the container on the orchestrator network"""
        cmd = [
            "podman",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            self.full_name,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        return None

    def exec_command(self, command: List[str]) -> bool:
        """Execute a command inside the container"""
        cmd = ["podman", "exec", self.full_name] + command
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Exec failed: {result.stderr}")
            return False
        return True
