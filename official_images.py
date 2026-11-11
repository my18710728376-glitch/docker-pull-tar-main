"""内置官方镜像清单。

按镜像仓库分组维护官方镜像条目，用于 Web 界面的「官方镜像筛选」：
- 未选择任何分组时，搜索走原有在线接口（保持原有模糊检索能力）；
- 选择分组后，搜索范围严格限定在所选分组的清单内，并支持精确全匹配优先展示。

清单为静态数据，不依赖网络，便于离线搜索与测试。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from registry_config import RegistryConfigError, normalize_repository, split_image_reference


@dataclass(frozen=True)
class OfficialImage:
    """官方镜像条目。"""

    repo: str
    description: str = ""


@dataclass(frozen=True)
class OfficialImageGroup:
    """一组官方镜像（对应一个镜像仓库）。"""

    key: str
    name: str
    host: str
    images: Tuple[OfficialImage, ...]


def _group(key: str, name: str, host: str, entries: Sequence[Tuple[str, str]]) -> OfficialImageGroup:
    return OfficialImageGroup(
        key=key,
        name=name,
        host=host,
        images=tuple(OfficialImage(repo=repo, description=desc) for repo, desc in entries),
    )


OFFICIAL_IMAGE_GROUPS: Dict[str, OfficialImageGroup] = {
    "docker": _group("docker", "Docker 官方镜像", "registry-1.docker.io", [
        ("library/nginx", "官方 Nginx Web 服务器镜像"),
        ("library/redis", "官方 Redis 内存数据库镜像"),
        ("library/postgres", "官方 PostgreSQL 数据库镜像"),
        ("library/mysql", "官方 MySQL 数据库镜像"),
        ("library/mongo", "官方 MongoDB 数据库镜像"),
        ("library/python", "官方 Python 运行时镜像"),
        ("library/node", "官方 Node.js 运行时镜像"),
        ("library/alpine", "官方 Alpine Linux 基础镜像"),
        ("library/ubuntu", "官方 Ubuntu 基础镜像"),
        ("library/busybox", "官方 BusyBox 基础镜像"),
        ("library/hello-world", "官方最小验证镜像"),
        ("library/traefik", "官方 Traefik 边缘路由镜像"),
    ]),
    "ghcr": _group("ghcr", "GitHub 官方镜像", "ghcr.io", [
        ("ghcr.io/actions/actions-runner", "GitHub Actions 自托管 Runner"),
        ("ghcr.io/astral-sh/uv", "uv Python 包管理器"),
        ("ghcr.io/astral-sh/rye", "Rye Python 项目管理工具"),
        ("ghcr.io/linuxcontainers/alpine", "Linux Containers Alpine 镜像"),
        ("ghcr.io/renovatebot/renovate", "Renovate 依赖更新机器人"),
        ("ghcr.io/immich-app/immich-server", "Immich 照片管理服务"),
        ("ghcr.io/kite-org/kite", "Kite Kubernetes 仪表盘"),
    ]),
    "quay": _group("quay", "Quay 官方镜像", "quay.io", [
        ("quay.io/prometheus/prometheus", "Prometheus 监控服务"),
        ("quay.io/prometheus/node-exporter", "Node Exporter 主机指标采集"),
        ("quay.io/prometheus/alertmanager", "Alertmanager 告警服务"),
        ("quay.io/coreos/etcd", "CoreOS etcd 键值存储"),
        ("quay.io/argoproj/argocd", "Argo CD 持续交付工具"),
        ("quay.io/jetstack/cert-manager-controller", "cert-manager 证书控制器"),
    ]),
    "k8s": _group("k8s", "Kubernetes 官方镜像", "registry.k8s.io", [
        ("registry.k8s.io/pause", "Kubernetes pause 基础镜像"),
        ("registry.k8s.io/etcd", "Kubernetes etcd 镜像"),
        ("registry.k8s.io/coredns/coredns", "Kubernetes CoreDNS 镜像"),
        ("registry.k8s.io/kube-apiserver", "Kubernetes API Server"),
        ("registry.k8s.io/kube-controller-manager", "Kubernetes 控制器管理器"),
        ("registry.k8s.io/kube-scheduler", "Kubernetes 调度器"),
        ("registry.k8s.io/kube-proxy", "Kubernetes 网络代理"),
        ("registry.k8s.io/ingress-nginx/controller", "Kubernetes Ingress Nginx 控制器"),
        ("registry.k8s.io/kubectl", "Kubernetes kubectl 命令行工具镜像"),
    ]),
    "k8s-gcr": _group("k8s-gcr", "Kubernetes 旧版官方镜像", "k8s.gcr.io", [
        ("k8s.gcr.io/pause", "Kubernetes 旧版 pause 镜像"),
        ("k8s.gcr.io/etcd", "Kubernetes 旧版 etcd 镜像"),
        ("k8s.gcr.io/coredns/coredns", "Kubernetes 旧版 CoreDNS 镜像"),
        ("k8s.gcr.io/kube-apiserver", "Kubernetes 旧版 API Server"),
        ("k8s.gcr.io/kube-proxy", "Kubernetes 旧版网络代理"),
    ]),
    "gcr": _group("gcr", "Google 官方镜像", "gcr.io", [
        ("gcr.io/distroless/static", "Distroless 静态基础镜像"),
        ("gcr.io/distroless/base", "Distroless 基础镜像"),
        ("gcr.io/distroless/java", "Distroless Java 运行时镜像"),
        ("gcr.io/google-containers/pause", "Google pause 镜像"),
        ("gcr.io/google-containers/etcd", "Google etcd 镜像"),
        ("gcr.io/kaniko-project/executor", "Kaniko 镜像构建器"),
    ]),
    "gitlab": _group("gitlab", "GitLab 官方镜像", "registry.gitlab.com", [
        ("registry.gitlab.com/gitlab-org/gitlab-runner", "GitLab Runner"),
        ("registry.gitlab.com/gitlab-org/omnibus-gitlab", "GitLab Omnibus 镜像"),
        ("registry.gitlab.com/gitlab-org/container-registry", "GitLab Container Registry"),
    ]),
}

DEFAULT_PAGE_SIZE = 20


def get_group_choices() -> List[Tuple[str, str]]:
    """返回官方镜像筛选下拉框选项：``[(展示名称, 分组键), ...]``。"""
    return [(group.name, group.key) for group in OFFICIAL_IMAGE_GROUPS.values()]


def normalize_group_keys(group_keys) -> List[str]:
    """把筛选下拉的取值统一为有效的分组 key 列表。

    兼容 None、单个字符串、字符串列表，以及下拉显示名（如「Docker 官方镜像」）；
    未知值被忽略，结果按配置顺序去重。空列表表示未选择任何分组。
    """
    if group_keys is None:
        return []
    raw = [group_keys] if isinstance(group_keys, str) else list(group_keys or [])
    label_to_key = {group.name: key for key, group in OFFICIAL_IMAGE_GROUPS.items()}
    wanted = set()
    for item in raw:
        if item is None:
            continue
        text = str(item).strip()
        if not text:
            continue
        key = label_to_key.get(text, text)
        if key in OFFICIAL_IMAGE_GROUPS:
            wanted.add(key)
    return [key for key in OFFICIAL_IMAGE_GROUPS if key in wanted]


def get_group_names(group_keys) -> List[str]:
    """返回所选分组的展示名称，保持配置顺序。"""
    return [OFFICIAL_IMAGE_GROUPS[key].name for key in normalize_group_keys(group_keys)]


def get_images(group_keys: Optional[Sequence[str]] = None) -> List[OfficialImage]:
    """获取指定分组的官方镜像；未指定分组时返回全部清单（按仓库去重）。"""
    if not group_keys:
        keys = list(OFFICIAL_IMAGE_GROUPS.keys())
    else:
        keys = normalize_group_keys(group_keys)

    images: List[OfficialImage] = []
    seen_repos: Set[str] = set()
    for key in keys:
        for image in OFFICIAL_IMAGE_GROUPS[key].images:
            if image.repo in seen_repos:
                continue
            seen_repos.add(image.repo)
            images.append(image)
    return images


def query_name_candidates(query: str) -> Set[str]:
    """生成用于精确匹配的候选名称集合。"""
    text = str(query or "").strip()
    if not text:
        return set()

    names = {text.lower()}
    try:
        registry, repository, _tag = split_image_reference(text)
    except RegistryConfigError:
        return names

    names.add(repository.lower())
    if registry:
        names.add(f"{registry.lower()}/{repository.lower()}")
    else:
        # Docker Hub 官方镜像习惯上省略 library/ 前缀
        names.add(normalize_repository(repository).lower())
    return names


def _query_repository_text(query: str) -> str:
    """去掉 tag / digest 后的查询文本，用于模糊匹配。"""
    text = str(query or "").strip()
    try:
        _registry, repository, _tag = split_image_reference(text)
        return repository.lower()
    except RegistryConfigError:
        return text.lower()


def find_exact_images(query: str, group_keys: Optional[Sequence[str]] = None) -> List[OfficialImage]:
    """在指定范围内查找名称完全一致的官方镜像。"""
    names = query_name_candidates(query)
    if not names:
        return []
    return [image for image in get_images(group_keys) if image.repo.lower() in names]


def search_images(query: str, group_keys: Optional[Sequence[str]] = None) -> List[OfficialImage]:
    """在指定范围内搜索官方镜像：精确匹配优先，其后为模糊匹配结果。"""
    text = _query_repository_text(query)
    if not text:
        return []

    candidates = get_images(group_keys)
    exact = [image for image in candidates if image.repo.lower() in query_name_candidates(query)]
    exact_repos = {image.repo for image in exact}

    fuzzy = [
        image for image in candidates
        if image.repo not in exact_repos
        and (text in image.repo.lower() or text in image.description.lower())
    ]
    return exact + fuzzy
