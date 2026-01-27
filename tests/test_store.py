"""
Unit Tests for ResourceStore.
"""

import pytest
import threading
from concurrent.futures import ThreadPoolExecutor

from store import ResourceStore
from resources import PodResource


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def store():
    """Fresh ResourceStore for each test."""
    return ResourceStore()


@pytest.fixture
def sample_pod():
    """Sample pod resource."""
    return PodResource(
        name="test-pod",
        spec={"containers": [{"name": "nginx", "image": "nginx:alpine"}]},
    )


# =============================================================================
# CRUD OPERATIONS
# =============================================================================


class TestStoreCRUD:
    """Tests for basic CRUD operations."""

    def test_create_and_get(self, store, sample_pod):
        """Test creating and retrieving a resource."""
        store.create(sample_pod)

        result = store.get("Pod", "test-pod", "default")
        assert result is not None
        assert result.name == "test-pod"

    def test_create_duplicate_raises_error(self, store, sample_pod):
        """Test that creating duplicate resource raises ValueError."""
        store.create(sample_pod)

        with pytest.raises(ValueError, match="already exists"):
            store.create(sample_pod)

    def test_get_nonexistent_returns_none(self, store):
        """Test that getting nonexistent resource returns None."""
        result = store.get("Pod", "nonexistent", "default")
        assert result is None

    def test_list_empty(self, store):
        """Test listing when no resources exist."""
        result = store.list("Pod", "default")
        assert result == []

    def test_list_returns_all(self, store):
        """Test listing returns all resources of a kind."""
        for i in range(3):
            pod = PodResource(
                name=f"pod-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
            )
            store.create(pod)

        result = store.list("Pod", "default")
        assert len(result) == 3

    def test_update_existing(self, store, sample_pod):
        """Test updating an existing resource."""
        store.create(sample_pod)

        updated_pod = PodResource(
            name="test-pod",
            spec={"containers": [{"name": "updated", "image": "alpine"}]},
        )
        store.update(updated_pod)

        result = store.get("Pod", "test-pod", "default")
        assert result.spec["containers"][0]["name"] == "updated"

    def test_update_nonexistent_raises_error(self, store, sample_pod):
        """Test that updating nonexistent resource raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            store.update(sample_pod)

    def test_delete_existing(self, store, sample_pod):
        """Test deleting an existing resource."""
        store.create(sample_pod)

        result = store.delete("Pod", "test-pod", "default")
        assert result is True
        assert store.get("Pod", "test-pod", "default") is None

    def test_delete_nonexistent_returns_false(self, store):
        """Test that deleting nonexistent resource returns False."""
        result = store.delete("Pod", "nonexistent", "default")
        assert result is False

    def test_create_or_update_creates(self, store, sample_pod):
        """Test create_or_update creates when resource doesn't exist."""
        store.create_or_update(sample_pod)

        result = store.get("Pod", "test-pod", "default")
        assert result is not None

    def test_create_or_update_updates(self, store, sample_pod):
        """Test create_or_update updates when resource exists."""
        store.create(sample_pod)

        updated_pod = PodResource(
            name="test-pod",
            spec={"containers": [{"name": "updated", "image": "alpine"}]},
        )
        store.create_or_update(updated_pod)

        result = store.get("Pod", "test-pod", "default")
        assert result.spec["containers"][0]["name"] == "updated"


# =============================================================================
# THREAD SAFETY
# =============================================================================


class TestThreadSafety:
    """Tests for concurrent access safety."""

    def test_concurrent_creates(self, store):
        """Test concurrent creates don't corrupt store."""

        def create_pod(i):
            pod = PodResource(
                name=f"pod-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
            )
            store.create(pod)

        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(create_pod, range(100))

        result = store.list("Pod", "default")
        assert len(result) == 100

    def test_concurrent_reads_and_writes(self, store):
        """Test concurrent reads and writes don't cause errors."""
        # Pre-populate some data
        for i in range(10):
            pod = PodResource(
                name=f"pod-{i}",
                spec={"containers": [{"name": "app", "image": "nginx"}]},
            )
            store.create(pod)

        errors = []

        def reader():
            try:
                for _ in range(50):
                    store.list("Pod", "default")
                    store.get("Pod", "pod-5", "default")
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(10, 30):
                    pod = PodResource(
                        name=f"pod-{i}",
                        spec={"containers": [{"name": "app", "image": "nginx"}]},
                    )
                    store.create_or_update(pod)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(5)] + [
            threading.Thread(target=writer) for _ in range(3)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent access: {errors}"
