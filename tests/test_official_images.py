import pytest

from official_images import (
    OFFICIAL_IMAGE_GROUPS,
    find_exact_images,
    get_group_choices,
    get_group_names,
    get_images,
    normalize_group_keys,
    search_images,
)

OFFICIAL_GROUP_KEYS = ["ghcr", "quay", "k8s", "k8s-gcr", "gcr", "gitlab"]


def test_groups_cover_official_registries():
    for key in OFFICIAL_GROUP_KEYS + ["docker"]:
        assert key in OFFICIAL_IMAGE_GROUPS


def test_group_choices_expose_all_groups():
    keys = [key for _, key in get_group_choices()]
    assert keys == list(OFFICIAL_IMAGE_GROUPS.keys())


@pytest.mark.parametrize("key", list(OFFICIAL_IMAGE_GROUPS.keys()))
def test_group_images_have_no_duplicates(key):
    repos = [image.repo for image in OFFICIAL_IMAGE_GROUPS[key].images]
    assert len(repos) == len(set(repos))


@pytest.mark.parametrize("key", OFFICIAL_GROUP_KEYS)
def test_non_hub_group_repos_are_prefixed_with_host(key):
    host = OFFICIAL_IMAGE_GROUPS[key].host
    for image in OFFICIAL_IMAGE_GROUPS[key].images:
        assert image.repo.startswith(f"{host}/")


def test_docker_group_uses_library_namespace():
    for image in OFFICIAL_IMAGE_GROUPS["docker"].images:
        assert image.repo.startswith("library/")


def test_get_images_without_groups_returns_all():
    expected = sum(len(group.images) for group in OFFICIAL_IMAGE_GROUPS.values())
    assert len(get_images()) == expected


@pytest.mark.parametrize("key", list(OFFICIAL_IMAGE_GROUPS.keys()))
def test_get_images_limits_scope_to_one_group(key):
    repos = {image.repo for image in get_images([key])}
    expected = {image.repo for image in OFFICIAL_IMAGE_GROUPS[key].images}
    assert repos == expected


def test_get_images_merges_multiple_groups():
    repos = {image.repo for image in get_images(["docker", "k8s"])}
    assert "library/nginx" in repos
    assert "registry.k8s.io/pause" in repos
    assert "quay.io/prometheus/prometheus" not in repos


def test_get_group_names_keeps_config_order():
    assert get_group_names(["k8s", "docker"]) == ["Docker 官方镜像", "Kubernetes 官方镜像"]


@pytest.mark.parametrize("query,expected_repo", [
    ("library/nginx", "library/nginx"),
    ("nginx", "library/nginx"),
    ("Library/Nginx", "library/nginx"),
    ("library/nginx:latest", "library/nginx"),
    ("registry.k8s.io/pause", "registry.k8s.io/pause"),
])
def test_find_exact_images(query, expected_repo):
    repos = [image.repo for image in find_exact_images(query, ["docker", "k8s"])]
    assert repos == [expected_repo]


def test_find_exact_images_returns_empty_for_partial_name():
    assert find_exact_images("ngin", ["docker"]) == []


def test_search_images_puts_exact_match_first():
    results = search_images("library/nginx", ["docker"])
    assert results[0].repo == "library/nginx"


def test_search_images_keeps_fuzzy_matching():
    repos = [image.repo for image in search_images("ngin", ["docker"])]
    assert "library/nginx" in repos
    assert "library/redis" not in repos


def test_search_images_matches_description():
    repos = [image.repo for image in search_images("PostgreSQL", ["docker"])]
    assert repos == ["library/postgres"]


def test_search_images_is_strictly_scoped_to_selected_group():
    repos = [image.repo for image in search_images("redis", ["k8s"])]
    assert repos == []


def test_search_images_scopes_multiple_groups():
    repos = [image.repo for image in search_images("pause", ["k8s", "gcr"])]
    assert "registry.k8s.io/pause" in repos
    assert "gcr.io/google-containers/pause" in repos
    assert "library/nginx" not in repos


def test_search_images_ignores_unknown_group():
    assert search_images("nginx", ["unknown"]) == []


def test_search_images_strips_tag_in_fuzzy_mode():
    repos = [image.repo for image in search_images("nginx:1.25", ["docker"])]
    assert "library/nginx" in repos


def test_normalize_group_keys_handles_string_and_display_name():
    assert normalize_group_keys(None) == []
    assert normalize_group_keys([]) == []
    assert normalize_group_keys("docker") == ["docker"]
    assert normalize_group_keys("Docker 官方镜像") == ["docker"]


def test_normalize_group_keys_keeps_config_order_and_deduplicates():
    assert normalize_group_keys(["k8s", "docker", "k8s", "unknown"]) == ["docker", "k8s"]
    assert normalize_group_keys(["Kubernetes 官方镜像", "Google 官方镜像"]) == ["k8s", "gcr"]


def test_get_images_accepts_string_and_display_name():
    repos = [image.repo for image in get_images("Docker 官方镜像")]
    assert "library/nginx" in repos
    assert "registry.k8s.io/pause" not in repos


def test_get_group_names_accepts_display_name():
    assert get_group_names(["Kubernetes 官方镜像", "docker"]) == ["Docker 官方镜像", "Kubernetes 官方镜像"]
