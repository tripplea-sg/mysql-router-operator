# MySQL Router Operator

Author: Hananto Wicaksono

License: GNU General Public License v3.0 or later

This workspace contains a small Kopf operator that deploys MySQL Router inside
Kubernetes for an InnoDB Cluster running outside Kubernetes.

## What It Watches

The operator runs in the `mysql-router` namespace and watches `MySQLRouter`
custom resources:

```yaml
apiVersion: mysql.oracle.com/v1alpha1
kind: MySQLRouter
spec:
  innodbCluster:
    nodes:
      - ip: <innodb_cluster_node_ip>
        port: <mysql_port>
```
Example:
```yaml
apiVersion: mysql.oracle.com/v1alpha1
kind: MySQLRouter
spec:
  innodbCluster:
    nodes:
      - ip: 10.0.10.76
        port: 3306
      - ip: 10.0.10.68
        port: 3306
      - ip: 10.0.10.63
        port: 3306
```

The operator creates the per-node headless `Service` and `Endpoints` objects
automatically. Manual per-node YAML files are no longer required.

## What It Creates

When at least one node endpoint is available, the operator reconciles:

- `Service`/`Endpoints` per external InnoDB node
  - generated from `spec.innodbCluster.nodes`.
  - pruned automatically when a node is removed from the custom resource.
- `Secret/mysql-router-bootstrap`
  - keeps `MYSQL_BOOTSTRAP_HOST` and `MYSQL_BOOTSTRAP_PORT` pointed at the first
    generated external-node service FQDN.
- `ConfigMap/mysql-router-config`
  - stores `router-entrypoint.sh`.
  - stores `innodb-nodes.txt` with every generated external-node service FQDN and
    port.
- `Service/mysql-router`
  - headless service for router pod FQDNs.
- `Deployment/mysql-router`
  - one router replica per discovered InnoDB node.
  - uses `emptyDir` for `/router`, so there is no PVC/PV.
  - mounts the ConfigMap for startup logic and discovered inventory.

## 1. Install the Operator

The operator image is pulled from the public GHCR repository:

```text
ghcr.io/tripplea-sg/mysql-router-operator:0.1.0
```

OKE can pull this public image without `imagePullSecrets`.

Kubernetes manifests are available in the GitHub `deploy` directory:

- [namespace.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/namespace.yaml)
- [mysqlrouter-crd.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/mysqlrouter-crd.yaml)
- [operator-rbac.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/operator-rbac.yaml)
- [operator-deployment.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/operator-deployment.yaml)
- [secret.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/secret.yaml)
- [mysqlrouter.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/mysqlrouter.yaml)

For a complete sample installation from GitHub:

```sh
kubectl apply -k https://github.com/tripplea-sg/mysql-router-operator//deploy?ref=main
```

If you cloned the repository locally, run:

```sh
kubectl apply -k deploy
```

Check that the operator is running:

```sh
kubectl get pods -n mysql-router
kubectl logs -n mysql-router deploy/mysql-router-operator
```

The Kustomize deployment includes:

- [deploy/namespace.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/namespace.yaml)
- [deploy/mysqlrouter-crd.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/mysqlrouter-crd.yaml)
- [deploy/operator-rbac.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/operator-rbac.yaml)
- [deploy/operator-deployment.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/operator-deployment.yaml)
- [deploy/secret.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/secret.yaml)
- [deploy/mysqlrouter.yaml](https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/mysqlrouter.yaml)

For a production-style rollout, you can apply the pieces in phases:

```sh
kubectl apply -f https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/namespace.yaml
kubectl apply -f https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/mysqlrouter-crd.yaml
kubectl apply -f https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/operator-rbac.yaml
kubectl apply -f https://github.com/tripplea-sg/mysql-router-operator/blob/main/deploy/operator-deployment.yaml
```

Then create the secret and `MySQLRouter` resource as described below.

## 2. Create the Bootstrap Secret

MySQL Router needs a MySQL account that can bootstrap against the external
InnoDB Cluster. The included `deploy/secret.yaml` creates:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mysql-router-bootstrap
  namespace: mysql-router
type: Opaque
stringData:
  MYSQL_BOOTSTRAP_USER: gradmin
  MYSQL_BOOTSTRAP_PASSWORD: grpass
  MYSQL_BOOTSTRAP_HOST: placeholder
  MYSQL_BOOTSTRAP_PORT: "3306"
```

You can create it from YAML:

```sh
kubectl apply -f deploy/namespace.yaml
kubectl apply -f deploy/secret.yaml
```

Or imperatively:

```sh
kubectl create secret generic mysql-router-bootstrap \
  -n mysql-router \
  --from-literal=MYSQL_BOOTSTRAP_USER=gradmin \
  --from-literal=MYSQL_BOOTSTRAP_PASSWORD=grpass \
  --from-literal=MYSQL_BOOTSTRAP_HOST=placeholder \
  --from-literal=MYSQL_BOOTSTRAP_PORT=3306
```

The operator updates `MYSQL_BOOTSTRAP_HOST` and `MYSQL_BOOTSTRAP_PORT` to the
first generated external-node service FQDN, so the initial host can be a
placeholder.

## 3. Deploy MySQL Router

Define the external InnoDB Cluster nodes in `deploy/mysqlrouter.yaml`:

```yaml
apiVersion: mysql.oracle.com/v1alpha1
kind: MySQLRouter
metadata:
  name: mysql-router
  namespace: mysql-router
spec:
  router:
    serviceName: mysql-router
    bootstrapSecret: mysql-router-bootstrap
    image: container-registry.oracle.com/mysql/community-router:9.7
  innodbCluster:
    name: oke-cawnvg2rvuq-nzkjuhaz6jq-snvjd2jtcoq
    nodeServicePrefix: oke-cawnvg2rvuq-nzkjuhaz6jq-snvjd2jtcoq
    nodes:
      - ip: 10.0.10.76
        port: 3306
      - ip: 10.0.10.68
        port: 3306
      - ip: 10.0.10.63
        port: 3306
```

Apply it:

```sh
kubectl apply -f deploy/namespace.yaml
kubectl apply -f deploy/mysqlrouter.yaml
```

The operator creates:

- one generated headless service and endpoint per external InnoDB node
- `ConfigMap/mysql-router-config`
- `Service/mysql-router`
- `Deployment/mysql-router`

Verify:

```sh
kubectl get mysqlrouter -n mysql-router
kubectl get svc,endpoints,deploy,pods -n mysql-router
```

## 4. Adapt to InnoDB Cluster Scaling

To change the external InnoDB Cluster nodes, edit `deploy/mysqlrouter.yaml` and apply
again. For example, when scaling from 3 external nodes to 5:

```yaml
spec:
  innodbCluster:
    nodes:
      - ip: 10.0.10.76
        port: 3306
      - ip: 10.0.10.68
        port: 3306
      - ip: 10.0.10.63
        port: 3306
      - ip: 10.0.10.81
        port: 3306
      - ip: 10.0.10.82
        port: 3306
```

Then apply:

```sh
kubectl apply -f deploy/mysqlrouter.yaml
```

On reconcile, the operator:

- creates services/endpoints for new nodes
- updates `mysql-router-config`
- updates the bootstrap secret if the first node changes
- scales the router deployment to the node count by default
- prunes generated services/endpoints for removed nodes

If you want a fixed number of router replicas independent of InnoDB node count,
set:

```yaml
spec:
  router:
    replicas: 3
```
