import gzip
import hashlib
import json
import os
import shutil
import threading
import time
import math  # 新增 math 库用于计算页数
import urllib.request
import warnings
from html import escape as html_escape

warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")
warnings.filterwarnings("ignore", category=UserWarning, module="requests")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List
from pathlib import Path
import gradio as gr

from official_images import (
    DEFAULT_PAGE_SIZE,
    OFFICIAL_IMAGE_GROUPS,
    get_group_choices,
    get_group_names,
    get_images,
    normalize_group_keys,
    query_name_candidates,
    search_images,
)
from registry_config import (
    RegistryConfigError,
    build_image_target,
    build_image_target_from_reference,
    check_registry_connectivity,
    list_registry_choices,
    load_registry_config,
    normalize_repository,
    resolve_registry,
    split_image_reference,
)

# 配置参数
VERSION = "v2.1.0-Gradio7-Web"
DEFAULT_1MS_API = "https://1ms.run/api/v1/registry"
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 120
DOWNLOAD_MAX_RETRIES = 4
BACKOFF_BASE = 0.3
MAX_PARALLEL_LAYERS = 8
# 分组筛选时从在线索引补充的最大条数（结果在本地与内置清单合并后统一分页）
DEFAULT_OFFICIAL_ONLINE_LIMIT = 100

# 支持 Docker 与 OCI 两种清单格式：GHCR 等仓库对纯 OCI 镜像只识别 OCI Accept 头，
# 缺少 OCI 类型时接口会返回 404 MANIFEST_UNKNOWN
MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.oci.image.manifest.v1+json"
)

# 代理只作用在本次请求的 Session 上，不在导入期改写进程环境变量，
# 避免影响同进程中的其它组件（例如同机运行的其他服务）


# --------------------------
# 状态与进度管理 (适配 Web)
# --------------------------
@dataclass
class DownloadStats:
    total_size: int = 0
    downloaded_size: int = 0
    speeds: List[float] = field(default_factory=list)

    def get_avg_speed(self) -> float:
        if not self.speeds: return 0.0
        return sum(self.speeds[-10:]) / len(self.speeds[-10:])

    def format_size(self, size: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024: return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"


class LayerProgress:
    def __init__(self, name: str, total_size: int, index: int, total_layers: int):
        self.name = name
        self.total_size = total_size
        self.downloaded_size = 0
        self.index = index
        self.total_layers = total_layers
        self.status = "waiting"  # waiting, downloading, completed


class WebProgressDisplay:
    def __init__(self):
        self.layers: Dict[str, LayerProgress] = {}
        self.stats = DownloadStats()
        self.lock = threading.Lock()
        self.is_done = False
        self.error_msg = ""
        self.final_path = ""
        self.target_reference = ""

    def add_layer(self, name: str, total_size: int, index: int, total_layers: int):
        with self.lock:
            self.layers[name] = LayerProgress(name, total_size, index, total_layers)

    def update_layer(self, name: str, downloaded: int):
        with self.lock:
            if name in self.layers:
                self.layers[name].downloaded_size = downloaded
                self.layers[name].status = "downloading"

    def update_layer_size(self, name: str, total_size: int):
        with self.lock:
            if name in self.layers and total_size and total_size > 0:
                self.layers[name].total_size = max(self.layers[name].total_size, total_size)

    def complete_layer(self, name: str):
        with self.lock:
            if name in self.layers:
                layer = self.layers[name]
                if layer.total_size == 0:
                    layer.total_size = layer.downloaded_size
                else:
                    layer.downloaded_size = layer.total_size
                layer.status = "completed"

    def get_html_content(self) -> str:
        with self.lock:
            if self.error_msg:
                return f"""
                <div style="padding: 15px; border-radius: 8px; background-color: #ffebee; color: #c62828; font-weight: bold; border: 1px solid #ef5350;">
                    ❌ 发生错误: {html_escape(self.error_msg)}
                </div>
                """

            html = ["""
            <div style="font-family: 'Consolas', 'Courier New', monospace; background-color: #1e1e1e; color: #e0e0e0; padding: 16px; border-radius: 8px; min-height: 250px; font-size: 13px;">
                <div style="margin-bottom: 15px; font-weight: 600; color: #4fc3f7;">🐳 Docker Image Pull Status</div>
            """]

            if self.target_reference:
                html.append(f"""
                <div style="margin-bottom: 15px; color: #b0bec5;">🎯 目标镜像: {html_escape(self.target_reference)}</div>
                """)

            for _, layer in sorted(self.layers.items(), key=lambda x: x[1].index):
                if layer.total_size > 0:
                    progress_pct = (layer.downloaded_size / layer.total_size) * 100
                else:
                    progress_pct = 100.0 if layer.status == "completed" else 0.0

                if layer.status == "completed":
                    icon_color, bar_color, icon = "#4caf50", "#4caf50", "✔"
                elif layer.status == "downloading":
                    icon_color, bar_color, icon = "#29b6f6", "#29b6f6", "⬇"
                else:
                    icon_color, bar_color, icon = "#757575", "#616161", "⏳"

                t_str = self.stats.format_size(layer.total_size) if layer.total_size > 0 else "?"
                c_str = self.stats.format_size(layer.downloaded_size)

                html.append(f"""
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px;">
                    <div style="flex: 0 1 140px; min-width: 92px; color: {icon_color}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                        <span style="display: inline-block; width: 20px;">{icon}</span>
                        ({layer.index}/{layer.total_layers}) {layer.name:<12}
                    </div>
                    <div style="flex: 1 1 auto; min-width: 60px; margin: 0 4px; background-color: #333333; height: 10px; border-radius: 5px; overflow: hidden;">
                        <div style="width: {progress_pct}%; background-color: {bar_color}; height: 100%; transition: width 0.3s ease;"></div>
                    </div>
                    <div style="flex: 0 0 auto; min-width: 104px; padding-left: 8px; text-align: right; color: #b0bec5; white-space: nowrap;">
                        <span style="display:inline-block; width: 50px;">{progress_pct:5.1f}%</span> | {c_str}/{t_str}
                    </div>
                </div>
                """)

            speed = self.stats.get_avg_speed()
            speed_str = self.stats.format_size(int(speed)) if speed > 0 else "0B"

            html.append(f"""
                <hr style="border: none; border-top: 1px dashed #424242; margin: 15px 0;">
                <div style="color: #ffb74d;">📊 预估下载速度: {speed_str}/s</div>
            """)

            if self.is_done:
                html.append(f"""
                    <div style="margin-top: 15px; padding: 15px; background-color: #e8f5e9; color: #1b5e20; border: 1px solid #a5d6a7; border-radius: 6px; font-family: sans-serif;">
                        <div style="font-size: 16px; font-weight: bold; margin-bottom: 10px;">🎉 所有组件下载并打包完成!</div>
                        <div style="margin-bottom: 8px;">
                            📦 <strong>保存路径:</strong> 
                            <code style="background: #ffffff; color: #d81b60; padding: 3px 6px; border-radius: 4px; border: 1px solid #f48fb1; font-family: monospace;">{self.final_path}</code>
                        </div>
                        <div>
                            💡 <strong>导入命令:</strong> 
                            <code style="background: #ffffff; color: #1976d2; padding: 3px 6px; border-radius: 4px; border: 1px solid #90caf9; font-family: monospace;">docker load -i {self.final_path}</code>
                        </div>
                    </div>
                """)

            html.append("</div>")
            return "".join(html)


# --------------------------
# 核心网络与下载逻辑
# --------------------------
def apply_proxy_config(proxy_mode, p_host, p_user, p_pass):
    """按界面选择生成代理字典，不改写进程级环境变量。

    "系统代理" 读取操作系统代理设置；"自定义代理" 使用界面填写的地址；
    "无代理" 返回空字典。
    """
    proxies = {}
    if proxy_mode == "系统代理":
        sys_proxies = urllib.request.getproxies()
        if "http" in sys_proxies:
            proxies["http"] = sys_proxies["http"]
        if "https" in sys_proxies:
            proxies["https"] = sys_proxies["https"]
    elif proxy_mode == "自定义代理":
        auth = f"{p_user}:{p_pass}@" if p_user else ""
        url = f"http://{auth}{p_host}" if p_host else ""
        if url:
            proxies = {"http": url, "https": url}
    return proxies


def get_session(verify_ssl, proxies, trust_env=False) -> requests.Session:
    """创建带重试与连接池的 Session。

    trust_env 为 False 时不读取进程环境变量中的代理，确保界面选择生效。
    """
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=BACKOFF_BASE, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=64, pool_maxsize=128)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.verify = verify_ssl
    session.proxies = proxies
    session.trust_env = trust_env
    return session


def get_auth_head(session, registry, repository):
    try:
        ping_url = f"https://{registry}/v2/"
        resp = session.get(ping_url, timeout=10)
        if resp.status_code == 401 and "WWW-Authenticate" in resp.headers:
            www = resp.headers["WWW-Authenticate"]
            auth_url = www.split('"')[1]
            reg_service = www.split('"')[3]
            token_url = f"{auth_url}?service={reg_service}&scope=repository:{repository}:pull"
            t_resp = session.get(token_url, timeout=10)
            t_resp.raise_for_status()
            access_token = t_resp.json()["token"]
            return {
                "Authorization": f"Bearer {access_token}",
                "Accept": MANIFEST_ACCEPT
            }
    except Exception as e:
        print(f"Auth Ping Fallback: {e}")
    return {"Accept": MANIFEST_ACCEPT}


def download_file_chunked(session, url, headers, save_path, desc, expected_digest, progress: WebProgressDisplay):
    """下载单个 blob，支持断点续传。

    每次重试都会重新读取已落盘大小并重新构造 Range 头：
    若沿用旧偏移追加写入，会让同一段数据重复写进文件导致层损坏。
    服务端忽略 Range 返回 200 全量内容时从零覆盖写入。
    """
    for attempt in range(DOWNLOAD_MAX_RETRIES):
        if progress.error_msg: return False
        resume_pos = os.path.getsize(save_path) if os.path.exists(save_path) else 0
        fetch_headers = headers.copy()
        if resume_pos > 0:
            fetch_headers["Range"] = f"bytes={resume_pos}-"
        try:
            with session.get(url, headers=fetch_headers, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as resp:
                if resp.status_code == 416:
                    progress.complete_layer(desc)
                    return True
                resp.raise_for_status()

                if resume_pos > 0 and resp.status_code != 206:
                    resume_pos = 0

                content_range = resp.headers.get("content-range")
                total_size = int(content_range.split("/")[1]) if content_range else int(
                    resp.headers.get("content-length", 0)) + resume_pos
                progress.update_layer_size(desc, total_size)

                mode = "ab" if resume_pos > 0 else "wb"
                downloaded_size = resume_pos
                last_update = time.time()
                last_size = resume_pos

                with open(save_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if progress.error_msg: return False
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            progress.update_layer(desc, downloaded_size)
                            progress.stats.total_size += len(chunk)

                            now = time.time()
                            if now - last_update >= 0.5:
                                speed = (downloaded_size - last_size) / (now - last_update)
                                progress.stats.speeds.append(speed)
                                last_size = downloaded_size
                                last_update = now
            progress.complete_layer(desc)
            return True
        except Exception as e:
            if attempt < DOWNLOAD_MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE * (2 ** attempt))
                continue
            raise e
    return False


def _split_arch(arch):
    """拆分界面架构选项为 ``(architecture, variant)``。

    界面使用 ``arm/v7`` 这种写法，而 manifest 中对应
    ``architecture="arm"`` + ``variant="v7"`` 两个字段。
    """
    base, _, variant = str(arch or "").partition("/")
    return base, (variant or None)


def pull_image_logic(progress: WebProgressDisplay, registry, repository, tag, arch, output_dir, proxy_args):
    try:
        proxies = apply_proxy_config(*proxy_args[:4])
        session = get_session(proxy_args[4], proxies, trust_env=(proxy_args[0] == "系统代理"))

        # 只有 Docker Hub 才使用 library/ 命名空间；registry.k8s.io 等仓库的
        # 单段仓库名（如 pause）本身合法，补全 library/ 会导致 404
        if "/" not in repository and registry.lower() in DOCKER_HUB_HOSTS:
            repository = f"library/{repository}"
        progress.target_reference = f"{registry}/{repository}:{tag}"

        # 拉取前先验证目标仓库连通性，连接异常时直接给出明确提示
        check_registry_connectivity(session, registry)

        auth_head = get_auth_head(session, registry, repository)

        mani_url = f"https://{registry}/v2/{repository}/manifests/{tag}"
        resp = session.get(mani_url, headers=auth_head, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        if resp.status_code == 404:
            raise ValueError(f"Tag 不存在: {registry}/{repository}:{tag}，请检查 Tag 名称是否正确")
        resp.raise_for_status()
        resp_json = resp.json()

        if resp_json.get("manifests"):
            want_arch, want_variant = _split_arch(arch)
            digest = None
            for m in resp_json["manifests"]:
                platform = m.get("platform") or {}
                m_arch = platform.get("architecture")
                m_variant = platform.get("variant")
                m_os = platform.get("os")
                if m_arch != want_arch or m_os != "linux":
                    continue
                # arm 需要同时匹配 variant（arm/v7 在清单中写作 architecture=arm + variant=v7）
                if want_variant and m_variant and m_variant != want_variant:
                    continue
                digest = m.get("digest")
                break
            if not digest:
                raise ValueError(f"该镜像在 {tag} 下未找到 {arch} 架构。")

            mani_url = f"https://{registry}/v2/{repository}/manifests/{digest}"
            resp = session.get(mani_url, headers=auth_head, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            resp.raise_for_status()
            resp_json = resp.json()

        layers = resp_json.get("layers", [])
        config_digest = resp_json["config"]["digest"]
        config_size = resp_json["config"].get("size", 0)

        safe_repo = repository.replace("/", "_")
        # arm/v7 这类架构名带斜杠，直接拼进路径会生成不存在的子目录
        safe_arch = str(arch).replace("/", "-")
        target_dir = Path(output_dir) / f"{safe_repo}_{tag}_{safe_arch}"
        imgdir = target_dir / "layers"
        imgdir.mkdir(parents=True, exist_ok=True)

        config_path = str(imgdir / f"{config_digest[7:]}.json")
        config_url = f"https://{registry}/v2/{repository}/blobs/{config_digest}"

        progress.add_layer("Config", config_size, 1, len(layers) + 1)

        parentid = ""
        for idx, l in enumerate(layers):
            ublob = l["digest"]
            fake_layerid = hashlib.sha256((parentid + "\n" + ublob + "\n").encode("utf-8")).hexdigest()
            l["fake_layerid"] = fake_layerid
            l["parent_layerid"] = parentid
            parentid = fake_layerid
            progress.add_layer(ublob[:12], l.get("size", 0), idx + 2, len(layers) + 1)

        download_file_chunked(session, config_url, auth_head, config_path, "Config", config_digest, progress)

        def download_worker(layer_obj):
            ublob = layer_obj["digest"]
            url = f"https://{registry}/v2/{repository}/blobs/{ublob}"
            fake_layerid = layer_obj["fake_layerid"]
            ldir = imgdir / fake_layerid
            ldir.mkdir(parents=True, exist_ok=True)

            save_path = str(ldir / "layer_gzip.tar")
            success = download_file_chunked(session, url, auth_head, save_path, ublob[:12], ublob, progress)
            return success, fake_layerid, ldir

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_LAYERS) as pool:
            futures = [pool.submit(download_worker, l) for l in layers]
            for fut in as_completed(futures):
                try:
                    suc, fid, ldir = fut.result()
                    if not suc:
                        raise Exception("分片网络下载失败")
                except Exception as e:
                    progress.error_msg = str(e)
                    return

        content = [{"Config": f"{config_digest[7:]}.json", "RepoTags": [f"{repository}:{tag}"], "Layers": []}]

        for l in layers:
            fake_layerid = l["fake_layerid"]
            parent_layerid = l["parent_layerid"]
            ldir = imgdir / fake_layerid
            gz_path = ldir / "layer_gzip.tar"
            tar_path = ldir / "layer.tar"

            if gz_path.exists():
                with gzip.open(gz_path, "rb") as gz, open(tar_path, "wb") as file:
                    shutil.copyfileobj(gz, file)
                os.remove(gz_path)

            with open(ldir / "json", "w") as f:
                json.dump({"id": fake_layerid, "parent": parent_layerid if parent_layerid else None}, f)

            content[0]["Layers"].append(f"{fake_layerid}/layer.tar")

        with open(imgdir / "manifest.json", "w") as f:
            json.dump(content, f)
        with open(imgdir / "repositories", "w") as f:
            json.dump({repository: {tag: layers[-1]["fake_layerid"]}}, f)

        tar_final = Path(output_dir) / f"{safe_repo}_{tag}_{safe_arch}.tar"
        # 先写入 .part 再原子改名，避免打包中断时留下半成品 tar 被用户误 load
        tar_tmp = tar_final.with_suffix(".tar.part")
        with tarfile.open(str(tar_tmp), "w") as tar:
            tar.add(str(imgdir), arcname="/")
        os.replace(str(tar_tmp), str(tar_final))

        shutil.rmtree(str(imgdir), ignore_errors=True)

        progress.final_path = str(tar_final.absolute())
        progress.is_done = True

    except Exception as e:
        import traceback
        traceback.print_exc()
        progress.error_msg = str(e)


# --------------------------
# UI 交互函数与文件管理
# --------------------------
def get_downloaded_tars(out_dir):
    if not out_dir: out_dir = "downloads"
    os.makedirs(out_dir, exist_ok=True)
    files = []
    try:
        for f in os.listdir(out_dir):
            if f.endswith(".tar"):
                files.append(os.path.join(out_dir, f))
        files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    except Exception:
        pass

    choices = [os.path.basename(f) for f in files]
    return files, gr.update(choices=choices, value=None)


def delete_local_tar(filename, out_dir):
    if filename:
        filepath = os.path.join(out_dir, filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                gr.Info(f"🗑️ 成功删除: {filename}")
            except Exception as e:
                gr.Warning(f"删除失败: {e}")
    return get_downloaded_tars(out_dir)


# 🌟 新增：带分页的核心搜索函数
def _registry_in_scope(host, allowed_hosts):
    """判断在线结果所属仓库是否落在所选分组范围内。"""
    host = str(host or "").lower()
    if host in allowed_hosts:
        return True
    # 1ms 索引把 Docker Hub 记为 docker.io，预置分组使用 registry-1.docker.io
    return host in DOCKER_HUB_HOSTS and any(h in DOCKER_HUB_HOSTS for h in allowed_hosts)


def _rows_from_api_items(items, allowed_hosts=None):
    """把 1ms 搜索接口返回的条目转换为表格行。

    allowed_hosts 非空时只保留属于这些仓库的条目，用于分组筛选时的范围约束。
    """
    rows = []
    for it in items:
        registry = str(it.get("registry") or "docker.io").lower()
        if allowed_hosts and not _registry_in_scope(registry, allowed_hosts):
            continue
        ns = it.get("namespace", "library")
        name = it.get("name", "")
        repo = f"{ns}/{name}" if ns else name
        rows.append([
            repo,
            it.get("star_count", 0),
            it.get("pull_count", 0),
            it.get("last_modified", "")[:10],
            (it.get("description") or "").replace("\n", "")[:50]
        ])
    return rows


def _rows_from_official_images(images):
    """把内置官方镜像条目转换为表格行。"""
    return [[image.repo, 0, 0, "", image.description[:50]] for image in images]


DOCKER_HUB_HOSTS = ("docker.io", "registry-1.docker.io", "index.docker.io")
GHCR_HOST = "ghcr.io"


def _registry_access_token(session, registry, repository):
    """获取匿名拉取 token。

    返回 None 表示无法获取（网络异常或非 Bearer 认证）；空字符串表示无需认证即可访问。
    """
    try:
        ping = session.get(f"https://{registry}/v2/", timeout=10)
    except Exception:
        return None

    if ping.status_code == 200:
        return ""
    if ping.status_code != 401:
        return None

    header = ping.headers.get("WWW-Authenticate") or ping.headers.get("www-authenticate") or ""
    if "Bearer" not in header or header.count('"') < 4:
        return None

    realm = header.split('"')[1]
    service = header.split('"')[3]
    try:
        resp = session.get(f"{realm}?service={service}&scope=repository:{repository}:pull", timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None
    return data.get("token") or data.get("access_token")


def _is_signature_tag(tag):
    """判断是否为签名/证明类 tag（registry.k8s.io 会返回大量 sha256-*.sig）。"""
    text = str(tag or "").strip().lower()
    if not text:
        return True
    return text.startswith("sha256-") or text.endswith((".sig", ".att", ".sbom"))


def _is_stable_version_tag(tag):
    """判断是否为不含预发布后缀的稳定版本号，如 v1.30.0 / 1.30。"""
    core = str(tag).strip()
    if core.lower().startswith("v"):
        core = core[1:]
    parts = core.split(".")
    return 2 <= len(parts) <= 3 and all(part.isdigit() for part in parts)


def _tag_version_key(tag):
    """解析版本 tag 为可比较的元组，非版本 tag 返回 None。"""
    core = str(tag).strip()
    if core.lower().startswith("v"):
        core = core[1:]
    parts = core.split("-", 1)[0].split(".")
    if not 2 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts) + (0,) * (3 - len(parts))


def fetch_remote_tags(session, registry, repository, limit=100):
    """直接通过 Registry API 读取公开仓库的标签列表，失败时返回空列表。

    签名类 tag 会在截断到 limit 之前被剔除，否则 registry.k8s.io 等仓库
    靠前的上百个 sha256-*.sig 会把真正的版本 tag 全部挤出下拉框；
    标签数量超过 limit 时按语义版本降序保留，避免只留下字典序靠前的旧版本。
    """
    token = _registry_access_token(session, registry, repository)
    if token is None:
        return []

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = session.get(f"https://{registry}/v2/{repository}/tags/list", headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        tags = [
            tag for tag in (resp.json().get("tags") or [])
            if tag and not _is_signature_tag(tag)
        ]
    except Exception:
        return []

    if len(tags) > limit:
        latest = [tag for tag in tags if tag == "latest"]
        rest = [tag for tag in tags if tag != "latest"]
        versioned = sorted(
            (tag for tag in rest if _tag_version_key(tag) is not None),
            key=lambda tag: (_tag_version_key(tag), _is_stable_version_tag(tag), tag),
            reverse=True,
        )
        plain = [tag for tag in rest if _tag_version_key(tag) is None]
        tags = (latest + versioned + plain)[:limit]
    return tags[:limit]


def probe_public_repository(session, registry, repository):
    """匿名读取标签以确认仓库存在且对外公开。"""
    return bool(fetch_remote_tags(session, registry, repository, limit=1))


def _looks_like_repository(keyword):
    """判断输入是否像完整的仓库名（含命名空间且不含空白）。"""
    text = keyword.strip()
    return "/" in text and not any(ch.isspace() for ch in text)


def resolve_exact_result(keyword, session):
    """当输入是完整仓库名时返回精确匹配结果行，否则返回 None。"""
    text = keyword.strip()
    if not _looks_like_repository(text):
        return None

    try:
        registry, repository, _tag = split_image_reference(text)
    except RegistryConfigError:
        return None

    if registry and registry.lower() not in DOCKER_HUB_HOSTS:
        # 非 Docker Hub 的完整引用无法在线校验元数据，直接生成精确匹配结果
        return [f"{registry}/{repository}", 0, 0, "", "精确匹配（非 Docker Hub 镜像）"]

    repo = normalize_repository(repository)
    try:
        resp = session.get(f"https://hub.docker.com/v2/repositories/{repo}/", timeout=10)
        if resp.status_code == 200:
            item = resp.json()
            repo_name = "/".join(part for part in (item.get("namespace"), item.get("name")) if part) or repo
            return [
                repo_name,
                item.get("star_count", 0),
                item.get("pull_count", 0),
                (item.get("last_updated") or "")[:10],
                (item.get("description") or "").replace("\n", "")[:50],
            ]
    except Exception:
        pass

    # Docker Hub 未收录时探测 GitHub Container Registry 上的同名公开仓库
    if probe_public_repository(session, GHCR_HOST, repo):
        return [f"{GHCR_HOST}/{repo}", 0, 0, "", f"精确匹配（{GHCR_HOST}）"]
    return None


def _prepend_exact_result(keyword, session, rows, total):
    """将精确匹配结果置于首位：已存在则前移，不存在则新增并累加总数。"""
    exact_row = resolve_exact_result(keyword, session)
    if not exact_row:
        # 无法获取元数据时，仍把已出现在模糊结果中的同名仓库前移
        candidates = query_name_candidates(keyword)
        for index, row in enumerate(rows):
            if row[0].lower() in candidates:
                rows.pop(index)
                rows.insert(0, row)
                break
        return rows, total

    for index, row in enumerate(rows):
        if row[0].lower() == exact_row[0].lower():
            rows.pop(index)
            rows.insert(0, exact_row)
            return rows, total

    rows.insert(0, exact_row)
    return rows, total + 1


def _local_index_rows(keyword):
    """内置官方清单中可补充到全局搜索的非 Docker Hub 镜像行。

    在线 1ms 索引只覆盖 Docker Hub，因此只补充带仓库前缀的静态条目；
    带路径的完整引用交给 resolve_exact_result() 处理，避免把
    ``kite-org/kite`` 之类的关键词误匹配到其它仓库的同名镜像。
    """
    try:
        _registry, repository, _tag = split_image_reference(keyword)
    except RegistryConfigError:
        return []
    text = repository.strip().lower()
    if not text or "/" in text or any(ch.isspace() for ch in text):
        return []

    rows = []
    for image in get_images():
        try:
            host, repo_path, _ = split_image_reference(image.repo)
        except RegistryConfigError:
            continue
        if not host or host.lower() in DOCKER_HUB_HOSTS:
            continue
        if text in repo_path.rsplit("/", 1)[-1].lower():
            rows.append([image.repo, 0, 0, "", image.description[:50]])

    rows.sort(key=lambda row: (row[0].rsplit("/", 1)[-1].lower() != text, row[0].lower()))
    return rows


def _merge_local_index(keyword, rows, total):
    """把内置清单命中项并入在线结果，去重后置顶并返回新增数量。"""
    local_rows = _local_index_rows(keyword)
    if not local_rows:
        return rows, total, 0
    existing = {row[0].lower() for row in rows}
    added = [row for row in local_rows if row[0].lower() not in existing]
    if not added:
        return rows, total, 0
    return added + rows, total + len(added), len(added)


def execute_official_search(keyword, target_page, official_keys, session):
    """在所选分组内搜索：内置清单 + 在线索引中同仓库范围的结果。

    内置清单保证官方镜像的精确匹配优先，在线索引覆盖清单之外的镜像；
    在线结果按分组对应的仓库 host 过滤，避免混入其它仓库。
    """
    scope = "、".join(get_group_names(official_keys))
    rows = _rows_from_official_images(search_images(keyword, official_keys))
    allowed_hosts = {OFFICIAL_IMAGE_GROUPS[key].host.lower() for key in official_keys}

    online_error = None
    try:
        resp = session.get(f"{DEFAULT_1MS_API}/search",
                           params={"query": keyword, "page": 1,
                                   "page_size": DEFAULT_OFFICIAL_ONLINE_LIMIT})
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("list", [])
    except Exception as exc:
        online_error = exc
        items = []

    seen = {row[0].lower() for row in rows}
    added = 0
    for row in _rows_from_api_items(items, allowed_hosts):
        if row[0].lower() in seen:
            continue
        seen.add(row[0].lower())
        rows.append(row)
        added += 1

    if not rows:
        offline_note = "（在线索引不可用）" if online_error is not None else ""
        html_info = (f"<div style='text-align:center; padding-top:8px; color:#e53935; font-size:14px;'>"
                     f"未在所选官方镜像集合（{scope}）中匹配到结果{offline_note}</div>")
        return [], 1, html_info, gr.update(interactive=False), gr.update(interactive=False)

    total = len(rows)
    total_pages = max(1, math.ceil(total / DEFAULT_PAGE_SIZE))
    actual_page = min(max(1, target_page), total_pages)
    start = (actual_page - 1) * DEFAULT_PAGE_SIZE
    page_rows = rows[start:start + DEFAULT_PAGE_SIZE]

    note = ""
    if online_error is not None:
        note = " · 在线索引不可用，仅显示内置清单"
    elif added:
        note = f" · 含在线结果 {added} 个"

    html_info = (f"<div style='text-align:center; padding-top:8px; color:#555; font-size:14px;'>"
                 f"第 <b>{actual_page}</b> / {total_pages} 页 (共 {total} 个结果 · 范围: {scope}){note}</div>")

    return (page_rows, actual_page, html_info,
            gr.update(interactive=actual_page > 1), gr.update(interactive=actual_page < total_pages))


def execute_search(keyword, target_page, p_mode, p_host, p_user, p_pass, vSSL, official_keys=None):
    """执行搜索：未选分组时合并在线索引与内置清单；选中时限定到对应仓库范围。"""
    if not keyword:
        return [], 1, "<div style='text-align:center;'>请先输入关键词</div>", gr.update(interactive=False), gr.update(
            interactive=False)

    proxies = apply_proxy_config(p_mode, p_host, p_user, p_pass)
    session = get_session(vSSL, proxies, trust_env=(p_mode == "系统代理"))

    official_keys = normalize_group_keys(official_keys)
    if official_keys:
        return execute_official_search(keyword, target_page, official_keys, session)

    online_error = None
    try:
        resp = session.get(f"{DEFAULT_1MS_API}/search",
                           params={"query": keyword, "page": target_page, "page_size": DEFAULT_PAGE_SIZE})
        resp.raise_for_status()
        data = resp.json().get("data", {})
        items = data.get("list", [])
        total = int(data.get("total", 0) or 0)
        res = _rows_from_api_items(items)
    except Exception as exc:
        # 在线索引不可用时降级到内置清单，保证离线环境仍可检索官方镜像
        online_error = exc
        res, total = [], 0
        target_page = 1

    # 分页页数以在线索引的可取页数为准：本地内置结果与精确匹配只并入第 1 页，
    # 若按含本地条数的总数计算页数，末页会向在线接口取到空数据
    online_total = total
    added = 0
    if target_page == 1:
        res, total, added = _merge_local_index(keyword, res, total)
        # 精确全匹配：完整仓库名优先置顶，其余结果保持不变
        res, total = _prepend_exact_result(keyword, session, res, total)

    total_pages = max(1, math.ceil(online_total / DEFAULT_PAGE_SIZE))
    actual_page = min(max(1, target_page), total_pages)

    note = ""
    if online_error is not None:
        note = (f" · <span style='color:#e67e22;'>在线索引不可用（{type(online_error).__name__}），"
                f"仅显示内置官方镜像与精确匹配结果</span>")
    elif added:
        note = f" · 含内置官方镜像 {added} 个"

    html_info = f"<div style='text-align:center; padding-top:8px; color:#555; font-size:14px;'>第 <b>{actual_page}</b> / {total_pages} 页 (共 {total} 个结果){note}</div>"

    can_prev = actual_page > 1
    can_next = actual_page < total_pages

    return res, actual_page, html_info, gr.update(interactive=can_prev), gr.update(interactive=can_next)


def fn_get_tags(selected_repo, p_mode, p_host, p_user, p_pass, vSSL):
    proxies = apply_proxy_config(p_mode, p_host, p_user, p_pass)
    session = get_session(vSSL, proxies, trust_env=(p_mode == "系统代理"))

    # 镜像自带仓库前缀时（如 ghcr.io/kite-org/kite）直接从该仓库读取标签
    try:
        registry, repository, _tag = split_image_reference(selected_repo)
    except RegistryConfigError:
        registry, repository = None, selected_repo
    if registry and registry.lower() not in DOCKER_HUB_HOSTS:
        tags = fetch_remote_tags(session, registry, repository)
        if tags:
            default = "latest" if "latest" in tags else next(
                (tag for tag in tags if _is_stable_version_tag(tag)), tags[0])
            return gr.update(choices=tags, value=default, interactive=True)
        return gr.update(choices=["latest"], value="latest", interactive=True)

    try:
        resp = session.get(f"{DEFAULT_1MS_API}/get_tags",
                           params={"repositories": selected_repo, "page": 1, "page_size": 100})
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("list", [])
        tags = [it.get("tag_name") for it in items if it.get("tag_name")]
        if not tags: tags = ["latest"]
        # 列表里没有 latest 时取第一个标签作为默认值，避免下拉框初值不在选项中
        default = "latest" if "latest" in tags else tags[0]
        return gr.update(choices=tags, value=default, interactive=True)
    except Exception:
        return gr.update(choices=["latest"], value="latest", interactive=True)


def resolve_pull_target(repo, tag, registry):
    """计算实际拉取目标。

    镜像名自带仓库前缀（如 registry.k8s.io/pause）时优先使用自身仓库；
    否则使用界面所选的镜像仓库并替换前缀。
    """
    tag = tag or "latest"
    explicit_registry, _repository, _tag = split_image_reference(f"{repo}:{tag}")
    if explicit_registry:
        return build_image_target_from_reference(f"{repo}:{tag}")

    option = resolve_registry(registry)
    return build_image_target(repository=repo, tag=tag, target_registry=option.host)


def fn_download_manager(repo, tag, arch, registry, out_dir, p_mode, p_host, p_user, p_pass, vSSL):
    if not repo:
        yield '<div style="color:red;font-weight:bold;">❌ 请先在上方搜索并在表格中点击选择一个镜像！</div>', gr.skip(), gr.skip()
        return

    # Tag 支持下拉选择与手动输入，先做基础校验，避免把空值或非法字符拼进请求地址
    tag = str(tag or "").strip()
    if not tag:
        yield '<div style="color:red;font-weight:bold;">❌ 请先选择或输入镜像 Tag！</div>', gr.skip(), gr.skip()
        return
    if ":" in tag or any(ch.isspace() for ch in tag) or len(tag) > 128:
        yield ('<div style="color:red;font-weight:bold;">❌ Tag 格式不正确：'
               '不能包含冒号或空格，长度不能超过 128 个字符</div>'), gr.skip(), gr.skip()
        return

    # 解析所选仓库并把镜像地址前缀替换为目标仓库
    try:
        target = resolve_pull_target(repo, tag, registry)
    except RegistryConfigError as exc:
        yield f'<div style="color:red;font-weight:bold;">❌ 镜像仓库配置错误: {exc}</div>', gr.skip(), gr.skip()
        return

    yield '<div style="color:#666;font-family:monospace;padding:10px;">🕒 初始化下载任务中，请稍候...</div>', gr.skip(), gr.skip()

    proxy_args = (p_mode, p_host, p_user, p_pass, vSSL)

    progress = WebProgressDisplay()

    t = threading.Thread(target=pull_image_logic,
                         args=(progress, target.registry, target.repository, target.tag, arch, out_dir, proxy_args))
    t.start()

    while t.is_alive():
        time.sleep(0.5)
        yield progress.get_html_content(), gr.skip(), gr.skip()

    files, drop_update = get_downloaded_tars(out_dir)
    yield progress.get_html_content(), files, drop_update


# --------------------------
# Gradio 界面设计
# --------------------------
# 加载镜像仓库配置：支持 registry_config.json 与 DOCKER_PULL_REGISTRY 环境变量
REGISTRY_CONFIG = load_registry_config(strict=False)
REGISTRY_CHOICES = list_registry_choices(REGISTRY_CONFIG)

if REGISTRY_CONFIG.default_source in ("config", "env"):
    DEFAULT_REGISTRY = REGISTRY_CONFIG.default_option().host
else:
    # 未显式配置时保持 Web 版历史默认（1ms 加速源）
    DEFAULT_REGISTRY = resolve_registry("mirror-1ms", REGISTRY_CONFIG).host

if DEFAULT_REGISTRY not in [host for _, host in REGISTRY_CHOICES]:
    REGISTRY_CHOICES.append((DEFAULT_REGISTRY, DEFAULT_REGISTRY))

# 布局样式：统一卡片层级、间距与响应式断点（Gradio 6 需通过 launch(css=...) 注入）
CUSTOM_CSS = """
:root {
  --dp-gap: 16px;
  --dp-radius: 14px;
  --dp-line: var(--border-color-primary, rgba(15, 23, 42, .12));
  --dp-surface: var(--background-fill-primary, #ffffff);
  --dp-muted: var(--body-text-color-subdued, #64748b);
}
html, body { overflow-x: hidden; }
.gradio-container { width: 100% !important; max-width: 1360px !important; margin-inline: auto !important; }
#dp-header {
  padding: 20px 22px !important; margin-bottom: var(--dp-gap) !important;
  border: 1px solid var(--dp-line) !important; border-radius: var(--dp-radius) !important;
  background: linear-gradient(115deg, #eff6ff 0%, var(--dp-surface) 72%) !important;
}
#dp-header h1 {
  margin: 0 0 6px !important; font-size: clamp(20px, 2.4vw, 28px) !important;
  line-height: 1.3 !important; overflow-wrap: anywhere;
}
#dp-header p { margin: 0 !important; color: var(--dp-muted) !important; font-size: 14px !important; overflow-wrap: anywhere; }

.dp-panel {
  gap: 12px !important; padding: 18px 20px !important; margin-bottom: var(--dp-gap) !important;
  min-width: 0 !important; max-width: 100% !important;
  border: 1px solid var(--dp-line) !important; border-radius: var(--dp-radius) !important;
  background: var(--dp-surface) !important; box-shadow: 0 2px 12px rgba(15, 23, 42, .045) !important;
}
.dp-panel > *, .dp-subpanel > * { min-width: 0 !important; max-width: 100% !important; }
.dp-step-title h3 { margin: 0 !important; font-size: 17px !important; font-weight: 650 !important; }
.dp-hint, .dp-hint p, .dp-hint strong, .dp-footer, .dp-footer p {
  margin: 0 !important; color: var(--dp-muted) !important; font-size: 13px !important;
}
.dp-subpanel {
  gap: 10px !important; padding: 14px !important; min-width: 0 !important; max-width: 100% !important;
  border: 1px dashed var(--dp-line) !important; border-radius: 10px !important;
}

#dp-settings-row > *, #dp-search-row > *, #dp-params-row > * { min-width: 0 !important; }
#dp-search-row button { min-width: 116px; }
#dp-results {
  overflow-x: auto !important; width: 100% !important;
  max-width: 100% !important; min-width: 0 !important;
}
#dp-pager {
  display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important;
  align-items: center; gap: 12px;
}
#dp-pager > * { flex: 0 0 auto !important; min-width: 0 !important; }
#dp-page-info { flex: 1 1 auto !important; min-width: 0 !important; }
#dp-page-info > div { text-align: center !important; }
#dp-pager button { min-width: 104px; width: 104px; }
#dp-download-btn, #dp-download-btn button, #dp-file-actions button { width: 100%; }
#dp-file-actions { gap: 10px !important; }
.dp-empty {
  padding: 28px 12px; color: #94a3b8; font-size: 14px; text-align: center;
  border: 1px dashed #cbd5e1; border-radius: 10px;
}

@media (max-width: 1200px) {
  #dp-settings-row, #dp-search-row, #dp-download-row, #dp-files-row {
    display: flex !important; flex-direction: column !important; flex-wrap: wrap !important;
    grid-template-columns: 1fr !important;
  }
  #dp-settings-row > *, #dp-search-row > *, #dp-download-row > *, #dp-files-row > * {
    flex: 1 1 100% !important; width: 100% !important; max-width: 100% !important; min-width: 0 !important;
  }
}
@media (max-width: 768px) {
  .gradio-container { padding-inline: 10px !important; }
  #dp-header { padding: 14px !important; }
  .dp-panel { padding: 14px !important; }
  #dp-params-row { flex-wrap: wrap !important; }
  #dp-pager { flex-wrap: wrap !important; }
  #dp-page-info { flex: 1 1 100% !important; order: -1; }
  #dp-pager button { flex: 1 1 40% !important; width: auto; min-width: 104px; }
  .dp-footer { text-align: left !important; }
}
"""

with gr.Blocks(title="Docker 镜像离线下载工具", fill_width=True) as demo:
    # 隐藏的 State：保存当前分页对应的关键词、官方镜像筛选条件与页码
    current_kw_state = gr.State("")
    current_groups_state = gr.State([])
    current_page_state = gr.State(1)

    # L0 页头：标题、版本与一句话任务说明
    gr.Markdown(
        f"# 🐳 Docker 镜像快捷离线拉取器 · {VERSION}\n"
        "搜索镜像 → 选择版本与架构 → 导出 `.tar` 包供内网 `docker load`。",
        elem_id="dp-header",
    )

    # L1 全局设置：作用于下方全部任务
    with gr.Accordion("⚙️ 全局设置 · 保存路径 / 镜像仓库 / 网络", open=False, elem_id="dp-settings"):
        with gr.Row(elem_id="dp-settings-row", equal_height=True):
            out_dir_ui = gr.Textbox(label="📦 镜像保存目录", value="downloads",
                                    info="默认下载到当前文件夹的 downloads 目录下", scale=2)
            registry_ui = gr.Dropdown(
                label="🌐 镜像仓库",
                choices=REGISTRY_CHOICES,
                value=DEFAULT_REGISTRY,
                info="支持 Docker Hub、GHCR、Quay、K8s、GCR、GitLab 及自定义仓库（可通过 registry_config.json 或 DOCKER_PULL_REGISTRY 扩展）",
                scale=3
            )
            ssl_ui = gr.Checkbox(label="🔐 开启 SSL 证书验证", value=True,
                                 info="如果遇到代理证书问题可取消勾选", scale=1)

        with gr.Row(elem_id="dp-proxy-row"):
            proxy_mode_ui = gr.Radio(label="📡 代理模式", choices=["无代理", "系统代理", "自定义代理"],
                                     value="无代理")
        with gr.Row(visible=False, elem_id="dp-proxy-custom") as custom_proxy_row:
            proxy_host_ui = gr.Textbox(label="代理地址 (例如 127.0.0.1:7890)")
            proxy_user_ui = gr.Textbox(label="代理用户名 (可选)")
            proxy_pass_ui = gr.Textbox(label="代理密码 (可选)", type="password")

    def toggle_proxy_visibility(mode):
        return gr.update(visible=(mode == "自定义代理"))

    proxy_mode_ui.change(fn=toggle_proxy_visibility, inputs=[proxy_mode_ui], outputs=[custom_proxy_row])

    # L2 任务流：① 查 → ② 选/下载 → ③ 管理，纵向排列保证阅读与操作顺序
    with gr.Column(elem_classes=["dp-panel"], elem_id="dp-search-panel"):
        gr.Markdown("### ① 查镜像", elem_classes=["dp-step-title"])
        gr.Markdown("搜索后点击表格中任意一行，即可载入该镜像的 Tag 与架构。", elem_classes=["dp-hint"])

        with gr.Row(elem_id="dp-search-row"):
            kw_ui = gr.Textbox(label="镜像关键词",
                               placeholder="eg: nginx, redis, registry.k8s.io/kubectl", scale=4)
            official_ui = gr.Dropdown(
                label="官方镜像筛选（可多选）",
                choices=get_group_choices(),
                multiselect=True,
                value=[],
                info="不选=全网搜索并合并内置官方索引；选中后仅在所选集合内检索",
                scale=3
            )
            search_btn = gr.Button("🔍 搜索", variant="primary", scale=1)

        df_results = gr.Dataframe(
            headers=["镜像名称 (Repo)", "⭐ Stars", "⬇️ Pulls", "更新日期", "描述"],
            interactive=False, wrap=True, type="array", max_height=360,
            elem_id="dp-results"
        )

        with gr.Row(elem_id="dp-pager"):
            prev_btn = gr.Button("◀ 上一页", interactive=False, size="sm")
            page_info_ui = gr.HTML(
                "<div class='dp-empty' style='padding:10px'>等待搜索...</div>",
                elem_id="dp-page-info"
            )
            next_btn = gr.Button("下一页 ▶", interactive=False, size="sm")

    with gr.Column(elem_classes=["dp-panel"], elem_id="dp-download-panel"):
        gr.Markdown("### ② 选择并下载", elem_classes=["dp-step-title"])
        gr.Markdown("左侧确认目标与参数，右侧实时查看分层下载进度。", elem_classes=["dp-hint"])

        with gr.Row(elem_id="dp-download-row", equal_height=True):
            with gr.Column(scale=2, elem_classes=["dp-subpanel"], elem_id="dp-select-col"):
                selected_repo_ui = gr.Textbox(label="当前选中目标",
                                              placeholder="请先在上方表格中点击选择一项…",
                                              interactive=False)
                with gr.Row(elem_id="dp-params-row"):
                    tag_ui = gr.Dropdown(label="Tag", choices=["latest"], value="latest",
                                         allow_custom_value=True, interactive=False, scale=2,
                                         info="可从下拉选择，也可在选中镜像后直接输入自定义 Tag")
                    arch_ui = gr.Dropdown(label="架构 Arch",
                                          choices=["amd64", "arm64", "arm/v7", "s390x", "ppc64le"],
                                          value="amd64", scale=1)
                dl_btn = gr.Button("⬇️ 开始下载 & 导出", variant="primary", size="lg",
                                   elem_id="dp-download-btn")

            with gr.Column(scale=3, elem_classes=["dp-subpanel"], elem_id="dp-progress-col"):
                log_box = gr.HTML(
                    value="<div class='dp-empty'>等待开始下载…</div>",
                    elem_id="dp-progress"
                )

    with gr.Column(elem_classes=["dp-panel"], elem_id="dp-files-panel"):
        gr.Markdown("### ③ 本地镜像包管理", elem_classes=["dp-step-title"])
        gr.Markdown("下载完成的 `.tar` 会显示在这里，可点击文件名下载到本机或删除释放空间。",
                    elem_classes=["dp-hint"])

        with gr.Row(elem_id="dp-files-row", equal_height=True):
            with gr.Column(scale=3):
                file_list_ui = gr.File(
                    label="📦 已下载的镜像包 (.tar)",
                    file_count="multiple",
                    interactive=False
                )
            with gr.Column(scale=1, elem_id="dp-file-actions"):
                refresh_btn = gr.Button("🔄 刷新本地列表", variant="secondary")
                delete_dropdown = gr.Dropdown(label="🗑️ 选择要删除的文件")
                delete_btn = gr.Button("❌ 删除选中文件", variant="stop")

    gr.Markdown("提示：「① 查镜像」的选中结果驱动「② 选择并下载」，下载完成后自动刷新「③ 本地镜像包管理」。",
                elem_classes=["dp-footer"])


    # --------------------------
    # 事件绑定逻辑
    # --------------------------

    # 🌟 修改：绑定首次搜索事件（重置为第1页），同步保存关键词与官方镜像筛选范围
    def on_initial_search(kw, official_keys, p_mode, p_host, p_user, p_pass, vSSL):
        groups = normalize_group_keys(official_keys)
        res, actual_page, html_info, can_prev, can_next = execute_search(
            kw, 1, p_mode, p_host, p_user, p_pass, vSSL, groups)
        return kw, groups, res, actual_page, html_info, can_prev, can_next


    search_btn.click(
        fn=on_initial_search,
        inputs=[kw_ui, official_ui, proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui, ssl_ui],
        outputs=[current_kw_state, current_groups_state, df_results, current_page_state, page_info_ui, prev_btn,
                 next_btn]
    )


    # 🌟 新增：绑定上一页与下一页事件（基于保存的 State 进行操作）
    def on_prev_page(saved_kw, saved_groups, current_page, p_mode, p_host, p_user, p_pass, vSSL):
        res, actual_page, html_info, can_prev, can_next = execute_search(
            saved_kw, current_page - 1, p_mode, p_host, p_user, p_pass, vSSL, saved_groups)
        return res, actual_page, html_info, can_prev, can_next


    def on_next_page(saved_kw, saved_groups, current_page, p_mode, p_host, p_user, p_pass, vSSL):
        res, actual_page, html_info, can_prev, can_next = execute_search(
            saved_kw, current_page + 1, p_mode, p_host, p_user, p_pass, vSSL, saved_groups)
        return res, actual_page, html_info, can_prev, can_next


    prev_btn.click(
        fn=on_prev_page,
        inputs=[current_kw_state, current_groups_state, current_page_state,
                proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui, ssl_ui],
        outputs=[df_results, current_page_state, page_info_ui, prev_btn, next_btn]
    )

    next_btn.click(
        fn=on_next_page,
        inputs=[current_kw_state, current_groups_state, current_page_state,
                proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui, ssl_ui],
        outputs=[df_results, current_page_state, page_info_ui, prev_btn, next_btn]
    )


    def on_df_select(evt: gr.SelectData, current_df, p_mode, p_host, p_user, p_pass, vSSL):
        try:
            row_idx = evt.index[0]
            if isinstance(current_df, dict) and "data" in current_df:
                repo_name = current_df["data"][row_idx][0]
            elif hasattr(current_df, "iloc"):
                repo_name = current_df.iloc[row_idx, 0]
            else:
                repo_name = current_df[row_idx][0]
            tag_update = fn_get_tags(repo_name, p_mode, p_host, p_user, p_pass, vSSL)
            return repo_name, tag_update
        except Exception as e:
            import traceback
            traceback.print_exc()
            return "", gr.update(choices=["latest"], value="latest", interactive=True)


    df_results.select(
        fn=on_df_select,
        inputs=[df_results, proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui, ssl_ui],
        outputs=[selected_repo_ui, tag_ui]
    )

    dl_btn.click(
        fn=fn_download_manager,
        inputs=[
            selected_repo_ui, tag_ui, arch_ui, registry_ui, out_dir_ui,
            proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui, ssl_ui
        ],
        outputs=[log_box, file_list_ui, delete_dropdown]  # 同步刷新文件面板
    )

    # 文件管理器事件绑定
    demo.load(
        fn=get_downloaded_tars, inputs=[out_dir_ui], outputs=[file_list_ui, delete_dropdown]
    )
    refresh_btn.click(
        fn=get_downloaded_tars, inputs=[out_dir_ui], outputs=[file_list_ui, delete_dropdown]
    )
    delete_btn.click(
        fn=delete_local_tar, inputs=[delete_dropdown, out_dir_ui], outputs=[file_list_ui, delete_dropdown]
    )

    demo.queue(default_concurrency_limit=4)

if __name__ == "__main__":
    print(f"🚀 服务启动中...\n💡 请在浏览器访问下方 URL 打开 UI 界面。")
    favicon_path = Path(__file__).resolve().parent / "favicon.ico"
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        inbrowser=True,
        theme=gr.themes.Soft(primary_hue="blue"),
        css=CUSTOM_CSS,
        favicon_path=str(favicon_path) if favicon_path.exists() else None
    )
