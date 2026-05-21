import json

import pytest
from kpkg.helpers.configs import Configurator


@pytest.fixture
def minimal_config():
    return {
        "kandji": {"api_url": "https://test.api.kandji.io", "token_name": "KANDJI_TOKEN"},
        "token_keystore": {"environment": True, "keychain": False},
        "li_enforcement": {"delays": {"prod": 5, "test": 0}, "type": "audit_enforce"},
        "slack": {"enabled": False, "webhook_name": "SLACK_TOKEN"},
        "use_package_map": False,
        "zz_defaults": {
            "auto_create_app": True,
            "dry_run": False,
            "dynamic_lookup": False,
            "new_app_naming": "APPNAME (kpkg)",
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
# Kandji API enum and vice versa.
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
    api_url = "https://generated.api.kandji.io"
    monkeypatch.setenv("KANDJI_API_URL", api_url)
    monkeypatch.setenv("KANDJI_TOKEN_NAME", "GENERATED_TOKEN")
    bare_config.parent_dir = str(tmp_path)

    result = bare_config._read_config("config.json")

    assert result["kandji"]["api_url"] == api_url
    assert result["kandji"]["token_name"] == "GENERATED_TOKEN"
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
    bare_config.kpkg_config = minimal_config
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
    mocker.patch("kpkg.helpers.configs.platform.system", return_value="Linux")
    bare_config.get_install_media_metadata()
    assert bare_config.pkg_path_name == expected


# ---------------------------------------------------------------------------
# Configurator._set_custom_name
# ---------------------------------------------------------------------------


def test_set_custom_name_default_template(bare_config):
    install_name = "Firefox"
    template = "APPNAME (kpkg)"
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
    combined_obj.kandji_api_prefix = "https://test.api.kandji.io/api/v1"
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
