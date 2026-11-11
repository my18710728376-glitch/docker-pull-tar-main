"""多镜像仓库配置与解析模块。

集中管理镜像仓库的可选项、配置来源、镜像地址前缀替换以及连通性预检查，
供 Web 界面（app.py）使用。

配置优先级（从高到低）：
    1. 调用方显式指定（Web 界面下拉框）
    2. 环境变量 DOCKER_PULL_REGISTRY（取值可以是仓库别名或仓库地址）
    3. 配置文件中的 default_registry（默认读取项目根目录 registry_config.json，
       可用环境变量 DOCKER_PULL_CONFIG_FILE 指定其它路径）
    4. 内置默认值 Docker Hub（registry-1.docker.io；Web 入口未配置时实际默认 docker.1ms.run）

配置文件格式示例：
    {
        "default_registry": "ghcr",
        "registries": {
            "my-mirror": {"name": "公司内部镜像站", "host": "mirror.example.com"},
            "ghcr": {"name": "GitHub Container Registry", "host": "ghcr.io"}
        }
    }
"""

import json
import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

DEFAULT_CONFIG_FILENAME = "registry_config.json"
DEFAULT_REGISTRY_KEY = "docker"
DEFAULT_REGISTRY_HOST = "registry-1.docker.io"
ENV_REGISTRY_SELECTOR = "DOCKER_PULL_REGISTRY"
ENV_CONFIG_PATH = "DOCKER_PULL_CONFIG_FILE"
CONNECTIVITY_TIMEOUT = 10

# 预置镜像仓库：官方仓库 + 历史版本已支持的加速源
PRESET_REGISTRIES: Dict[str, "RegistryOption"] = {}


class RegistryConfigError(ValueError):
    """镜像仓库配置错误（地址无效、别名不存在、配置文件格式错误等）。"""


class RegistryConnectivityError(ConnectionError):
    """镜像仓库连通性预检查失败。"""


@dataclass(frozen=True)
class RegistryOption:
    """一个可选镜像仓库。"""

    key: str
    name: str
    host: str
    description: str = ""

    @property
    def display_name(self) -> str:
        """界面展示用名称，例如 ``GitHub Container Registry (ghcr.io)``。"""
        return f"{self.name} ({self.host})"


@dataclass(frozen=True)
class ImageTarget:
    """一次拉取的目标镜像信息。"""

    registry: str
    repository: str
    tag: str

    @property
    def reference(self) -> str:
        """完整镜像引用，例如 ``ghcr.io/library/nginx:latest``。"""
        return f"{self.registry}/{self.repository}:{self.tag}"

    @property
    def repo_tag(self) -> str:
        """不带仓库前缀的镜像标签，用于写入 tar 包的 RepoTags。"""
        return f"{self.repository}:{self.tag}"


@dataclass(frozen=True)
class RegistryConfig:
    """解析后的仓库配置。"""

    registries: Dict[str, RegistryOption]
    default_selector: Optional[str] = None
    default_source: str = "builtin"
    config_path: Optional[str] = None

    def default_option(self) -> RegistryOption:
        """返回当前配置下的默认仓库。"""
        return resolve_registry(self.default_selector, self)


def _register_preset(key: str, name: str, host: str, description: str) -> None:
    PRESET_REGISTRIES[key] = RegistryOption(key=key, name=name, host=host, description=description)


_register_preset("docker", "Docker Hub (官方)", "registry-1.docker.io", "默认镜像仓库")
_register_preset("ghcr", "GitHub Container Registry", "ghcr.io", "GitHub 官方容器仓库")
_register_preset("quay", "Quay.io", "quay.io", "Red Hat 官方容器仓库")
_register_preset("k8s", "Kubernetes 官方镜像", "registry.k8s.io", "Kubernetes 新官方仓库")
_register_preset("k8s-gcr", "Kubernetes 旧版镜像", "k8s.gcr.io", "Kubernetes 历史仓库（已冻结）")
_register_preset("gcr", "Google Container Registry", "gcr.io", "Google 官方容器仓库")
_register_preset("gitlab", "GitLab Container Registry", "registry.gitlab.com", "GitLab 官方容器仓库")
_register_preset("mirror-1ms", "1ms 专属加速", "docker.1ms.run", "国内加速源")
_register_preset("mirror-nju", "国内源: 南大", "docker.nju.edu.cn", "南京大学镜像源")


def normalize_registry_host(registry: str) -> str:
    """规范化仓库地址：去掉协议头、首尾空白和结尾斜杠，并做基础校验。"""
    if not registry:
        raise RegistryConfigError("镜像仓库地址不能为空")

    host = str(registry).strip()
    for scheme in ("https://", "http://"):
        if host.lower().startswith(scheme):
            host = host[len(scheme):]
    host = host.strip().rstrip("/")

    if not host:
        raise RegistryConfigError("镜像仓库地址不能为空")
    if "/" in host:
        raise RegistryConfigError(f"镜像仓库地址只需主机名，不要包含路径: {registry}")
    if any(ch.isspace() for ch in host):
        raise RegistryConfigError(f"镜像仓库地址不能包含空格: {registry}")
    return host.lower()


def is_registry_like(selector: str) -> bool:
    """判断一个字符串看起来是仓库地址（含端口、域名或 localhost）。"""
    text = str(selector).strip()
    first_segment = text.split("/", 1)[0]
    return "." in first_segment or ":" in first_segment or first_segment.lower() == "localhost"


def get_default_config_path() -> Path:
    """返回配置文件路径：优先环境变量，其次项目根目录。"""
    env_path = os.environ.get(ENV_CONFIG_PATH)
    if env_path:
        return Path(env_path).expanduser()
    return Path(__file__).resolve().parent / DEFAULT_CONFIG_FILENAME


def _parse_registry_entry(key: str, raw: object) -> RegistryOption:
    """解析配置文件中的单个仓库条目，支持字符串或对象两种写法。"""
    if isinstance(raw, str):
        name, host = key, raw
        description = ""
    elif isinstance(raw, dict):
        host = raw.get("host") or raw.get("registry") or ""
        name = raw.get("name") or key
        description = raw.get("description") or ""
    else:
        raise RegistryConfigError(f"仓库 {key} 的配置格式不正确，应为字符串或对象")

    if not host:
        raise RegistryConfigError(f"仓库 {key} 缺少 host 字段")
    return RegistryOption(
        key=str(key),
        name=str(name),
        host=normalize_registry_host(str(host)),
        description=str(description),
    )


def _read_config_file(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise RegistryConfigError(f"配置文件 {path} 不是合法的 JSON: {exc}") from exc
    except OSError as exc:
        raise RegistryConfigError(f"读取配置文件 {path} 失败: {exc}") from exc

    if not isinstance(data, dict):
        raise RegistryConfigError(f"配置文件 {path} 的根节点必须是对象")
    return data


def load_registry_config(config_path: Optional[str] = None, strict: bool = True) -> RegistryConfig:
    """加载仓库配置。

    参数:
        config_path: 显式指定的配置文件路径，为空时使用环境变量或默认路径。
        strict: 为 True 时配置错误直接抛出；为 False 时忽略配置文件并回退到内置预置项。
    """
    catalog: Dict[str, RegistryOption] = dict(PRESET_REGISTRIES)
    default_selector: Optional[str] = None
    default_source = "builtin"
    resolved_path: Optional[str] = None

    is_explicit_path = config_path is not None or bool(os.environ.get(ENV_CONFIG_PATH))
    path = Path(config_path).expanduser() if config_path else get_default_config_path()

    try:
        if path.exists():
            data = _read_config_file(path)
            for key, raw in (data.get("registries") or {}).items():
                option = _parse_registry_entry(key, raw)
                catalog[str(key)] = option

            file_default = data.get("default_registry")
            if file_default:
                default_selector = str(file_default)
                default_source = "config"
            resolved_path = str(path)
        elif is_explicit_path:
            raise RegistryConfigError(f"配置文件不存在: {path}")
    except RegistryConfigError as exc:
        if strict:
            raise
        warnings.warn(f"忽略无效的镜像仓库配置: {exc}", RuntimeWarning)
        catalog = dict(PRESET_REGISTRIES)
        default_selector = None
        default_source = "builtin"
        resolved_path = None

    env_selector = os.environ.get(ENV_REGISTRY_SELECTOR)
    if env_selector:
        default_selector = env_selector.strip()
        default_source = "env"

    config = RegistryConfig(
        registries=catalog,
        default_selector=default_selector,
        default_source=default_source,
        config_path=resolved_path,
    )

    # 默认仓库可能来自配置文件或环境变量，别名写错时在这里拦截：
    # 非严格模式下告警并回退到内置默认，避免整个应用在导入期启动失败
    if default_selector:
        try:
            resolve_registry(default_selector, config)
        except RegistryConfigError as exc:
            if strict:
                raise
            warnings.warn(f"忽略无效的默认镜像仓库配置: {exc}", RuntimeWarning)
            config = RegistryConfig(
                registries=catalog,
                default_selector=None,
                default_source="builtin",
                config_path=resolved_path,
            )

    return config


def resolve_registry(selector: Optional[str] = None, config: Optional[RegistryConfig] = None) -> RegistryOption:
    """把别名或仓库地址解析为 RegistryOption。

    参数:
        selector: 仓库别名（如 ``ghcr``）或地址（如 ``ghcr.io``）；为空时使用配置默认值。
        config: 已加载的配置，为空时自动加载。
    """
    cfg = config if config is not None else load_registry_config()

    if selector is None or str(selector).strip() == "":
        selector = cfg.default_selector
    if selector is None or str(selector).strip() == "":
        return cfg.registries.get(DEFAULT_REGISTRY_KEY) or PRESET_REGISTRIES[DEFAULT_REGISTRY_KEY]

    text = str(selector).strip()
    for key, option in cfg.registries.items():
        if key.lower() == text.lower():
            return option

    host = normalize_registry_host(text)
    for option in cfg.registries.values():
        if option.host == host:
            return option

    if not is_registry_like(host):
        available = ", ".join(sorted(cfg.registries.keys()))
        raise RegistryConfigError(f"无法识别的镜像仓库: {selector}，可用别名: {available}")

    return RegistryOption(key=host, name=host, host=host, description="自定义镜像仓库")


def list_registry_choices(config: Optional[RegistryConfig] = None) -> List[Tuple[str, str]]:
    """返回下拉框选项列表：``[(展示名称, 仓库地址), ...]``，按地址去重。"""
    cfg = config if config is not None else load_registry_config()
    choices: List[Tuple[str, str]] = []
    seen_hosts = set()
    for option in cfg.registries.values():
        if option.host in seen_hosts:
            continue
        seen_hosts.add(option.host)
        choices.append((option.display_name, option.host))
    return choices


def split_image_reference(image: str) -> Tuple[Optional[str], str, str]:
    """拆分镜像引用为 ``(registry, repository, tag)``。

    registry 为 None 表示镜像引用中未显式写出仓库地址（隐含 Docker Hub）。
    """
    reference = str(image or "").strip()
    if not reference:
        raise RegistryConfigError("镜像名称不能为空")
    if "@" in reference:
        raise RegistryConfigError(f"暂不支持 digest 形式的镜像引用: {image}")

    tag = ""
    last_slash = reference.rfind("/")
    last_colon = reference.rfind(":")
    if last_colon > last_slash:
        tag = reference[last_colon + 1:]
        reference = reference[:last_colon]

    first_separator = reference.find("/")
    if first_separator == -1:
        return None, reference, tag

    first_segment = reference[:first_separator]
    if is_registry_like(first_segment):
        repository = reference[first_separator + 1:]
        if not repository:
            raise RegistryConfigError(f"镜像名称缺少仓库路径: {image}")
        return first_segment, repository, tag

    return None, reference, tag


def normalize_repository(repository: str) -> str:
    """规范化仓库路径：单段名称按 Docker Hub 习惯补全 ``library/``。"""
    repo = str(repository or "").strip().strip("/")
    if not repo:
        raise RegistryConfigError("镜像仓库路径不能为空")
    if "/" not in repo:
        return f"library/{repo}"
    return repo


def build_image_target_from_reference(image: str, target_registry: Optional[str] = None) -> ImageTarget:
    """按目标仓库替换镜像地址前缀，生成拉取目标。

    - 指定 target_registry 时，原镜像的仓库前缀被替换为目标仓库；
    - 未指定时保留镜像自带的仓库前缀，没有前缀则使用 Docker Hub。
    """
    registry, repository, tag = split_image_reference(image)

    if target_registry:
        host = normalize_registry_host(target_registry)
        repository = normalize_repository(repository)
    elif registry:
        host = normalize_registry_host(registry)
        repository = repository.strip("/")
    else:
        host = DEFAULT_REGISTRY_HOST
        repository = normalize_repository(repository)

    return ImageTarget(registry=host, repository=repository, tag=tag or "latest")


def build_image_target(repository: str, tag: str, target_registry: Optional[str] = None) -> ImageTarget:
    """由仓库路径、标签与目标仓库生成拉取目标（Web 界面的搜索结果为仓库路径）。"""
    host = normalize_registry_host(target_registry) if target_registry else DEFAULT_REGISTRY_HOST
    return ImageTarget(registry=host, repository=normalize_repository(repository), tag=(tag or "latest").strip())


def rewrite_image_reference(image: str, target_registry: str) -> str:
    """把镜像地址前缀替换为目标仓库，返回完整镜像引用字符串。"""
    return build_image_target_from_reference(image, target_registry).reference


def check_registry_connectivity(
    session: requests.Session,
    registry: str,
    timeout: int = CONNECTIVITY_TIMEOUT,
) -> str:
    """在拉取前检查镜像仓库 ``/v2/`` 端点是否可访问。

    返回可读的状态描述；无法访问时抛出 ``RegistryConnectivityError``，
    异常信息中包含仓库地址与具体的排障建议。
    """
    host = normalize_registry_host(registry)
    url = f"https://{host}/v2/"

    try:
        response = session.get(url, timeout=timeout)
    except requests.exceptions.SSLError as exc:
        raise RegistryConnectivityError(
            f"无法连接镜像仓库 {host}：SSL/TLS 握手失败（{exc}）。"
            f"若使用代理，请检查代理证书，或临时关闭 SSL 校验后重试。"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise RegistryConnectivityError(
            f"无法连接镜像仓库 {host}：连接超时（{timeout} 秒）。"
            f"请检查网络与代理设置，或更换可用的镜像仓库。"
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise RegistryConnectivityError(
            f"无法连接镜像仓库 {host}：网络不可达（{exc}）。"
            f"请检查仓库地址拼写、DNS/网络与代理设置。"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RegistryConnectivityError(
            f"无法连接镜像仓库 {host}：请求失败（{exc}）。请检查网络与代理设置。"
        ) from exc

    status_code = response.status_code
    if status_code in (200, 401, 403):
        if status_code == 200:
            return f"镜像仓库 {host} 可访问（HTTP 200）"
        if status_code == 401:
            return f"镜像仓库 {host} 可访问（HTTP 401，需要认证）"
        return f"镜像仓库 {host} 可访问（HTTP 403，访问受限）"

    if status_code == 404:
        raise RegistryConnectivityError(
            f"镜像仓库 {host} 的 /v2/ 接口返回 HTTP 404，该地址可能不是有效的 Docker Registry。"
            f"请检查仓库地址或更换镜像仓库。"
        )

    raise RegistryConnectivityError(
        f"镜像仓库 {host} 连通性检查失败：HTTP {status_code}。"
        f"请稍后重试或更换镜像仓库。"
    )
