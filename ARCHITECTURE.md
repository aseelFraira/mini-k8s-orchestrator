# Mini Kubernetes Orchestrator - Architecture & Flow

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              REST API (main.py)                             │
│                         FastAPI on port 3000                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OrchestratorCluster (cluster.py)                    │
│  - Coordinates everything                                                   │
│  - Creates orchestrator-net network                                         │
│  - Starts/stops controllers                                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
           ┌───────────────┐  ┌──────────────┐  ┌─────────────┐
           │ PodController │  │ServiceControl│  │ReplicaSet   │
           │               │  │              │  │Controller   │
           └───────────────┘  └──────────────┘  └─────────────┘
                    │                 │                 │
                    └─────────────────┼─────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ResourceStore (store.py)                           │
│                    In-memory: Pod{}, Service{}, ReplicaSet{}                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Podman (containers)                              │
│                         Running on orchestrator-net                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Flow 1: Creating a Pod

```
User: POST /api/v1/namespaces/default/pods
      { "kind": "Pod", "metadata": {"name": "my-app"}, "spec": {...} }
```

### Step-by-Step:

```
1. REST API (main.py)
   │
   ├─► Validates kind == "Pod"
   ├─► Calls cluster.apply_resource(resource_dict)
   │
   ▼
2. OrchestratorCluster (cluster.py)
   │
   ├─► Creates PodResource object from dict
   ├─► Calls store.create_or_update(pod)
   │
   ▼
3. ResourceStore (store.py)
   │
   ├─► Stores: resources["Pod"]["default"]["my-app"] = pod
   ├─► Returns immediately (async reconciliation)
   │
   ▼
4. PodController.reconcile() [runs every 1 second]
   │
   ├─► Lists desired pods from store
   ├─► Lists actual containers from Podman (podman ps)
   ├─► Finds "my-app" missing from Podman
   ├─► Creates Container(pod=pod, container_name="...")
   ├─► Calls container.start()
   │
   ▼
5. Container.start() (container.py)
   │
   ├─► Runs: podman run -d --name default_my-app_container
   │         --network orchestrator-net
   │         --network-alias my-app        ← Pod name for DNS
   │         --label managed-by=my-orchestrator
   │         --label pod-hash-number=abc123
   │         nginx:alpine
   │
   ▼
6. Container is now running and reachable at "my-app" from other pods
```

---

## Flow 2: Creating a ReplicaSet

```
User: POST /api/apps/v1/namespaces/default/replicasets
      { "kind": "ReplicaSet", "spec": {"replicas": 3, "selector": {"app": "web"}, ...} }
```

### Step-by-Step:

```
1. REST API → Cluster → Store
   │
   ├─► ReplicaSet "my-rs" stored in store
   │
   ▼
2. ReplicaSetController.reconcile() [every 1 second]
   │
   ├─► Lists ReplicaSets from store
   ├─► For "my-rs": desired=3, current=0
   ├─► Creates 3 PodResources in store:
   │     - my-rs-0 (labels: {app: web})
   │     - my-rs-1 (labels: {app: web})
   │     - my-rs-2 (labels: {app: web})
   │
   ▼
3. PodController.reconcile() [next cycle]
   │
   ├─► Finds 3 new pods in store, 0 in Podman
   ├─► Starts 3 containers:
   │     - default_my-rs-0_web (--network-alias my-rs-0)
   │     - default_my-rs-1_web (--network-alias my-rs-1)
   │     - default_my-rs-2_web (--network-alias my-rs-2)
   │
   ▼
4. All 3 pods running, reachable by name
```

### Scaling Up/Down:

```
PUT /api/apps/v1/namespaces/default/replicasets/my-rs
    { "spec": {"replicas": 5} }
    
ReplicaSetController:
  ├─► desired=5, current=3
  ├─► Creates my-rs-3, my-rs-4 in store
  
PodController:
  ├─► Starts 2 new containers
```

---

## Flow 3: Creating a Service

```
User: POST /api/v1/namespaces/default/services
      { "kind": "Service", "spec": {"selector": {"app": "web"}, "ports": [{"port": 80, "targetPort": 8080}]} }
```

### Step-by-Step:

```
1. REST API → Cluster → Store
   │
   ├─► Service "my-svc" stored in store
   │
   ▼
2. ServiceController.reconcile() [every 1 second]
   │
   ├─► Finds Service "my-svc" with no proxy pod
   ├─► Creates proxy pod in store:
   │     name: "proxy-my-svc"
   │     spec: {
   │       containers: [{
   │         name: "nginx",
   │         image: "nginx:alpine",
   │         network_aliases: ["web"],  ← From selector value
   │         ports: [80]                ← For host exposure
   │       }]
   │     }
   │
   ▼
3. PodController.reconcile() [next cycle]
   │
   ├─► Finds "proxy-my-svc" in store, not in Podman
   ├─► Starts container:
   │     podman run -d --name default_proxy-my-svc_nginx
   │       --network orchestrator-net
   │       --network-alias web          ← Service reachable as "web"
   │       --network-alias proxy-my-svc
   │       -p 80:80                      ← Host can access localhost:80
   │       nginx:alpine
   │
   ▼
4. ServiceController.reconcile() [next cycle]
   │
   ├─► Proxy container now running
   ├─► Finds backend pods matching selector {app: web}
   ├─► Gets their IPs: [10.89.0.5, 10.89.0.6, 10.89.0.7]
   ├─► Generates nginx config:
   │
   │     upstream my-svc_backend {
   │         server 10.89.0.5:8080;
   │         server 10.89.0.6:8080;
   │         server 10.89.0.7:8080;
   │     }
   │     server {
   │         listen 80;
   │         location / {
   │             proxy_pass http://my-svc_backend;
   │         }
   │     }
   │
   ├─► Injects config into proxy container
   ├─► Runs: nginx -s reload
   │
   ▼
5. Service is now:
   - Reachable from pods as: curl http://web:80
   - Reachable from host as: curl http://localhost:80
   - Load balances to backend pods
```

---

## Flow 4: Pod-to-Pod Communication

```
Pod A wants to reach Pod B ("my-app")
```

```
┌─────────────┐                           ┌─────────────┐
│   Pod A     │                           │   Pod B     │
│             │  curl http://my-app:8080  │             │
│             │ ─────────────────────────►│  (my-app)   │
│             │                           │             │
└─────────────┘                           └─────────────┘
      │                                          │
      └──────────── orchestrator-net ────────────┘
                         │
                    DNS: "my-app" → 10.89.0.5
```

**How it works:**
1. Pod B started with `--network-alias my-app`
2. Podman's DNS (aardvark-dns) registers this alias
3. Pod A queries DNS for "my-app"
4. DNS returns Pod B's IP
5. Connection established

---

## Flow 5: Reconciliation Loop (Self-Healing)

Every 1 second, each controller runs reconcile():

### PodController.reconcile()

```
1. Get DESIRED state (from store)
2. Get ACTUAL state (from podman ps)
3. Compare and fix:
   - Orphaned containers? → Stop them
   - Missing containers? → Start them
   - Stopped containers? → Restart them
   - Spec changed (hash)? → Recreate them
```

### ReplicaSetController.reconcile()

```
1. For each ReplicaSet:
   - Count pods matching selector
   - Too few? → Create pods in store
   - Too many? → Delete pods from store
2. PodController will handle actual containers
```

### ServiceController.reconcile()

```
1. Ensure proxy pod exists in store
2. Find backend pods by selector
3. Update nginx config with backend IPs
4. Reload nginx
```

---

## Data Flow Summary

```
                    USER REQUEST
                         │
                         ▼
┌────────────────────────────────────────────┐
│              REST API (main.py)            │
│         POST/GET/PUT/DELETE endpoints      │
└────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────┐
│         OrchestratorCluster (cluster.py)   │
│            apply_resource()                │
└────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────┐
│           ResourceStore (store.py)         │
│     Thread-safe in-memory storage          │
│     Pod{}, Service{}, ReplicaSet{}         │
└────────────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     ┌──────────┐   ┌──────────┐   ┌───────────┐
     │   Pod    │   │ Service  │   │ReplicaSet │
     │Controller│   │Controller│   │Controller │
     └──────────┘   └──────────┘   └───────────┘
          │              │              │
          │              │              │
          ▼              │              │
┌────────────────────────┴────────────────────┐
│         Container (container.py)            │
│         Podman CLI wrapper                  │
└─────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────┐
│                   PODMAN                   │
│            orchestrator-net                │
│     ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐     │
│     │pod-1│  │pod-2│  │pod-3│  │proxy│     │
│     └─────┘  └─────┘  └─────┘  └─────┘     │
└────────────────────────────────────────────┘
```

---

## Key Concepts

| Concept | Implementation |
|---------|----------------|
| **Desired State** | ResourceStore (what user wants) |
| **Actual State** | Podman containers (what's running) |
| **Reconciliation** | Controllers compare & fix differences |
| **Self-Healing** | Loop runs every 1 second |
| **Service Discovery** | DNS via `--network-alias` |
| **Load Balancing** | Nginx proxy with upstream servers |
| **Spec Changes** | Detected via hash, triggers recreation |

---

## File Structure

```
src/
├── main.py          # FastAPI REST API entry point
├── cluster.py       # OrchestratorCluster - coordinates everything
├── store.py         # ResourceStore - thread-safe in-memory storage
├── resources.py     # Resource definitions (Pod, Service, ReplicaSet)
├── controllers.py   # Reconciliation controllers
├── container.py     # Podman container wrapper
└── api.py           # (Legacy) Internal API helpers
```
