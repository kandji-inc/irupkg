import json
import logging
import os

import pytest
import requests
from irupkg.helpers.configs import Configurator, _deprecated_env, _deprecated_config_key, _warned_deprecated


@pytest.fixture
def minimal_config():
    return {
        "iru": {"api_url": "https://test.api.iru.com", "token_name": "IRUPKG_TOKEN"},
        "token_keystore": {"environment": True, "keychain": False},
        "li_enforcement": {"delays": {"prod": 5, "test": 0}, "type": "audit_enforce"},
        "slack": {"enabled": False, "webhook_name": "SLACK_TOKEN"},
        "use_package_map": False,
        "zz_defaults": {
            "auto_create_app": True,
            "dry_run": False,
            "dynamic_lookup": False,
            "new_app_naming": "APPNAME (irupkg)",
            "self_service_category": "Apps",
            "test_self_service_category": "Utilities",
            "unzip_location": "/Applications",
        },
    }


@pytest.fixture
def config_dir(tmp_path, minimal_config):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(minimal_config))
    return tmp_path


@pytest.fixture
def bare_config(tmp_path):
    obj = Configurator()
    obj.parent_dir = str(tmp_path)
    obj.arg_dry_run = False
    obj.arg_ss_category = None
    obj.arg_test_category = None
    obj.arg_unzip_location = None
    obj.arg_pkg_path = None
    obj.arg_app_name = None
    obj.arg_prod_name = None
    obj.arg_test_name = None
    return obj


# ---------------------------------------------------------------------------
# Configurator._parse_enforcement -- swaps the user-facing label with the
# Iru API enum and vice versa.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        pytest.param("audit_enforce", "continuously_enforce", id="audit-enforce"),
        pytest.param("self_service", "no_enforcement", id="self-service"),
        pytest.param("install_once", "install_once", id="install-once-passthrough"),
    ],
)
def test_parse_enforcement_valid_values(bare_config, input_val, expected):
    assert bare_config._parse_enforcement(input_val) == expected


def test_parse_enforcement_returns_false_when_value_unknown(bare_config):
    assert bare_config._parse_enforcement("not_a_real_enforcement") is False


# ---------------------------------------------------------------------------
# Configurator._read_config -- env-var auto-generate fallback. The trivial
# "file exists, returns parsed JSON" path is just round-tripping json.loads;
# the meaningful behaviour is writing a fresh config from KANDJI_API_URL when
# no file is present.
# ---------------------------------------------------------------------------


def test_read_config_auto_generates_from_env_when_missing(bare_config, tmp_path, monkeypatch):
    api_url = "https://generated.api.iru.com"
    monkeypatch.setenv("IRU_API_URL", api_url)
    monkeypatch.setenv("IRUPKG_TOKEN_NAME", "GENERATED_TOKEN")
    bare_config.parent_dir = str(tmp_path)

    result = bare_config._read_config("config.json")

    assert result["iru"]["api_url"] == api_url
    assert result["iru"]["token_name"] == "GENERATED_TOKEN"
    # File is persisted on disk for subsequent runs
    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk == result


# ---------------------------------------------------------------------------
# Configurator._set_defaults_enforcements
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ss_cat", "expected_enforcement"),
    [
        pytest.param(None, "continuously_enforce", id="no-ss-category"),
        pytest.param("Apps", "no_enforcement", id="ss-category-overrides-to-no-enforcement"),
    ],
)
def test_set_defaults_enforcements_enforcement_type(bare_config, minimal_config, ss_cat, expected_enforcement):
    bare_config.iru_config = minimal_config
    bare_config.map_ss_category = ss_cat
    bare_config.map_test_category = None
    bare_config.map_unzip_location = None
    bare_config._set_defaults_enforcements()
    assert bare_config.custom_app_enforcement == expected_enforcement


# ---------------------------------------------------------------------------
# Configurator.get_install_media_metadata -- derives the installer name from
# the file path; tested for two common upstream naming conventions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        pytest.param("CotEditor_7.0.2.dmg", "CotEditor", id="underscore-before-version"),
        pytest.param("Firefox-1.0.0.dmg", "Firefox", id="dash-before-version"),
        pytest.param("claude-devtools-0.4.16-arm64.dmg", "claude-devtools", id="hyphenated-name"),
        pytest.param("Zed-aarch64.dmg", "Zed", id="arch-suffix-no-version"),
    ],
)
def test_derive_installer_name(bare_config, mocker, filename, expected):
    bare_config.pkg_path = f"/fake/{filename}"
    bare_config.install_name = None
    mocker.patch("irupkg.helpers.configs.platform.system", return_value="Linux")
    bare_config.get_install_media_metadata()
    assert bare_config.pkg_path_name == expected


# ---------------------------------------------------------------------------
# Configurator._set_custom_name
# ---------------------------------------------------------------------------


def test_set_custom_name_default_template(bare_config):
    install_name = "Firefox"
    template = "APPNAME (irupkg)"
    bare_config.install_name = install_name
    bare_config.pkg_path_name = "myapp"
    bare_config.arg_app_name = None
    bare_config.app_names = {}
    bare_config.default_custom_name = template
    bare_config._set_custom_name()
    assert bare_config.custom_app_name == template.replace("APPNAME", install_name)


# ---------------------------------------------------------------------------
# Configurator._populate_self_service
# ---------------------------------------------------------------------------


def test_populate_self_service_exact_prod_match(combined_obj, fake_response_factory, mocker):
    matching = {"id": "cat-custom", "name": "CustomCat"}
    categories = [{"id": "cat-apps", "name": "Apps"}, matching]
    combined_obj.iru_api_prefix = "https://test.api.iru.com/api/v1"
    combined_obj.auth_headers = {}
    combined_obj.default_ss_category = "Apps"
    combined_obj.test_default_ss_category = "Utilities"
    combined_obj.map_ss_category = matching["name"]
    combined_obj.map_test_category = None
    combined_obj.arg_ss_category = None
    combined_obj.arg_test_category = None
    mocker.patch("requests.get", return_value=fake_response_factory(200, categories))

    combined_obj._populate_self_service()
    assert combined_obj.ss_category_id == matching["id"]


# ---------------------------------------------------------------------------
# _deprecated_env -- deprecation shim for KANDJI_* env vars
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_deprecated_warnings():
    """Reset the module-level warning guard between tests."""
    _warned_deprecated.clear()
    yield
    _warned_deprecated.clear()


@pytest.mark.parametrize(
    ("iru_val", "kandji_val", "expect_warning", "expect_result"),
    [
        pytest.param("iru_url", None, False, "iru_url", id="canonical-only-no-warning"),
        pytest.param(None, "kandji_url", True, "kandji_url", id="legacy-only-warns"),
        pytest.param("iru_url", "kandji_url", False, "iru_url", id="both-set-canonical-wins-no-warning"),
        pytest.param(None, None, False, "", id="neither-set-returns-empty"),
    ],
)
def test_deprecated_env_api_url(iru_val, kandji_val, expect_warning, expect_result, monkeypatch, capsys):
    if iru_val is not None:
        monkeypatch.setenv("IRU_API_URL", iru_val)
    else:
        monkeypatch.delenv("IRU_API_URL", raising=False)
    if kandji_val is not None:
        monkeypatch.setenv("KANDJI_API_URL", kandji_val)
    else:
        monkeypatch.delenv("KANDJI_API_URL", raising=False)

    result = os.environ.get("IRU_API_URL") or _deprecated_env("KANDJI_API_URL") or ""
    assert result == expect_result
    stderr = capsys.readouterr().err
    if expect_warning:
        assert "KANDJI_API_URL is deprecated" in stderr
    else:
        assert "KANDJI_API_URL" not in stderr


@pytest.mark.parametrize(
    ("iru_val", "kandji_val", "expect_warning", "expect_result"),
    [
        pytest.param("IRU_NAME", None, False, "IRU_NAME", id="canonical-only-no-warning"),
        pytest.param(None, "KANDJI_NAME", True, "KANDJI_NAME", id="legacy-only-warns"),
        pytest.param("IRU_NAME", "KANDJI_NAME", False, "IRU_NAME", id="both-set-canonical-wins"),
        pytest.param(None, None, False, "IRUPKG_TOKEN", id="neither-set-default-iru-token"),
    ],
)
def test_deprecated_env_token_name(iru_val, kandji_val, expect_warning, expect_result, bare_config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IRU_API_URL", "tenant.api.iru.com")
    monkeypatch.delenv("KANDJI_TOKEN", raising=False)
    bare_config.parent_dir = str(tmp_path)
    if iru_val is not None:
        monkeypatch.setenv("IRUPKG_TOKEN_NAME", iru_val)
    else:
        monkeypatch.delenv("IRUPKG_TOKEN_NAME", raising=False)
    if kandji_val is not None:
        monkeypatch.setenv("KANDJI_TOKEN_NAME", kandji_val)
    else:
        monkeypatch.delenv("KANDJI_TOKEN_NAME", raising=False)

    result = bare_config._read_config("config.json")

    assert result["iru"]["token_name"] == expect_result
    stderr = capsys.readouterr().err
    if expect_warning:
        assert "KANDJI_TOKEN_NAME is deprecated" in stderr
    else:
        assert "KANDJI_TOKEN_NAME" not in stderr


def test_deprecated_env_token_name_falls_back_to_kandji_token(bare_config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IRU_API_URL", "tenant.api.iru.com")
    monkeypatch.delenv("IRUPKG_TOKEN_NAME", raising=False)
    monkeypatch.delenv("KANDJI_TOKEN_NAME", raising=False)
    monkeypatch.delenv("IRUPKG_TOKEN", raising=False)
    monkeypatch.setenv("KANDJI_TOKEN", "legacy-secret")
    bare_config.parent_dir = str(tmp_path)

    result = bare_config._read_config("config.json")

    assert result["iru"]["token_name"] == "KANDJI_TOKEN"
    assert "KANDJI_TOKEN is deprecated" in capsys.readouterr().err


def test_deprecated_env_token_name_prefers_irupkg_token_when_both_set(bare_config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IRU_API_URL", "tenant.api.iru.com")
    monkeypatch.delenv("IRUPKG_TOKEN_NAME", raising=False)
    monkeypatch.delenv("KANDJI_TOKEN_NAME", raising=False)
    monkeypatch.setenv("IRUPKG_TOKEN", "new-secret")
    monkeypatch.setenv("KANDJI_TOKEN", "legacy-secret")
    bare_config.parent_dir = str(tmp_path)

    result = bare_config._read_config("config.json")

    assert result["iru"]["token_name"] == "IRUPKG_TOKEN"
    assert "KANDJI_TOKEN" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _deprecated_config_key -- deprecation shim for config.json "kandji" key
# ---------------------------------------------------------------------------


def test_deprecated_config_key_warns_on_legacy_key(bare_config, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("IRU_API_URL", raising=False)
    monkeypatch.delenv("KANDJI_API_URL", raising=False)
    legacy_config = {
        "kandji": {"api_url": "https://test.api.iru.com", "token_name": "IRUPKG_TOKEN"},
        "token_keystore": {"environment": True, "keychain": False},
        "li_enforcement": {"delays": {"prod": 5, "test": 0}, "type": "audit_enforce"},
        "slack": {"enabled": False, "webhook_name": "SLACK_TOKEN"},
        "use_package_map": False,
        "zz_defaults": {
            "auto_create_app": True,
            "dry_run": False,
            "dynamic_lookup": False,
            "new_app_naming": "APPNAME (irupkg)",
            "self_service_category": "Apps",
            "test_self_service_category": "Utilities",
            "unzip_location": "/Applications",
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(legacy_config))
    bare_config.parent_dir = str(tmp_path)
    bare_config.config_file = "config.json"
    bare_config.package_map_file = "package_map.json"
    bare_config.audit_script = "audit_app_and_version.zsh"
    bare_config.test_app, bare_config.prod_app = False, False
    bare_config.temp_dir = bare_config.tmp_pkg_path = bare_config.tmp_dmg_mount = None

    bare_config.iru_config = bare_config._read_config("config.json")
    if "iru" in bare_config.iru_config:
        pass
    elif "kandji" in bare_config.iru_config:
        _deprecated_config_key("kandji", "iru")

    stderr = capsys.readouterr().err
    assert "config.json key 'kandji' is deprecated" in stderr


def test_new_iru_config_key_no_warning(bare_config, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("IRU_API_URL", raising=False)
    monkeypatch.delenv("KANDJI_API_URL", raising=False)
    new_config = {
        "iru": {"api_url": "https://test.api.iru.com", "token_name": "IRUPKG_TOKEN"},
        "token_keystore": {"environment": True, "keychain": False},
        "li_enforcement": {"delays": {"prod": 5, "test": 0}, "type": "audit_enforce"},
        "slack": {"enabled": False, "webhook_name": "SLACK_TOKEN"},
        "use_package_map": False,
        "zz_defaults": {
            "auto_create_app": True,
            "dry_run": False,
            "dynamic_lookup": False,
            "new_app_naming": "APPNAME (irupkg)",
            "self_service_category": "Apps",
            "test_self_service_category": "Utilities",
            "unzip_location": "/Applications",
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(new_config))
    bare_config.parent_dir = str(tmp_path)

    bare_config.iru_config = bare_config._read_config("config.json")
    if "iru" in bare_config.iru_config:
        pass
    elif "kandji" in bare_config.iru_config:
        _deprecated_config_key("kandji", "iru")

    stderr = capsys.readouterr().err
    assert "deprecated" not in stderr


# ---------------------------------------------------------------------------
# Configurator._set_iru_config -- host detection, migration check, tenant URL
# ---------------------------------------------------------------------------


def _setup_for_iru_config(obj, api_url, mocker):
    obj.iru_api_url = api_url
    obj.irupkg_token_name = "IRUPKG_TOKEN"
    obj.token_keystores = {"environment": True, "keychain": False}
    mocker.patch.object(obj, "_retrieve_token", return_value="fake-token")


@pytest.mark.parametrize(
    ("api_url", "expected_tenant"),
    [
        pytest.param("https://tenant.api.iru.com", "https://tenant.iru.com", id="iru-com-us"),
        pytest.param("https://tenant.api.eu.iru.com", "https://tenant.iru.com", id="iru-com-eu"),
    ],
)
def test_set_iru_config_iru_com_tenant_url(combined_obj, mocker, api_url, expected_tenant):
    _setup_for_iru_config(combined_obj, api_url, mocker)
    mock_get = mocker.patch("requests.get")

    combined_obj._set_iru_config()

    assert combined_obj.tenant_url == expected_tenant
    mock_get.assert_not_called()


@pytest.mark.parametrize(
    ("api_url", "migration_status", "expected_tenant", "expected_check_url"),
    [
        pytest.param(
            "https://tenant.api.kandji.io",
            "COMPLETED",
            "https://tenant.iru.com",
            "https://tenant.gateway.kandji.io/main-backend/app/v1/company/auth-migration-status",
            id="kandji-io-us-migrated",
        ),
        pytest.param(
            "https://tenant.api.eu.kandji.io",
            "STARTED",
            "https://tenant.iru.com",
            "https://tenant.gateway.eu.kandji.io/main-backend/app/v1/company/auth-migration-status",
            id="kandji-io-eu-migrated",
        ),
        pytest.param(
            "https://tenant.api.kandji.io",
            None,
            "https://tenant.kandji.io",
            "https://tenant.gateway.kandji.io/main-backend/app/v1/company/auth-migration-status",
            id="kandji-io-us-unmigrated",
        ),
        pytest.param(
            "https://tenant.api.eu.kandji.io",
            None,
            "https://tenant.eu.kandji.io",
            "https://tenant.gateway.eu.kandji.io/main-backend/app/v1/company/auth-migration-status",
            id="kandji-io-eu-unmigrated",
        ),
    ],
)
def test_set_iru_config_kandji_io_migration(
    combined_obj, mocker, fake_response_factory, caplog,
    api_url, migration_status, expected_tenant, expected_check_url,
):
    _setup_for_iru_config(combined_obj, api_url, mocker)
    body = {"auth_migration_status": migration_status} if migration_status else {}
    mock_get = mocker.patch("requests.get", return_value=fake_response_factory(200, body))

    with caplog.at_level(logging.INFO, logger="irupkg.helpers.configs"):
        combined_obj._set_iru_config()

    assert combined_obj.tenant_url == expected_tenant
    assert mock_get.call_args.kwargs.get("url") == expected_check_url
    if migration_status:
        assert "deprecated kandji.io" in caplog.text
    else:
        assert "not-yet-migrated tenant" in caplog.text


def test_set_iru_config_gateway_unreachable(combined_obj, mocker, caplog):
    _setup_for_iru_config(combined_obj, "https://tenant.api.kandji.io", mocker)
    mocker.patch("requests.get", side_effect=requests.RequestException("timeout"))

    with caplog.at_level(logging.INFO, logger="irupkg.helpers.configs"):
        combined_obj._set_iru_config()

    assert combined_obj.tenant_url == "https://tenant.kandji.io"
    assert "gateway unreachable" in caplog.text


def test_set_iru_config_tenant_not_found_exits(combined_obj, mocker, fake_response_factory):
    _setup_for_iru_config(combined_obj, "https://tenant.api.kandji.io", mocker)
    mocker.patch("requests.get", return_value=fake_response_factory(200, {"error": "tenantNotFound"}))

    with pytest.raises(SystemExit):
        combined_obj._set_iru_config()
