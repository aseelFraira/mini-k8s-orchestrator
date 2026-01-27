# Test Suite Overview

Total: **109 tests** across 6 test files.

---

## test_controllers.py (17 tests)
Unit tests for ReplicaSet and Service controllers (no Podman).

### TestReplicaSetControllerAddPods
- `test_creates_pods_when_none_exist` — RS with 3 replicas creates 3 pods from scratch
- `test_creates_missing_pods_when_below_desired` — RS with 3 replicas but only 1 pod creates 2 more
- `test_fills_gaps_in_indices` — RS recreates pods at missing indices (e.g., 0,2 exist → creates 1)

### TestReplicaSetControllerDeletePods
- `test_deletes_excess_pods_when_above_desired` — RS with 2 replicas but 4 pods deletes 2
- `test_scale_down_to_zero` — RS scaled to 0 replicas deletes all owned pods
- `test_delete_only_owned_pods` — RS only deletes pods it owns, leaves others untouched

### TestReplicaSetControllerOrphanCleanup
- `test_cleans_up_orphaned_pods_when_rs_deleted` — Deleting RS removes all its owned pods
- `test_does_not_delete_pods_without_owner` — Orphan cleanup ignores pods with no ownerRef

### TestReplicaSetControllerSpecChanges
- `test_scale_up_replica_count` — Updating RS from 2→4 replicas creates 2 new pods
- `test_scale_down_replica_count` — Updating RS from 4→2 replicas deletes 2 pods

### TestReplicaSetIdempotency
- `test_multiple_reconciles_no_change` — Running reconcile twice with no changes produces same state

### TestServiceControllerProxyPod
- `test_creates_proxy_pod_for_service` — Service creates a proxy pod named `{svc-name}-proxy`
- `test_proxy_pod_uses_nginx_image` — Proxy pod uses nginx:alpine image for load balancing

### TestServiceControllerOrphanCleanup
- `test_cleans_up_proxy_pod_when_service_deleted` — Deleting service removes its proxy pod
- `test_does_not_delete_non_proxy_pods` — Service cleanup only removes proxy pods, not backend pods

### TestMultipleReplicaSets
- `test_multiple_replicasets_independent` — Two RS with same selector create pods independently
- `test_deleting_one_rs_doesnt_affect_other` — Deleting one RS doesn't touch other RS's pods

---

## test_podman.py (12 tests)
Podman integration tests (requires real Podman).

### TestContainerLifecycle
- `test_pod_creates_podman_container` — Creating a pod spawns a real Podman container
- `test_delete_pod_removes_podman_container` — Deleting a pod stops and removes the container

### TestContainerLabels
- `test_container_has_managed_by_label` — Container has `managed-by=mini-orchestrator` label
- `test_container_has_pod_name_label` — Container has `pod-name={name}` label for tracking

### TestContainerNetworking
- `test_container_on_orchestrator_network` — Container joins the `orchestrator-network` bridge

### TestReconciliation
- `test_stopped_container_is_restarted` — Controller restarts a manually stopped container
- `test_orphaned_container_is_removed` — Container without matching pod is cleaned up

### TestSpecChanges
- `test_env_change_triggers_recreation` — Changing pod env vars recreates the container
- `test_image_change_triggers_recreation` — Changing pod image recreates the container

### TestReplicaSetPods
- `test_replicaset_creates_multiple_pods` — RS with 3 replicas creates 3 Podman containers
- `test_replicaset_scale_creates_new_pods` — Scaling RS up creates additional containers

### TestServiceProxy
- `test_service_creates_proxy_container` — Service creates an nginx proxy container

---

## test_integration.py (10 tests)
Integration tests for API/store consistency and controller coordination.

### TestStoreAPIConsistency
- `test_api_list_reflects_store_state` — GET /pods returns all pods from store
- `test_api_get_returns_updated_status` — GET /pods/{name} returns current status after update
- `test_api_delete_removes_from_store` — DELETE /pods/{name} removes pod from store

### TestReplicaSetControllerIntegration
- `test_replicaset_pod_naming_convention` — RS pods follow `{rs-name}-{index}` naming
- `test_replicaset_owner_references` — RS pods have correct ownerReferences metadata

### TestServiceControllerIntegration
- `test_service_tracks_ready_backends` — Service status shows count of ready backend pods
- `test_service_updates_backends_on_scale` — Scaling RS updates service's readyBackends count

### TestNamespaceIsolation
- `test_same_name_different_namespaces` — Same pod name allowed in different namespaces
- `test_delete_only_affects_target_namespace` — Delete in ns-a doesn't affect ns-b
- `test_service_only_selects_same_namespace_pods` — Service only routes to pods in same namespace

---

## test_e2e.py (13 tests)
End-to-end tests with real Podman containers.

### TestE2EPod
- `test_pod_becomes_running` — Pod status transitions to "Running" after reconciliation
- `test_pod_container_status_ready` — Pod containerStatuses shows ready=true
- `test_pod_with_environment_variables` — Env vars from spec are set inside container
- `test_pod_labels_preserved` — Pod metadata.labels are queryable via API
- `test_delete_pod_stops_container` — DELETE removes pod and stops Podman container
- `test_pods_can_reach_each_other_by_name` — Pod A can ping Pod B using DNS name
- `test_pod_can_reach_service_by_name` — Pod can curl service by its DNS name

### TestE2EReplicaSet
- `test_replicaset_creates_running_pods` — RS creates N running pods with containers
- `test_replicaset_scale_up` — PATCH to increase replicas creates new pods
- `test_replicaset_scale_down` — PATCH to decrease replicas removes excess pods
- `test_delete_replicaset_removes_pods` — DELETE RS cascades to all owned pods
- `test_replicaset_self_healing` — Manually deleting a pod triggers RS to recreate it

### TestE2EService
- `test_service_accessible_on_host` — Service port is reachable from host machine

---

## test_store.py (13 tests)
Unit tests for ResourceStore.

### TestStoreCRUD
- `test_create_and_get` — Create stores resource, get retrieves it
- `test_create_duplicate_raises_error` — Creating same name twice raises ValueError
- `test_get_nonexistent_returns_none` — Getting unknown name returns None
- `test_list_empty` — List on empty store returns empty list
- `test_list_returns_all` — List returns all stored resources
- `test_update_existing` — Update modifies stored resource in place
- `test_update_nonexistent_raises_error` — Updating unknown name raises KeyError
- `test_delete_existing` — Delete removes resource, returns True
- `test_delete_nonexistent_returns_false` — Deleting unknown name returns False
- `test_create_or_update_creates` — Upsert creates if not exists
- `test_create_or_update_updates` — Upsert updates if exists

### TestThreadSafety
- `test_concurrent_creates` — 100 parallel creates don't corrupt store
- `test_concurrent_reads_and_writes` — Mixed read/write from threads is safe

---

## test_resources.py (44 tests)
Unit tests for resource models (Pod, Service, ReplicaSet).

### TestPodResourceCreation
- `test_creates_pod_with_minimal_spec` — Pod created with just name and containers
- `test_creates_pod_with_custom_namespace` — Pod respects namespace parameter
- `test_creates_pod_with_metadata` — Pod stores labels and annotations
- `test_generates_spec_hash` — Pod computes SHA256 hash of spec for drift detection
- `test_different_specs_produce_different_hashes` — Changed spec = different hash

### TestPodResourceProperties
- `test_containers_property` — `.containers` returns spec.containers list
- `test_containers_missing_raises_error` — Missing containers key raises ValueError
- `test_first_container_property` — `.first_container` returns containers[0]
- `test_empty_containers_list_raises_error` — Empty containers list raises ValueError
- `test_image_property` — `.image` returns first container's image
- `test_container_without_name_raises_error` — Container without name raises ValueError
- `test_env_property_with_list_format` — `.env` parses [{name,value}] format
- `test_env_property_with_dict_format` — `.env` parses {key:value} dict format
- `test_env_empty_dict_when_missing` — Missing env returns empty dict
- `test_labels_property` — `.labels` returns metadata.labels
- `test_labels_empty_when_no_metadata` — Missing metadata returns empty labels
- `test_get_container_spec_found` — `get_container_spec(name)` finds container by name
- `test_get_container_spec_not_found` — `get_container_spec(unknown)` returns None

### TestPodResourceSerialization
- `test_to_dict_structure` — `.to_dict()` returns proper K8s-like structure
- `test_to_dict_json_serializable` — `.to_dict()` output is JSON serializable

### TestPodResourceValidation
- `test_empty_spec_raises_error` — Empty spec dict raises ValueError
- `test_spec_with_extra_fields` — Unknown fields are preserved (no strict schema)

### TestServiceResourceCreation
- `test_creates_service_with_minimal_spec` — Service created with selector and ports
- `test_creates_service_with_custom_namespace` — Service respects namespace parameter

### TestServiceResourceProperties
- `test_selector_property` — `.selector` returns spec.selector dict
- `test_selector_empty_allowed` — Empty selector is valid (selects nothing)
- `test_ports_property` — `.ports` returns spec.ports list
- `test_ports_missing_raises_error` — Missing ports key raises ValueError
- `test_service_type_default_clusterip` — Default type is "ClusterIP"
- `test_service_type_custom` — Custom type (NodePort, LoadBalancer) is stored

### TestServiceProxyPodSpec
- `test_proxy_pod_spec_structure` — `.proxy_pod_spec()` returns valid pod spec
- `test_proxy_pod_spec_network_aliases` — Proxy has service name as network alias
- `test_proxy_pod_spec_ports` — Proxy exposes correct port mappings

### TestReplicaSetResourceCreation
- `test_creates_replicaset_with_minimal_spec` — RS created with selector and template
- `test_creates_replicaset_with_custom_namespace` — RS respects namespace parameter

### TestReplicaSetResourceProperties
- `test_replicas_property` — `.replicas` returns spec.replicas count
- `test_replicas_default_one` — Missing replicas defaults to 1
- `test_replicas_zero` — Zero replicas is valid (scale to 0)
- `test_selector_property` — `.selector` returns spec.selector.matchLabels
- `test_selector_missing_raises_error` — Missing selector raises ValueError
- `test_template_property` — `.template` returns spec.template pod template
- `test_template_missing_raises_error` — Missing template raises ValueError

### TestReplicaSetResourceValidation
- `test_negative_replicas_raises_error` — Negative replicas raises ValueError
- `test_non_integer_replicas_raises_error` — Non-integer replicas raises TypeError

---

## Running Tests

```bash
# Run all tests
uv run pytest

# Run specific file
uv run pytest tests/test_controllers.py

# Run with verbose output
uv run pytest -v

# Run only unit tests (no Podman)
uv run pytest tests/test_controllers.py tests/test_store.py tests/test_resources.py

# Run Podman tests (requires Podman)
uv run pytest tests/test_podman.py tests/test_e2e.py tests/test_integration.py
```
