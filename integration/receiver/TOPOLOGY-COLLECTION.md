# Read-only topology collection profile

`topology_watch.WatchStore` accepts core/v1 Nodes and resource.k8s.io/v1 ResourceSlices and namespaced ResourceClaims. The DRA normalization profile remains Kubernetes 1.34.0. API type acceptance is not a claim of conformance to every server/driver version. Qualification uses a local TLS peer plus recorded fixtures; no customer cluster was contacted.

`topology_collect.collect` uses an explicitly supplied TLS origin, CA and bearer credential. It issues GET only, follows no redirects and uses no ambient kubeconfig/proxy. The subprocess deadline includes DNS. Responses are capped at 8 MiB; list collections at 10,000 objects; watch batches at 1,024 frames. Paginated lists refuse until a deployment uses a supported bounded collection. A deployment must provision read/list/watch permissions only for its collections and scope. Namespace names alone are not immutable tenant identity; provision the collector and scope under a trusted operator.

A full list establishes an opaque resource version. Ordered watch batches commit atomically. Stream failure, expired resource versions, queue overflow, UID mismatch and competing collectors invalidate the projection. Restart requires relist. Resource versions are never parsed or numerically ordered. Ordering is supplied by the single TLS stream. New collection attempts with changed TLS origin/CA require relist. No health, capacity or execution permission is inferred from a connection.

SQLite FULL synchronous transactions retain the last committed projection. This is a local persistent cache, not an HA service. Trusted owner-controlled storage is required; an attacker able to replace its database can falsify observations. The in-memory source session prevents a stale collector from advancing a newer session. A conflicting collector can force resynchronization; run one owner per scoped collection.

The trusted collection host timestamps reads with its UTC clock and refuses responses that arrive after the configured validity interval. Observations cannot authorize execution. A candidate invalidates when a relevant snapshot changes or expires; uncertain submitted work requires reconciliation without retry. Topology snapshots cannot replace the executor's independent grant, target, namespace, object and attempt checks.

Reference semantics: https://kubernetes.io/docs/reference/using-api/api-concepts/ (opaque resource versions; watch loss and 410 relist). Exact provider/server qualification remains in the release support matrix.
