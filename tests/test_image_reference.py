import pytest

from registry_config import (
    DEFAULT_REGISTRY_HOST,
    ImageTarget,
    RegistryConfigError,
    build_image_target,
    build_image_target_from_reference,
    normalize_repository,
    rewrite_image_reference,
    split_image_reference,
)
from registry_config import PRESET_REGISTRIES

OFFICIAL_KEYS = ["docker", "ghcr", "quay", "k8s", "k8s-gcr", "gcr", "gitlab"]


@pytest.mark.parametrize("image,registry,repository,tag", [
    ("nginx", None, "nginx", ""),
    ("nginx:1.25", None, "nginx", "1.25"),
    ("library/nginx", None, "library/nginx", ""),
    ("docker.io/library/nginx:latest", "docker.io", "library/nginx", "latest"),
    ("ghcr.io/foo/bar:1.0", "ghcr.io", "foo/bar", "1.0"),
    ("localhost:5000/app:dev", "localhost:5000", "app", "dev"),
])
def test_split_image_reference(image, registry, repository, tag):
    assert split_image_reference(image) == (registry, repository, tag)


@pytest.mark.parametrize("image", ["", "   ", "nginx@sha256:abc"])
def test_split_invalid_reference_raises(image):
    with pytest.raises(RegistryConfigError):
        split_image_reference(image)


def test_normalize_repository_adds_library_for_single_segment():
    assert normalize_repository("nginx") == "library/nginx"
    assert normalize_repository("/library/nginx/") == "library/nginx"
    assert normalize_repository("foo/bar") == "foo/bar"


@pytest.mark.parametrize("key", OFFICIAL_KEYS)
def test_rewrite_prefix_for_each_preset_registry(key):
    host = PRESET_REGISTRIES[key].host
    target = build_image_target_from_reference("nginx:1.25", host)
    assert target.registry == host
    assert target.repository == "library/nginx"
    assert target.tag == "1.25"
    assert target.reference == f"{host}/library/nginx:1.25"


@pytest.mark.parametrize("key", OFFICIAL_KEYS)
def test_rewrite_replaces_explicit_registry_prefix(key):
    host = PRESET_REGISTRIES[key].host
    reference = rewrite_image_reference("docker.io/library/nginx:latest", host)
    assert reference == f"{host}/library/nginx:latest"


def test_rewrite_keeps_namespace_of_explicit_reference():
    reference = rewrite_image_reference("ghcr.io/linuxcontainers/alpine:3.20", "quay.io")
    assert reference == "quay.io/linuxcontainers/alpine:3.20"


def test_rewrite_default_target_keeps_docker_hub():
    target = build_image_target_from_reference("nginx:latest")
    assert target.registry == DEFAULT_REGISTRY_HOST
    assert target.repository == "library/nginx"


def test_rewrite_without_target_preserves_image_registry():
    target = build_image_target_from_reference("harbor.example.com/team/app:1.0")
    assert target.registry == "harbor.example.com"
    assert target.repository == "team/app"


def test_build_image_target_for_web_search_result():
    target = build_image_target("library/redis", "7", "registry.k8s.io")
    assert isinstance(target, ImageTarget)
    assert target.reference == "registry.k8s.io/library/redis:7"
    assert target.repo_tag == "library/redis:7"


def test_build_image_target_defaults_to_latest_tag():
    target = build_image_target("redis", None)
    assert target.tag == "latest"
    assert target.registry == DEFAULT_REGISTRY_HOST


@pytest.mark.parametrize("key", OFFICIAL_KEYS)
def test_image_target_reference_uses_selected_host(key):
    option = PRESET_REGISTRIES[key]
    target = build_image_target_from_reference("alpine", option.host)
    assert target.reference.startswith(f"{option.host}/")
