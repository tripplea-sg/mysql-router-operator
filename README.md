# MySQL Router Operator

Author: Hananto Wicaksono

License: GNU General Public License v3.0 or later

This workspace contains a small Kopf operator that deploys MySQL Router inside
Kubernetes for an InnoDB Cluster running outside Kubernetes.

## What It Watches

The sample manifests deploy the operator in the mysql-router namespace, but
the operator watches MySQLRouter custom resources in every namespace.

The namespace of the MySQLRouter custom resource is the namespace where MySQL
Router is deployed:

```yaml
apiVersion: mysql.oracle.com/v1alpha1
kind: MySQLRouter
metadata:
  namespace: <mysql_router_namespace>
spec:
  innodbCluster:
    nodes:
      - hostname: <innodb_cluster_node_hostname>
        ip: <innodb_cluster_node_ip>
        port: <mysql_port>
```
Example:
```yaml
apiVersion: mysql.oracle.com/v1alpha1
kind: MySQLRouter
metadata:
  namespace: mysql-router
spec:
  innodbCluster:
    nodes:
      - hostname: sun
        ip: 10.0.10.76
        port: 3306
      - hostname: earth
        ip: 10.0.10.68
        port: 3306
      - hostname: moon
        ip: 10.0.10.63
        port: 3306
```

The operator creates the per-node headless Service and Endpoints objects in
the same namespace automatically. Manual per-node YAML files are no longer
required.

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
ghcr.io/tripplea-sg/mysql-router-operator:0.1.9
```

OKE/Kubernetes can pull this public image without imagePullSecrets.

The image must include linux/amd64 because most OKE worker nodes are AMD64.
The GitHub Actions workflow builds both linux/amd64 and linux/arm64.

Kubernetes manifests are available in the GitHub deploy directory:

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
kubectl apply -f https://raw.githubusercontent.com/tripplea-sg/mysql-router-operator/main/deploy/namespace.yaml
kubectl apply -f https://raw.githubusercontent.com/tripplea-sg/mysql-router-operator/main/deploy/mysqlrouter-crd.yaml
kubectl apply -f https://raw.githubusercontent.com/tripplea-sg/mysql-router-operator/main/deploy/operator-rbac.yaml
kubectl apply -f https://raw.githubusercontent.com/tripplea-sg/mysql-router-operator/main/deploy/operator-deployment.yaml
```

Then create the secret and `MySQLRouter` resource as described below.

## 2. Create the Bootstrap Secret

MySQL Router needs a MySQL account that can bootstrap against the external
InnoDB Cluster with the following manifest (`secret.yaml`):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mysql-router-bootstrap
  namespace: <your-namespace>
type: Opaque
stringData:
  MYSQL_BOOTSTRAP_USER: <innodb_cluster_admin_user>
  MYSQL_BOOTSTRAP_PASSWORD: <innodb_cluster_admin_password>
  MYSQL_BOOTSTRAP_HOST: placeholder
  MYSQL_BOOTSTRAP_PORT: "<mysql_port>"
```
Example:
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
kubectl apply -f secret.yaml
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

Define the external InnoDB Cluster nodes in `mysqlrouter.yaml` using the following template:

```yaml
apiVersion: mysql.oracle.com/v1alpha1
kind: MySQLRouter
metadata:
  name: <name_of_the_mysql_router_deployment>
  namespace: <namespace_for_running_mysql_router>
spec:
  router:
    replicas: <number_of_mysql_router>
    serviceName: mysql-router
    bootstrapSecret: <secret_name>
    image: container-registry.oracle.com/mysql/community-router:9.7
  innodbCluster:
    nodes:
      - hostname: <hostname_of_innodb_cluster_node>
        ip: <ip_address_of_innodb_cluster_node>
        port: <mysql_port>
```
Example:

```yaml
apiVersion: mysql.oracle.com/v1alpha1
kind: MySQLRouter
metadata:
  name: mysql-router
  namespace: mysql-router
spec:
  router:
    replicas: 3
    serviceName: mysql-router
    bootstrapSecret: mysql-router-bootstrap
    image: container-registry.oracle.com/mysql/community-router:9.7
  innodbCluster:
    nodes:
      - hostname: oke-cawnvg2rvuq-nzkjuhaz6jq-snvjd2jtcoq-0
        ip: 10.0.10.76
        port: 3306
      - hostname: oke-cawnvg2rvuq-nzkjuhaz6jq-snvjd2jtcoq-1
        ip: 10.0.10.68
        port: 3306
      - hostname: oke-cawnvg2rvuq-nzkjuhaz6jq-snvjd2jtcoq-2
        ip: 10.0.10.63
        port: 3306
```

Apply it:

```sh
kubectl apply -f mysqlrouter.yaml
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

To change the external InnoDB Cluster nodes, edit `mysqlrouter.yaml` and apply
again. For example, when scaling from 3 external nodes to 5:

```yaml
spec:
  innodbCluster:
    nodes:
      - hostname: mysql-0.example.com
        ip: 10.0.10.76
        port: 3306
      - hostname: mysql-1.example.com
        ip: 10.0.10.68
        port: 3306
      - hostname: mysql-2.example.com
        ip: 10.0.10.63
        port: 3306
      - hostname: mysql-3.example.com
        ip: 10.0.10.81
        port: 3306
      - hostname: mysql-4.example.com
        ip: 10.0.10.82
        port: 3306
```

Then apply:

```sh
kubectl apply -f mysqlrouter.yaml
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
## 5. Test MySQL Router

I'm using namespace `mysql-router` for running my routers. Check that the router pods are running and replace namespace name into your namespace name used to run routers:

```sh
kubectl get pods -n mysql-router -l app=mysql-router -o wide
kubectl logs -n mysql-router deploy/mysql-router
```

Check generated services and endpoints:

```sh
kubectl get svc,endpoints -n mysql-router
```

Create a temporary MySQL client pod:

```sh
kubectl run mysql-client \
  -n mysql-router \
  --image=container-registry.oracle.com/mysql/community-server:9.7 \
  --restart=Never \
  --command -- sleep 3600
```

Connect through the read-write router port:

```sh
kubectl exec -it mysql-client -n mysql-router -- \
  mysql -h mysql-router.mysql-router.svc.cluster.local -P 6446 -u gradmin -p
```

Run:

```sql
SELECT @@hostname, @@port, @@read_only, @@super_read_only;
```

Connect through the read-only router port:

```sh
kubectl exec -it mysql-client -n mysql-router -- \
  mysql -h mysql-router.mysql-router.svc.cluster.local -P 6447 -u gradmin -p
```

If MySQL login fails, run a TCP connectivity check:

```sh
kubectl exec -it mysql-client -n mysql-router -- \
  bash -lc 'timeout 5 bash -c "</dev/tcp/mysql-router.mysql-router.svc.cluster.local/6446" && echo OK'
```

Clean up the test pod:

```sh
kubectl delete pod mysql-client -n mysql-router
```

If testing fails, collect diagnostics:

```sh
kubectl get pods -n mysql-router -o wide
kubectl get svc,endpoints -n mysql-router
kubectl logs -n mysql-router deploy/mysql-router
kubectl describe pod -n mysql-router -l app=mysql-router
```

## 6. Housekeeping and Uninstall

Delete all `MySQLRouter` custom resources first, so the operator can clean up
generated router resources:

```sh
kubectl delete mysqlrouter --all -A --ignore-not-found
```

Delete any generated router resources that remain in `mysql-router`:

```sh
kubectl delete deploy mysql-router -n mysql-router --ignore-not-found
kubectl delete svc mysql-router -n mysql-router --ignore-not-found
kubectl delete cm mysql-router-config -n mysql-router --ignore-not-found
kubectl delete svc -n mysql-router -l mysql.oracle.com/router-node=external-innodb --ignore-not-found
kubectl delete endpoints -n mysql-router -l mysql.oracle.com/router-node=external-innodb --ignore-not-found
```

Delete the operator:

```sh
kubectl delete deploy mysql-router-operator -n mysql-router --ignore-not-found
kubectl delete serviceaccount mysql-router-operator -n mysql-router --ignore-not-found
kubectl delete clusterrolebinding mysql-router-operator --ignore-not-found
kubectl delete clusterrole mysql-router-operator --ignore-not-found
```

Delete the CRD:

```sh
kubectl delete crd mysqlrouters.mysql.oracle.com --ignore-not-found
```

Delete the bootstrap secret and namespace:

```sh
kubectl delete secret mysql-router-bootstrap -n mysql-router --ignore-not-found
kubectl delete ns mysql-router --ignore-not-found
```

If a `MySQLRouter` resource is stuck because of a finalizer, remove it:

```sh
kubectl patch mysqlrouter <name> -n <namespace> \
  --type=json \
  -p='[{"op":"remove","path":"/metadata/finalizers"}]'
```

If the namespace is stuck in `Terminating`, inspect it:

```sh
kubectl get ns mysql-router -o yaml
```

As a last resort, force-finalize the namespace:

```sh
kubectl get ns mysql-router -o json > ns.json
```

Edit `ns.json` so `spec.finalizers` is empty:

```json
"spec": {
  "finalizers": []
}
```

Then run:

```sh
kubectl replace --raw "/api/v1/namespaces/mysql-router/finalize" -f ns.json
```

