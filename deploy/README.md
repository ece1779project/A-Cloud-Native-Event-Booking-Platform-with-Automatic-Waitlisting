# Deployment runbook

The migration Job is intentionally excluded from the main Kustomizations.
Run it and wait for completion before applying API, WebSocket, and worker
Deployments.

The checked-in host name and registry in the DOKS overlay are placeholders.
Replace `events.example.com`, `registry.digitalocean.com/ticketbooking/app`, and
`registry.digitalocean.com/ticketbooking/frontend` before deployment.

## Required secrets

Create the namespace and one application Secret. Use URL-safe PostgreSQL
credentials because the password is embedded in connection URLs.

```sh
kubectl apply -f deploy/kubernetes/base/namespace.yaml

kubectl -n ticketbooking create secret generic ticketbooking-secrets \
  --from-literal=postgres-database="$POSTGRES_DB" \
  --from-literal=postgres-user="$POSTGRES_USER" \
  --from-literal=postgres-password="$POSTGRES_PASSWORD" \
  --from-literal=database-url="$DATABASE_URL" \
  --from-literal=postgres-exporter-dsn="$POSTGRES_EXPORTER_DSN" \
  --from-file=jwt-private-key="$JWT_PRIVATE_KEY_FILE" \
  --from-file=jwt-public-key="$JWT_PUBLIC_KEY_FILE" \
  --from-literal=grafana-admin-user="$GRAFANA_ADMIN_USER" \
  --from-literal=grafana-admin-password="$GRAFANA_ADMIN_PASSWORD"
```

The two URLs have different schemes:

```text
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@postgres:5432/DATABASE
POSTGRES_EXPORTER_DSN=postgresql://USER:PASSWORD@postgres:5432/DATABASE?sslmode=disable
```

Never commit a rendered Secret or real TLS/JWT key.

## Minikube

Build the application image inside minikube:

```sh
minikube start
eval "$(minikube docker-env)"
docker build -t ticketbooking-app:dev .
docker build -f frontend/Dockerfile -t ticketbooking-frontend:dev .
```

Start PostgreSQL and Redis before migration:

```sh
kubectl apply -n ticketbooking -f deploy/kubernetes/base/configmap.yaml
kubectl apply -n ticketbooking -f deploy/kubernetes/base/postgres.yaml
kubectl apply -n ticketbooking -f deploy/kubernetes/base/redis.yaml
kubectl -n ticketbooking rollout status statefulset/postgres --timeout=5m
kubectl -n ticketbooking rollout status statefulset/redis --timeout=5m
```

Run and verify the migration:

```sh
kubectl -n ticketbooking delete job ticketbooking-migrate --ignore-not-found
kubectl set image -f deploy/kubernetes/base/migration.yaml \
  migrate=ticketbooking-app:dev --local -o yaml |
  kubectl apply -n ticketbooking -f -
kubectl -n ticketbooking wait --for=condition=complete job/ticketbooking-migrate --timeout=5m
kubectl -n ticketbooking logs job/ticketbooking-migrate
```

Only then apply the workloads:

```sh
kubectl apply -k deploy/kubernetes/overlays/minikube
kubectl -n ticketbooking rollout status deployment/api --timeout=5m
kubectl -n ticketbooking rollout status deployment/websocket --timeout=5m
kubectl -n ticketbooking rollout status deployment/worker --timeout=5m
```

API, WebSocket, and frontend NodePorts are `30080`, `30081`, and `30082`.

## DOKS

Before applying:

1. Push the application and frontend images to DigitalOcean Container Registry.
2. Replace the image repository/tag and public host name in the DOKS overlay.
3. Create `ticketbooking-tls` in the `ticketbooking` namespace, or configure the
   approved certificate controller to create it.
4. Confirm that the cluster provides the `cilium` GatewayClass and
   `do-block-storage` StorageClass.

Apply PostgreSQL and Redis, wait, and then run the same migration sequence
using the immutable registry image:

```sh
kubectl apply -f deploy/kubernetes/base/namespace.yaml
kubectl apply -n ticketbooking -f deploy/kubernetes/base/configmap.yaml
kubectl apply -n ticketbooking -f deploy/kubernetes/base/postgres.yaml
kubectl apply -n ticketbooking -f deploy/kubernetes/base/redis.yaml
kubectl -n ticketbooking rollout status statefulset/postgres --timeout=5m
kubectl -n ticketbooking rollout status statefulset/redis --timeout=5m
kubectl -n ticketbooking delete job ticketbooking-migrate --ignore-not-found
kubectl set image -f deploy/kubernetes/base/migration.yaml \
  migrate="$APP_IMAGE" --local -o yaml |
  kubectl apply -n ticketbooking -f -
kubectl -n ticketbooking wait --for=condition=complete job/ticketbooking-migrate --timeout=5m
kubectl apply -k deploy/kubernetes/overlays/doks
```

Do not raise API or WebSocket HPA maxima until the PostgreSQL connection
budget has been recalculated.

## Validation

```sh
kubectl -n ticketbooking get pods,svc,pvc
kubectl -n ticketbooking get gateway,httproute,hpa,pdb,networkpolicy
kubectl -n ticketbooking port-forward service/prometheus 9090:9090
kubectl -n ticketbooking port-forward service/grafana 3000:3000
```

Prometheus should show all five jobs as up: `api`, `websocket`, `worker`,
`postgres`, and `redis`.
