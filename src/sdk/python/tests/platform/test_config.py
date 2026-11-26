from __future__ import annotations

import pytest

from ratel_ai.platform import (
    API_KEY_ENV,
    BASE_URL_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    PlatformSwitch,
    platform_switch_on,
    resolve_platform_config,
)


def test_env_names_and_defaults_match_the_ts_sdk() -> None:
    assert API_KEY_ENV == "RATEL_API_KEY"
    assert BASE_URL_ENV == "RATEL_BASE_URL"
    assert DEFAULT_BASE_URL == "https://app.ratel.sh/api/v1"
    assert DEFAULT_TIMEOUT == 30.0


def test_api_key_argument_wins_over_env() -> None:
    config = resolve_platform_config(api_key="rtl_opt", env={"RATEL_API_KEY": "rtl_env"})
    assert config.api_key == "rtl_opt"


def test_api_key_falls_back_to_env() -> None:
    assert resolve_platform_config(env={"RATEL_API_KEY": "rtl_env"}).api_key == "rtl_env"


def test_api_key_is_none_without_argument_or_env() -> None:
    assert resolve_platform_config(env={}).api_key is None


def test_empty_env_api_key_counts_as_unset() -> None:
    assert resolve_platform_config(env={"RATEL_API_KEY": ""}).api_key is None


def test_base_url_argument_wins_over_env() -> None:
    config = resolve_platform_config(
        base_url="http://opt.test/api/v1", env={"RATEL_BASE_URL": "http://env.test/api/v1"}
    )
    assert config.base_url == "http://opt.test/api/v1"


def test_base_url_falls_back_to_env_then_default() -> None:
    env_config = resolve_platform_config(env={"RATEL_BASE_URL": "http://env.test/api/v1"})
    assert env_config.base_url == "http://env.test/api/v1"
    assert resolve_platform_config(env={}).base_url == DEFAULT_BASE_URL
    assert resolve_platform_config(env={"RATEL_BASE_URL": ""}).base_url == DEFAULT_BASE_URL


def test_base_url_loses_trailing_slashes() -> None:
    assert resolve_platform_config(base_url="http://opt.test/api/v1//", env={}).base_url == (
        "http://opt.test/api/v1"
    )
    assert resolve_platform_config(env={"RATEL_BASE_URL": "http://env.test/"}).base_url == (
        "http://env.test"
    )


def test_timeout_passes_through_with_a_default() -> None:
    assert resolve_platform_config(timeout=5.0, env={}).timeout == 5.0
    assert resolve_platform_config(env={}).timeout == DEFAULT_TIMEOUT


def test_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATEL_BASE_URL", "http://process.test/api/v1")
    assert resolve_platform_config().base_url == "http://process.test/api/v1"


def test_repr_never_shows_the_api_key() -> None:
    config = resolve_platform_config(api_key="rtl_secret_value", env={})
    assert "rtl_secret_value" not in repr(config)
    assert "rtl_secret_value" not in str(config)
    assert "api_key=" in repr(config)


def test_importing_ratel_ai_does_not_import_the_platform_client() -> None:
    import subprocess
    import sys

    code = "import sys, ratel_ai; print('ratel_ai.platform' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


@pytest.mark.parametrize(
    ("feature", "current", "deprecated"),
    [
        ("events", "RATEL_PLATFORM_EVENTS", "RATEL_CLOUD_EVENTS"),
        ("intent_graph", "RATEL_PLATFORM_INTENT_GRAPH", "RATEL_CLOUD_INTENT_GRAPH"),
    ],
)
def test_switch_reads_the_platform_name_then_the_deprecated_name(
    feature: PlatformSwitch, current: str, deprecated: str
) -> None:
    assert platform_switch_on(feature, env={}) is True
    assert platform_switch_on(feature, env={current: "off"}) is False
    assert platform_switch_on(feature, env={current: " OFF "}) is False
    assert platform_switch_on(feature, env={deprecated: "off"}) is False
    assert platform_switch_on(feature, env={current: "on", deprecated: "off"}) is True
    assert platform_switch_on(feature, env={current: "off", deprecated: "on"}) is False
    assert platform_switch_on(feature, env={current: "", deprecated: "off"}) is False


def test_ratel_ai_platform_and_the_stdlib_platform_module_work_in_one_process() -> None:
    import subprocess
    import sys

    code = (
        "import platform, ratel_ai.platform; "
        "from ratel_ai.platform import PlatformError, RatelPlatformClient; "
        "import platform as stdlib_platform; "
        "print(stdlib_platform.python_version(), "
        "RatelPlatformClient.__name__, PlatformError.__name__)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.split()[1:] == ["RatelPlatformClient", "PlatformError"]
    assert result.stdout.split()[0].startswith("3.")


def test_ratel_ai_cloud_does_not_exist() -> None:
    import importlib.util

    assert importlib.util.find_spec("ratel_ai.cloud") is None
