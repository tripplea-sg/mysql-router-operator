import base64
import hashlib
import json
import os
from typing import Any, Dict, List, Tuple

import kopf
from kubernetes import client, config
from kubernetes.client import ApiException


NAMESPACE = os.getenv("ROUTER_NAMESPACE", "mysql-router")
ROUTER_NAME = os.getenv("ROUTER_NAME", "mysql-router")
BOOTSTRAP_SECRET = os.getenv("BOOTSTRAP_SECRET", "mysql-router-bootstrap")
CLUSTER_LABEL = os.getenv("CLUSTER_LABEL", "mysql.oracle.com/innodb-cluster")
OWNER_LABEL = "mysql.oracle.com/router-owner"
NODE_ROLE_LABEL = "mysql.oracle.com/router-node"
ROUTER_LABELS = {"app.kubernetes.io/managed-by": "kopf"}
ROUTER_IMAGE = os.getenv(
    "ROUTER_IMAGE", "container-registry.oracle.com/mysql/community-router:9.7"
)


def load_kube_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def appsv1() -> client.AppsV1Api:
    return client.AppsV1Api()


def corev1() -> client.CoreV1Api:
    return client.CoreV1Api()


def resource_checksum(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def router_settings(
    namespace: str, name: str, spec: Dict[str, Any]
) -> Tuple[str, str, str, str, int, str]:
    router = spec.get("router") or {}
    cluster = spec.get("innodbCluster") or {}
    router_name = router.get("serviceName") or router.get("name") or ROUTER_NAME or name
    secret_name = router.get("bootstrapSecret") or BOOTSTRAP_SECRET
    image = router.get("image") or ROUTER_IMAGE
    cluster_name = cluster.get("name") or name
    replicas = int(router.get("replicas") or len(cluster.get("nodes") or []) or 1)
    node_prefix = cluster.get("nodeServicePrefix") or f"{router_name}-node"
    return router_name, secret_name, image, cluster_name, replicas, node_prefix


def desired_nodes(
    namespace: str,
    owner_name: str,
    spec: Dict[str, Any],
    cluster_name: str,
    node_prefix: str,
) -> List[Dict[str, Any]]:
    raw_nodes = (spec.get("innodbCluster") or {}).get("nodes") or []
    nodes: List[Dict[str, Any]] = []

    for index, raw_node in enumerate(raw_nodes):
        if not raw_node.get("ip"):
            raise kopf.PermanentError(f"spec.innodbCluster.nodes[{index}].ip is required")

        service_name = raw_node.get("serviceName") or raw_node.get("name") or f"{node_prefix}-{index}"
        port = int(raw_node.get("port") or 3306)
        nodes.append(
            {
                "name": service_name,
                "ip": raw_node["ip"],
                "host": f"{service_name}.{namespace}.svc.cluster.local",
                "port": port,
                "labels": node_labels(owner_name, cluster_name),
            }
        )

    return nodes


def node_labels(owner_name: str, cluster_name: str) -> Dict[str, str]:
    return {
        **ROUTER_LABELS,
        CLUSTER_LABEL: cluster_name,
        OWNER_LABEL: owner_name,
        NODE_ROLE_LABEL: "external-innodb",
    }


def router_labels(router_name: str, owner_name: str) -> Dict[str, str]:
    return {**ROUTER_LABELS, "app": router_name, OWNER_LABEL: owner_name}


def apply_external_node_service(namespace: str, node: Dict[str, Any]) -> None:
    body = client.V1Service(
        metadata=client.V1ObjectMeta(
            name=node["name"],
            namespace=namespace,
            labels=node["labels"],
        ),
        spec=client.V1ServiceSpec(
            cluster_ip="None",
            ports=[client.V1ServicePort(name="mysql", port=node["port"], target_port=node["port"])],
        ),
    )
    try:
        corev1().create_namespaced_service(namespace, body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        corev1().patch_namespaced_service(node["name"], namespace, body)


def apply_external_node_endpoints(namespace: str, node: Dict[str, Any]) -> None:
    body = client.V1Endpoints(
        metadata=client.V1ObjectMeta(
            name=node["name"],
            namespace=namespace,
            labels=node["labels"],
        ),
        subsets=[
            client.V1EndpointSubset(
                addresses=[client.V1EndpointAddress(ip=node["ip"])],
                ports=[client.V1EndpointPort(name="mysql", port=node["port"])],
            )
        ],
    )
    try:
        corev1().create_namespaced_endpoints(namespace, body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        corev1().patch_namespaced_endpoints(node["name"], namespace, body)


def prune_stale_external_nodes(
    namespace: str, owner_name: str, desired_names: List[str]
) -> None:
    selector = f"{OWNER_LABEL}={owner_name},{NODE_ROLE_LABEL}=external-innodb"
    desired = set(desired_names)

    for svc in corev1().list_namespaced_service(namespace, label_selector=selector).items:
        if svc.metadata.name not in desired:
            corev1().delete_namespaced_service(svc.metadata.name, namespace)

    for ep in corev1().list_namespaced_endpoints(namespace, label_selector=selector).items:
        if ep.metadata.name not in desired:
            corev1().delete_namespaced_endpoints(ep.metadata.name, namespace)


def delete_if_exists(api_call: Any, name: str, namespace: str) -> None:
    try:
        api_call(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise


def cleanup_owned_resources(namespace: str, owner_name: str, spec: Dict[str, Any]) -> None:
    router_name, _, _, _, _, _ = router_settings(namespace, owner_name, spec)
    selector = f"{OWNER_LABEL}={owner_name}"

    for ep in corev1().list_namespaced_endpoints(namespace, label_selector=selector).items:
        delete_if_exists(corev1().delete_namespaced_endpoints, ep.metadata.name, namespace)

    for svc in corev1().list_namespaced_service(namespace, label_selector=selector).items:
        delete_if_exists(corev1().delete_namespaced_service, svc.metadata.name, namespace)

    for cm in corev1().list_namespaced_config_map(namespace, label_selector=selector).items:
        delete_if_exists(corev1().delete_namespaced_config_map, cm.metadata.name, namespace)

    delete_if_exists(appsv1().delete_namespaced_deployment, router_name, namespace)


def apply_external_nodes(namespace: str, owner_name: str, nodes: List[Dict[str, Any]]) -> None:
    for node in nodes:
        apply_external_node_service(namespace, node)
        apply_external_node_endpoints(namespace, node)
    prune_stale_external_nodes(namespace, owner_name, [node["name"] for node in nodes])


def read_secret_value(secret: client.V1Secret, key: str, default: str = "") -> str:
    if secret.string_data and key in secret.string_data:
        return secret.string_data[key]
    if secret.data and key in secret.data:
        return base64.b64decode(secret.data[key]).decode()
    return default


def patch_secret_bootstrap_host(
    namespace: str, secret_name: str, nodes: List[Dict[str, Any]]
) -> None:
    if not nodes:
        return
    try:
        secret = corev1().read_namespaced_secret(secret_name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise kopf.TemporaryError(
                f"Secret {secret_name!r} is required before router bootstrap.",
                delay=30,
            )
        raise

    current_host = read_secret_value(secret, "MYSQL_BOOTSTRAP_HOST")
    current_port = read_secret_value(secret, "MYSQL_BOOTSTRAP_PORT", "3306")
    desired_host = nodes[0]["host"]
    desired_port = str(nodes[0]["port"])

    if current_host == desired_host and current_port == desired_port:
        return

    corev1().patch_namespaced_secret(
        secret_name,
        namespace,
        {
            "stringData": {
                "MYSQL_BOOTSTRAP_HOST": desired_host,
                "MYSQL_BOOTSTRAP_PORT": desired_port,
            }
        },
    )


def apply_configmap(
    namespace: str, router_name: str, owner_name: str, nodes: List[Dict[str, Any]]
) -> None:
    node_lines = "\n".join(f"{node['host']}:{node['port']}" for node in nodes)
    startup_script = r"""#!/bin/sh
set -eu

mkdir -p /router

if [ ! -f /router/mysqlrouter.conf ]; then
  printf '%s\n' "${MYSQL_BOOTSTRAP_PASSWORD}" | mysqlrouter \
    --bootstrap "${MYSQL_BOOTSTRAP_USER}@${MYSQL_BOOTSTRAP_HOST}:${MYSQL_BOOTSTRAP_PORT}" \
    --directory /router
fi

awk \
  -v ROUTER_BIND_ADDRESS=0.0.0.0 \
  -v HTTP_BIND_ADDRESS=0.0.0.0 \
  -v RW_PORT=6446 \
  -v RO_PORT=6447 \
  -v X_RW_PORT=6448 \
  -v X_RO_PORT=6449 \
  -v HTTP_PORT=8443 '
function flush_http_bind() {
  if (section_type == "http_server" && !saw_http_bind) {
    print "bind_address=" HTTP_BIND_ADDRESS
  }
}

/^[[]routing:[^]]+[]]$/ {
  flush_http_bind()
  section_type = "routing"
  routing_key = $0
  sub(/^\[routing:/, "", routing_key)
  sub(/\]$/, "", routing_key)
  saw_http_bind = 0
  print
  next
}

/^[[]http_server[]]$/ {
  flush_http_bind()
  section_type = "http_server"
  routing_key = ""
  saw_http_bind = 0
  print
  next
}

/^[[]/ {
  flush_http_bind()
  section_type = "other"
  routing_key = ""
  saw_http_bind = 0
  print
  next
}

section_type == "routing" && /^bind_address=/ {
  print "bind_address=" ROUTER_BIND_ADDRESS
  next
}

section_type == "routing" && /^bind_port=/ {
  if (routing_key ~ /_x_rw$/) { print "bind_port=" X_RW_PORT; next }
  if (routing_key ~ /_x_ro$/) { print "bind_port=" X_RO_PORT; next }
  if (routing_key ~ /_rw$/) { print "bind_port=" RW_PORT; next }
  if (routing_key ~ /_ro$/) { print "bind_port=" RO_PORT; next }
}

section_type == "http_server" && /^port=/ {
  print "port=" HTTP_PORT
  next
}

section_type == "http_server" && /^bind_address=/ {
  print "bind_address=" HTTP_BIND_ADDRESS
  saw_http_bind = 1
  next
}

{
  print
}

END {
  flush_http_bind()
}
' /router/mysqlrouter.conf > /router/mysqlrouter.conf.tmp
mv /router/mysqlrouter.conf.tmp /router/mysqlrouter.conf

exec mysqlrouter -c /router/mysqlrouter.conf
"""
    body = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(
            name=f"{router_name}-config",
            namespace=namespace,
            labels=router_labels(router_name, owner_name),
        ),
        data={"router-entrypoint.sh": startup_script, "innodb-nodes.txt": node_lines},
    )
    try:
        corev1().create_namespaced_config_map(namespace, body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        corev1().patch_namespaced_config_map(body.metadata.name, namespace, body)


def deployment_body(
    namespace: str,
    router_name: str,
    owner_name: str,
    secret_name: str,
    image: str,
    replicas: int,
    nodes: List[Dict[str, Any]],
) -> client.V1Deployment:
    checksum = resource_checksum({"nodes": nodes, "image": image})
    labels = router_labels(router_name, owner_name)
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(name=router_name, namespace=namespace, labels=labels),
        spec=client.V1DeploymentSpec(
            replicas=max(1, replicas),
            selector=client.V1LabelSelector(match_labels={"app": router_name}),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": router_name},
                    annotations={"router.mysql.oracle.com/config-checksum": checksum},
                ),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="mysqlrouter",
                            image=image,
                            image_pull_policy="IfNotPresent",
                            env_from=[
                                client.V1EnvFromSource(
                                    secret_ref=client.V1SecretEnvSource(name=secret_name)
                                )
                            ],
                            command=["/bin/sh", "/config/router-entrypoint.sh"],
                            ports=[
                                client.V1ContainerPort(name="mysql-rw", container_port=6446),
                                client.V1ContainerPort(name="mysql-ro", container_port=6447),
                                client.V1ContainerPort(name="mysqlx-rw", container_port=6448),
                                client.V1ContainerPort(name="mysqlx-ro", container_port=6449),
                                client.V1ContainerPort(name="http", container_port=8443),
                            ],
                            volume_mounts=[
                                client.V1VolumeMount(name="router-data", mount_path="/router"),
                                client.V1VolumeMount(name="router-config", mount_path="/config"),
                            ],
                            readiness_probe=client.V1Probe(
                                tcp_socket=client.V1TCPSocketAction(port=6446),
                                initial_delay_seconds=10,
                                period_seconds=5,
                            ),
                            liveness_probe=client.V1Probe(
                                tcp_socket=client.V1TCPSocketAction(port=6446),
                                initial_delay_seconds=30,
                                period_seconds=10,
                            ),
                        )
                    ],
                    volumes=[
                        client.V1Volume(
                            name="router-data",
                            empty_dir=client.V1EmptyDirVolumeSource(),
                        ),
                        client.V1Volume(
                            name="router-config",
                            config_map=client.V1ConfigMapVolumeSource(
                                name=f"{router_name}-config", default_mode=0o555
                            ),
                        ),
                    ],
                ),
            ),
        ),
    )


def apply_deployment(
    namespace: str,
    router_name: str,
    owner_name: str,
    secret_name: str,
    image: str,
    replicas: int,
    nodes: List[Dict[str, Any]],
) -> None:
    body = deployment_body(
        namespace, router_name, owner_name, secret_name, image, replicas, nodes
    )
    try:
        appsv1().create_namespaced_deployment(namespace, body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        appsv1().patch_namespaced_deployment(router_name, namespace, body)


def apply_router_service(namespace: str, router_name: str, owner_name: str) -> None:
    body = client.V1Service(
        metadata=client.V1ObjectMeta(
            name=router_name,
            namespace=namespace,
            labels=router_labels(router_name, owner_name),
        ),
        spec=client.V1ServiceSpec(
            cluster_ip="None",
            selector={"app": router_name},
            ports=[
                client.V1ServicePort(name="mysql-rw", port=6446, target_port=6446),
                client.V1ServicePort(name="mysql-ro", port=6447, target_port=6447),
                client.V1ServicePort(name="mysqlx-rw", port=6448, target_port=6448),
                client.V1ServicePort(name="mysqlx-ro", port=6449, target_port=6449),
                client.V1ServicePort(name="http", port=8443, target_port=8443),
            ],
        ),
    )
    try:
        corev1().create_namespaced_service(namespace, body)
    except ApiException as exc:
        if exc.status != 409:
            raise
        corev1().patch_namespaced_service(router_name, namespace, body)


def reconcile(
    namespace: str,
    name: str,
    spec: Dict[str, Any],
    logger: Any,
    reason: str = "custom resource",
) -> None:
    router_name, secret_name, image, cluster_name, replicas, node_prefix = router_settings(
        namespace, name, spec
    )
    nodes = desired_nodes(namespace, name, spec, cluster_name, node_prefix)
    if not nodes:
        raise kopf.PermanentError("spec.innodbCluster.nodes must contain at least one node")

    apply_external_nodes(namespace, name, nodes)
    patch_secret_bootstrap_host(namespace, secret_name, nodes)
    apply_configmap(namespace, router_name, name, nodes)
    apply_router_service(namespace, router_name, name)
    apply_deployment(namespace, router_name, name, secret_name, image, replicas, nodes)
    logger.info("Reconciled %s with %d external InnoDB nodes.", reason, len(nodes))


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_: Any) -> None:
    load_kube_config()
    settings.posting.level = 20


@kopf.on.resume("mysql.oracle.com", "v1alpha1", "mysqlrouters")
@kopf.on.create("mysql.oracle.com", "v1alpha1", "mysqlrouters")
@kopf.on.update("mysql.oracle.com", "v1alpha1", "mysqlrouters")
def mysql_router_changed(
    namespace: str,
    name: str,
    spec: Dict[str, Any],
    logger: Any,
    **_: Any,
) -> None:
    reconcile(namespace, name, spec, logger)


@kopf.on.delete("mysql.oracle.com", "v1alpha1", "mysqlrouters")
def mysql_router_deleted(
    namespace: str,
    name: str,
    spec: Dict[str, Any],
    logger: Any,
    **_: Any,
) -> None:
    cleanup_owned_resources(namespace, name, spec)
    logger.info("Removed resources owned by MySQLRouter/%s.", name)


@kopf.timer("mysql.oracle.com", "v1alpha1", "mysqlrouters", interval=300.0, sharp=True)
def periodic_reconcile(
    namespace: str,
    name: str,
    spec: Dict[str, Any],
    logger: Any,
    **_: Any,
) -> None:
    reconcile(namespace, name, spec, logger, reason="periodic scan")
