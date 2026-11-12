import pytest

from fakes import FakeResponse, FakeSession
from official_images import OFFICIAL_IMAGE_GROUPS
from registry_config import RegistryConfigError

app = pytest.importorskip("app", reason="需要安装 gradio 才能测试搜索逻辑")

ALL_GROUP_KEYS = list(OFFICIAL_IMAGE_GROUPS.keys())
PROXY_ARGS = ("无代理", "", "", "", True)


def make_session(search_items=None, hub_item=None, ghcr_tags=None):
    """构造同时支持 1ms 搜索、Docker Hub 详情与 GHCR 私有探测的假 Session。"""

    def handler(url, headers, stream, timeout, **kwargs):
        if "hub.docker.com" in url:
            if hub_item is not None:
                return FakeResponse(200, json_data=hub_item)
            return FakeResponse(404)
        if url.startswith("https://ghcr.io/v2/") and "/tags/list" not in url:
            return FakeResponse(401, headers={
                "WWW-Authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io"',
            })
        if "ghcr.io/token" in url:
            if ghcr_tags is None:
                return FakeResponse(401)
            return FakeResponse(200, json_data={"token": "fake-ghcr-token"})
        if "ghcr.io/v2/" in url and "/tags/list" in url:
            if ghcr_tags is None:
                return FakeResponse(404)
            return FakeResponse(200, json_data={"tags": ghcr_tags})
        if "1ms.run" in url:
            items = search_items or []
            return FakeResponse(200, json_data={"data": {"list": items, "total": len(items)}})
        raise AssertionError(f"未预期的请求地址: {url}")

    session = FakeSession(handler)
    session.hub_calls = 0
    return session


def install_session(monkeypatch, session):
    monkeypatch.setattr(app, "get_session", lambda verify_ssl, proxies, **kwargs: session)


def search(keyword, page=1, official_keys=None):
    return app.execute_search(keyword, page, *PROXY_ARGS, official_keys=official_keys)


def api_item(namespace, name, stars=1, pulls=2, description="", modified="2026-01-01T00:00:00Z",
             registry="docker.io"):
    return {
        "registry": registry,
        "namespace": namespace,
        "name": name,
        "star_count": stars,
        "pull_count": pulls,
        "last_modified": modified,
        "description": description,
    }


def test_unfiltered_search_without_slash_keeps_fuzzy_results(monkeypatch):
    items = [api_item("library", "nginx"), api_item("bitnami", "nginx")]
    session = make_session(items)
    install_session(monkeypatch, session)

    rows, page, info, _, _ = search("ngin")

    assert [row[0] for row in rows] == ["library/nginx", "bitnami/nginx"]
    assert page == 1
    assert "共 2 个结果" in info


def test_unfiltered_exact_match_is_moved_to_front(monkeypatch):
    items = [
        api_item("library", "nginx"),
        api_item("kite-images", "kite"),
        api_item("kite-org", "kite"),
    ]
    hub = {"namespace": "kite-org", "name": "kite", "star_count": 9, "pull_count": 99,
           "last_updated": "2026-02-02T00:00:00Z", "description": "exact repo"}
    session = make_session(items, hub_item=hub)
    install_session(monkeypatch, session)

    rows, _, info, _, _ = search("kite-org/kite")

    assert rows[0][0] == "kite-org/kite"
    assert rows[0][1] == "Docker Hub"
    assert rows[0][2] == 9
    assert "共 3 个结果" in info
    assert [row[0] for row in rows[1:]] == ["library/nginx", "kite-images/kite"]


def test_unfiltered_exact_match_inserted_when_api_misses_it(monkeypatch):
    items = [api_item("library", "nginx"), api_item("bitnami", "redis")]
    hub = {"namespace": "kite-org", "name": "kite", "star_count": 3, "pull_count": 42,
           "last_updated": "2026-03-03T00:00:00Z", "description": "exact repo"}
    session = make_session(items, hub_item=hub)
    install_session(monkeypatch, session)

    rows, _, info, _, _ = search("kite-org/kite")

    assert rows[0][0] == "kite-org/kite"
    assert len(rows) == 3
    assert "共 3 个结果" in info


def test_unfiltered_exact_match_falls_back_when_metadata_unavailable(monkeypatch):
    items = [
        api_item("library", "nginx"),
        api_item("kite-org", "kite"),
        api_item("bitnami", "redis"),
    ]
    session = make_session(items)
    install_session(monkeypatch, session)

    rows, _, _, _, _ = search("kite-org/kite")

    assert rows[0][0] == "kite-org/kite"


def test_unfiltered_non_hub_reference_is_exact_result(monkeypatch):
    items = [api_item("library", "alpine")]
    session = make_session(items)
    install_session(monkeypatch, session)

    rows, _, info, _, _ = search("ghcr.io/linuxcontainers/alpine")

    assert rows[0][0] == "ghcr.io/linuxcontainers/alpine"
    assert rows[0][1] == "GitHub (GHCR)"
    assert "精确匹配" in rows[0][5]
    assert "共 2 个结果" in info


def test_unfiltered_exact_match_falls_back_to_ghcr(monkeypatch):
    items = [api_item("library", "nginx"), api_item("kite-images", "kite")]
    session = make_session(items, ghcr_tags=["latest", "v0.16.0"])
    install_session(monkeypatch, session)

    rows, _, info, _, _ = search("kite-org/kite")

    assert rows[0][0] == "ghcr.io/kite-org/kite"
    assert rows[0][1] == "GitHub (GHCR)"
    assert "ghcr.io" in rows[0][5]
    assert "共 3 个结果" in info


def test_get_tags_reads_from_explicit_registry(monkeypatch):
    session = make_session(ghcr_tags=["latest", "v0.16.0"])
    install_session(monkeypatch, session)

    update = app.fn_get_tags("ghcr.io/kite-org/kite", *PROXY_ARGS)

    assert update["choices"] == ["latest", "v0.16.0"]
    assert update["value"] == "latest"


def test_single_group_limits_search_scope(monkeypatch):
    session = make_session([api_item("library", "nginx")])
    install_session(monkeypatch, session)

    rows, _, info, _, _ = search("nginx", official_keys=["docker"])

    docker_repos = {image.repo for image in OFFICIAL_IMAGE_GROUPS["docker"].images}
    assert all(row[0] in docker_repos for row in rows)
    assert rows[0][0] == "library/nginx"
    assert "Docker 官方镜像" in info
    assert session.hub_calls == 0


def test_single_group_returns_empty_when_no_match(monkeypatch):
    session = make_session()
    install_session(monkeypatch, session)

    rows, page, info, prev, next_ = search("redis", official_keys=["k8s"])

    assert rows == []
    assert page == 1
    assert "未在所选官方镜像集合" in info
    assert "Kubernetes 官方镜像" in info
    assert prev["interactive"] is False
    assert next_["interactive"] is False


def test_multiple_groups_union_scope(monkeypatch):
    session = make_session()
    install_session(monkeypatch, session)

    rows, _, info, _, _ = search("pause", official_keys=["k8s", "gcr"])

    repos = [row[0] for row in rows]
    assert "registry.k8s.io/pause" in repos
    assert "gcr.io/google-containers/pause" in repos
    assert "library/nginx" not in repos
    assert "Kubernetes 官方镜像" in info
    assert "Google 官方镜像" in info


def test_multiple_groups_exclude_unselected_repositories(monkeypatch):
    session = make_session()
    install_session(monkeypatch, session)

    rows, _, _, _, _ = search("prometheus", official_keys=["quay", "docker"])

    repos = [row[0] for row in rows]
    assert "quay.io/prometheus/prometheus" in repos
    assert all(repo.startswith("quay.io/") for repo in repos)


def test_exact_match_priority_inside_selected_group(monkeypatch):
    session = make_session()
    install_session(monkeypatch, session)

    rows, _, _, _, _ = search("registry.k8s.io/pause", official_keys=["k8s"])

    assert rows[0][0] == "registry.k8s.io/pause"


def test_exact_match_without_namespace_inside_group(monkeypatch):
    session = make_session()
    install_session(monkeypatch, session)

    rows, _, _, _, _ = search("nginx", official_keys=["docker"])

    assert rows[0][0] == "library/nginx"


def test_fuzzy_matching_preserved_inside_group(monkeypatch):
    session = make_session()
    install_session(monkeypatch, session)

    rows, _, _, _, _ = search("ng", official_keys=["docker"])

    repos = [row[0] for row in rows]
    assert "library/nginx" in repos
    assert "library/redis" not in repos


def test_empty_official_selection_uses_remote_search(monkeypatch):
    items = [api_item("library", "nginx")]
    session = make_session(items)
    install_session(monkeypatch, session)

    rows, _, _, _, _ = search("nginx", official_keys=[])

    assert [row[0] for row in rows] == ["library/nginx"]


def test_official_search_paginates_locally(monkeypatch):
    session = make_session()
    install_session(monkeypatch, session)

    rows_page_1, page_1, _, _, next_1 = search("io", page=1, official_keys=ALL_GROUP_KEYS)
    rows_page_2, page_2, _, prev_2, _ = search("io", page=2, official_keys=ALL_GROUP_KEYS)

    assert len(rows_page_1) == 20
    assert page_1 == 1
    assert next_1["interactive"] is True
    assert page_2 == 2
    assert prev_2["interactive"] is True
    assert rows_page_2

    repos_page_1 = {row[0] for row in rows_page_1}
    repos_page_2 = {row[0] for row in rows_page_2}
    assert repos_page_1.isdisjoint(repos_page_2)


def test_official_search_clamps_page_number(monkeypatch):
    session = make_session()
    install_session(monkeypatch, session)

    rows, page, _, _, next_ = search("nginx", page=99, official_keys=["docker"])

    assert page == 1
    assert rows
    assert next_["interactive"] is False


def test_empty_keyword_returns_placeholder():
    rows, page, info, prev, next_ = search("", official_keys=["docker"])

    assert rows == []
    assert page == 1
    assert "请先输入关键词" in info
    assert prev["interactive"] is False
    assert next_["interactive"] is False


@pytest.mark.parametrize("repo,tag,registry,expected", [
    ("registry.k8s.io/pause", "3.9", "docker.1ms.run", "registry.k8s.io/pause:3.9"),
    ("k8s.gcr.io/pause", "3.9", "ghcr.io", "k8s.gcr.io/pause:3.9"),
    ("library/nginx", "latest", "ghcr.io", "ghcr.io/library/nginx:latest"),
    ("kite-org/kite", "1.0", "quay.io", "quay.io/kite-org/kite:1.0"),
    ("nginx", "1.25", "registry.k8s.io", "registry.k8s.io/library/nginx:1.25"),
])
def test_resolve_pull_target(repo, tag, registry, expected):
    target = app.resolve_pull_target(repo, tag, registry)
    assert target.reference == expected


def test_resolve_pull_target_rejects_unknown_registry():
    with pytest.raises(RegistryConfigError):
        app.resolve_pull_target("library/nginx", "latest", "unknown source")


def test_k8s_group_search_finds_kubectl(monkeypatch):
    install_session(monkeypatch, make_session())

    rows, _, info, _, _ = search("kubectl", official_keys=["k8s"])

    assert [row[0] for row in rows] == ["registry.k8s.io/kubectl"]
    assert "Kubernetes 官方镜像" in info


def test_global_search_merges_builtin_k8s_index(monkeypatch):
    session = make_session([api_item("bitnami", "kubectl")])
    install_session(monkeypatch, session)

    rows, page, info, _, _ = search("kubectl")

    assert rows[0][0] == "registry.k8s.io/kubectl"
    assert "bitnami/kubectl" in [row[0] for row in rows]
    assert page == 1
    assert "共 2 个结果" in info
    assert "含内置官方镜像" in info


def test_global_search_falls_back_when_online_index_fails(monkeypatch):
    class OfflineSession:
        def get(self, *args, **kwargs):
            raise OSError("offline")

        def close(self):
            pass

    monkeypatch.setattr(app, "get_session", lambda *a, **k: OfflineSession())

    rows, page, info, _, _ = search("kubectl")

    assert rows[0][0] == "registry.k8s.io/kubectl"
    assert page == 1
    assert "在线索引不可用" in info


def test_fetch_remote_tags_skips_signature_tags():
    def handler(url, headers, stream, timeout, **kwargs):
        if url.endswith("/tags/list"):
            return FakeResponse(200, json_data={
                "tags": ["sha256-aaaa.sig", "v1.30.0", "sha256-bbbb.att", "latest"]
            })
        return FakeResponse(200)

    tags = app.fetch_remote_tags(FakeSession(handler), "registry.k8s.io", "kubectl")

    assert tags == ["v1.30.0", "latest"]


def test_fn_get_tags_defaults_to_stable_version(monkeypatch):
    def handler(url, headers, stream, timeout, **kwargs):
        if url.endswith("/tags/list"):
            return FakeResponse(200, json_data={
                "tags": ["sha256-aaaa.sig", "v1.30.0-alpha.1", "v1.30.0"]
            })
        return FakeResponse(200)

    monkeypatch.setattr(app, "get_session", lambda *a, **k: FakeSession(handler))

    update = app.fn_get_tags("registry.k8s.io/kubectl", *PROXY_ARGS)

    assert update["choices"] == ["v1.30.0-alpha.1", "v1.30.0"]
    assert update["value"] == "v1.30.0"


def test_fetch_remote_tags_keeps_newest_versions_when_truncated():
    raw_tags = [f"sha256-{index:04d}.sig" for index in range(120)] + [
        "v1.28.0", "v1.31.0", "v1.30.0", "v1.31.0-rc.1", "latest"
    ]

    def handler(url, headers, stream, timeout, **kwargs):
        if url.endswith("/tags/list"):
            return FakeResponse(200, json_data={"tags": raw_tags})
        return FakeResponse(200)

    tags = app.fetch_remote_tags(FakeSession(handler), "registry.k8s.io", "kubectl", limit=3)

    assert tags == ["latest", "v1.31.0", "v1.31.0-rc.1"]


def test_no_group_selection_keeps_online_results(monkeypatch):
    install_session(monkeypatch, make_session([api_item("bitnami", "nginx")]))

    rows, page, info, _, _ = search("bitnami/nginx")

    assert rows[0][0] == "bitnami/nginx"
    assert page == 1
    assert "范围:" not in info


def test_single_group_returns_online_results_in_same_registry(monkeypatch):
    install_session(monkeypatch, make_session([api_item("bitnami", "nginx")]))

    rows, page, info, _, _ = search("bitnami/nginx", official_keys=["docker"])

    assert [row[0] for row in rows] == ["bitnami/nginx"]
    assert page == 1
    assert "范围: Docker 官方镜像" in info
    assert "含在线结果 1 个" in info


def test_single_group_excludes_online_results_from_other_registries(monkeypatch):
    install_session(monkeypatch, make_session([api_item("bitnami", "nginx")]))

    rows, _, _, _, _ = search("nginx", official_keys=["k8s"])

    repos = [row[0] for row in rows]
    assert "bitnami/nginx" not in repos
    assert "registry.k8s.io/ingress-nginx/controller" in repos


def test_multiple_groups_merge_static_and_online_results(monkeypatch):
    install_session(monkeypatch, make_session([api_item("bitnami", "nginx")]))

    rows, _, info, _, _ = search("nginx", official_keys=["docker", "k8s"])

    repos = [row[0] for row in rows]
    assert "library/nginx" in repos
    assert "registry.k8s.io/ingress-nginx/controller" in repos
    assert "bitnami/nginx" in repos
    assert "Docker 官方镜像" in info
    assert "Kubernetes 官方镜像" in info


def test_selected_group_pagination_is_consistent(monkeypatch):
    items = [api_item(f"ns{index}", "nginx") for index in range(25)]
    install_session(monkeypatch, make_session(items))

    rows_1, page_1, _, _, next_1 = search("nginx", page=1, official_keys=["docker"])
    rows_2, page_2, _, prev_2, _ = search("nginx", page=2, official_keys=["docker"])

    assert page_1 == 1 and page_2 == 2
    assert {row[0] for row in rows_1}.isdisjoint({row[0] for row in rows_2})
    assert next_1["interactive"] is True
    assert prev_2["interactive"] is True


def test_group_selection_accepts_single_string(monkeypatch):
    install_session(monkeypatch, make_session([api_item("bitnami", "nginx")]))

    rows, _, info, _, _ = search("nginx", official_keys="docker")

    assert "library/nginx" in [row[0] for row in rows]
    assert "范围: Docker 官方镜像" in info


def test_group_selection_accepts_display_names(monkeypatch):
    install_session(monkeypatch, make_session())

    rows, _, info, _, _ = search("pause", official_keys=["Kubernetes 官方镜像", "Google 官方镜像"])

    repos = [row[0] for row in rows]
    assert "registry.k8s.io/pause" in repos
    assert "gcr.io/google-containers/pause" in repos
    assert "Kubernetes 官方镜像" in info
    assert "Google 官方镜像" in info


def test_unknown_group_value_falls_back_to_unfiltered_search(monkeypatch):
    install_session(monkeypatch, make_session([api_item("bitnami", "nginx")]))

    rows, _, info, _, _ = search("nginx", official_keys=["unknown"])

    assert "bitnami/nginx" in [row[0] for row in rows]
    assert "范围:" not in info


def test_selected_group_falls_back_when_online_index_fails(monkeypatch):
    class OfflineSession:
        def get(self, *args, **kwargs):
            raise OSError("offline")

        def close(self):
            pass

    monkeypatch.setattr(app, "get_session", lambda *a, **k: OfflineSession())

    rows, _, info, _, _ = search("kubectl", official_keys=["k8s"])

    assert [row[0] for row in rows] == ["registry.k8s.io/kubectl"]
    assert "在线索引不可用" in info


def test_search_rows_expose_registry_source(monkeypatch):
    """搜索结果每一行都应带仓库来源，且不同仓库显示不同来源。"""
    items = [
        api_item("library", "nginx"),
        api_item("prometheus", "alertmanager", registry="quay.io"),
    ]
    install_session(monkeypatch, make_session(items))

    rows, _, _, _, _ = search("nginx")

    assert all(row[1] for row in rows), "存在来源为空的记录"
    assert rows[0][1] == "Docker Hub"
    assert any(row[1] == "Quay.io" for row in rows)


def test_official_search_rows_carry_group_source(monkeypatch):
    """分组筛选时内置清单行的来源应与所选分组仓库一致。"""
    install_session(monkeypatch, make_session([]))

    rows, _, _, _, _ = search("nginx", official_keys=["docker"])

    assert [row[0] for row in rows] == ["library/nginx"]
    assert rows[0][1] == "Docker Hub"


def test_search_releases_session(monkeypatch):
    """每次搜索新建的 Session 应在返回前关闭，避免连接池泄漏。"""
    session = make_session([api_item("library", "nginx")])
    install_session(monkeypatch, session)

    search("nginx")

    assert session.closed


def test_pagination_requests_requested_online_page(monkeypatch):
    """翻页时应把目标页码传给在线索引。"""
    session = make_session([api_item("library", "nginx")])
    install_session(monkeypatch, session)

    search("nginx", page=2)

    page_params = [kwargs.get("params", {}).get("page")
                   for url, kwargs in session.requested_calls if "1ms.run" in url]
    assert page_params == [2]
