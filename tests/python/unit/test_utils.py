import hashlib
import os
import platform

import pytest
from kpkg.helpers.utils import Utilities, env_keystore_enabled, sha256_file


@pytest.fixture
def bare_utils():
    return Utilities()


# ---------------------------------------------------------------------------
# env_keystore_enabled -- gates the root-execution bypass and the keystore
# override. Presence-only checks would let an unrelated process that exports
# an empty ENV_KEYSTORE silently grant both; require an explicit truthy value.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("1", True, id="one"),
        pytest.param("true", True, id="true-lower"),
        pytest.param("TRUE", True, id="true-upper"),
        pytest.param("yes", True, id="yes"),
        pytest.param("on", True, id="on"),
        pytest.param(" 1 ", True, id="padded"),
        pytest.param(None, False, id="unset"),
        pytest.param("", False, id="empty-string"),
        pytest.param("0", False, id="zero"),
    ],
)
def test_env_keystore_enabled(value, expected, monkeypatch):
    if value is None:
        monkeypatch.delenv("ENV_KEYSTORE", raising=False)
    else:
        monkeypatch.setenv("ENV_KEYSTORE", value)
    assert env_keystore_enabled() is expected


# ---------------------------------------------------------------------------
# sha256_file -- directly relevant to the Kandji identity-check that drives
# upload/skip decisions; must match hashlib for any payload size.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"hello kpkg", id="known-content"),
        pytest.param(b"", id="empty"),
        pytest.param(b"x" * (4096 * 3 + 17), id="multi-chunk"),
    ],
)
def test_sha256_file(tmp_path, data):
    f = tmp_path / "test.bin"
    f.write_bytes(data)
    assert sha256_file(str(f)) == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Utilities._ensure_https
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("my.kandji.io", "https://my.kandji.io", id="bare-hostname"),
        pytest.param("http://my.kandji.io", "https://my.kandji.io", id="upgrades-http"),
    ],
)
def test_ensure_https(url, expected, bare_utils):
    assert bare_utils._ensure_https(url) == expected


# ---------------------------------------------------------------------------
# Utilities._retrieve_token -- secret resolution. ENV is the primary source
# when token_keystores["environment"] is true; the final unconditional fallback
# is scoped to non-Darwin so macOS treats config.json as authoritative.
# ---------------------------------------------------------------------------


def test_retrieve_token_reads_env_when_environment_primary(bare_utils, monkeypatch):
    bare_utils.token_keystores = {"environment": True, "keychain": False}
    monkeypatch.setenv("KANDJI_TOKEN", "tok_env")
    assert bare_utils._retrieve_token("KANDJI_TOKEN") == "tok_env"


@pytest.mark.skipif(platform.system() != "Darwin", reason="ENV fallback is omitted only on Darwin")
def test_retrieve_token_skips_env_fallback_on_darwin(bare_utils, monkeypatch):
    bare_utils.token_keystores = {"environment": False, "keychain": False}
    monkeypatch.setenv("KANDJI_TOKEN", "tok_env")
    assert bare_utils._retrieve_token("KANDJI_TOKEN") is None


def test_retrieve_token_returns_none_when_env_unset_and_keystores_disabled(bare_utils, monkeypatch):
    bare_utils.token_keystores = {"environment": False, "keychain": False}
    monkeypatch.delenv("KANDJI_TOKEN", raising=False)
    monkeypatch.delenv("kandji_token", raising=False)
    assert bare_utils._retrieve_token("KANDJI_TOKEN") is None


def test_retrieve_token_calls_keychain_when_env_absent(bare_utils, monkeypatch, mocker):
    bare_utils.token_keystores = {"environment": True, "keychain": True}
    monkeypatch.delenv("KANDJI_TOKEN", raising=False)
    monkeypatch.delenv("kandji_token", raising=False)
    mocker.patch("kpkg.helpers.utils.Utilities._keychain_token_get", return_value="keychain_val")
    assert bare_utils._retrieve_token("KANDJI_TOKEN") == "keychain_val"


# ---------------------------------------------------------------------------
# Utilities._ensure_audit_script -- resolves the audit script through parent_dir,
# CWD/CWD-parent, then the packaged copy from the kpkg.scripts subpackage.
# ---------------------------------------------------------------------------


def test_ensure_audit_script_materializes_packaged_copy(bare_utils, tmp_path, monkeypatch):
    bare_utils.audit_script = "audit_app_and_version.zsh"
    bare_utils.audit_script_path = str(tmp_path / "audit_app_and_version.zsh")
    # Move CWD somewhere that lacks the script so the CWD/CWD-parent fallbacks miss.
    isolated = tmp_path / "elsewhere"
    isolated.mkdir()
    monkeypatch.chdir(isolated)

    bare_utils._ensure_audit_script()

    assert os.path.exists(bare_utils.audit_script_path)


# ---------------------------------------------------------------------------
# Utilities._validate_response
# ---------------------------------------------------------------------------


def test_validate_response_populates_custom_apps_on_get(bare_utils, fake_response_factory):
    resp = fake_response_factory(200, {"results": [{"id": "abc", "name": "TestApp"}]})
    result = bare_utils._validate_response(resp, "get")
    assert result is True
    assert bare_utils.custom_apps == [{"id": "abc", "name": "TestApp"}]


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [
        pytest.param(401, "Unauthorized", id="401-unauthorized"),
        pytest.param(403, "Forbidden", id="403-forbidden"),
    ],
)
def test_validate_response_auth_error_exits(status_code, detail, combined_obj, fake_response_factory, mocker):
    mocker.patch.object(combined_obj, "slack_notify")
    combined_obj.custom_app_name = "TestApp"
    combined_obj.pkg_name = "test.pkg"
    resp = fake_response_factory(status_code, {"detail": detail})
    with pytest.raises(SystemExit) as exc_info:
        combined_obj._validate_response(resp, "get")
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Utilities.slack_notify
# ---------------------------------------------------------------------------


def test_slack_notify_returns_false_when_no_channel(bare_utils):
    bare_utils.slack_channel = None
    assert bare_utils.slack_notify("SUCCESS", "header", "body") is False


# ---------------------------------------------------------------------------
# Utilities._find_lib_item_match -- drives the update-vs-create decision in
# the main flow; each branch returns a distinct sentinel.
# ---------------------------------------------------------------------------


MATCHING_APP = {"id": "abc-123", "name": "MyApp (kpkg)", "file_key": "myapp.pkg"}
NON_MATCHING_APP = {"id": "xyz", "name": "OtherApp", "file_key": "other.pkg"}


@pytest.mark.parametrize(
    ("custom_apps", "expected"),
    [
        pytest.param([MATCHING_APP, NON_MATCHING_APP], MATCHING_APP, id="match-returns-app"),
        pytest.param([NON_MATCHING_APP], False, id="no-match-returns-false"),
    ],
)
def test_find_lib_item_match_single_match_branches(bare_utils, custom_apps, expected):
    bare_utils.custom_apps = custom_apps
    bare_utils.custom_app_name = "MyApp (kpkg)"
    bare_utils.default_auto_create = True
    bare_utils.default_dynamic_lookup = False
    assert bare_utils._find_lib_item_match() == expected


def test_find_lib_item_match_returns_none_on_multiple_matches(bare_utils, mocker):
    app1 = {
        "id": "id-1",
        "name": "MyApp (kpkg)",
        "file_key": "myapp-1.0.pkg",
        "created_at": "2024-01-01T00:00:00.000Z",
        "file_updated": "2024-01-01",
    }
    app2 = {**app1, "id": "id-2", "file_key": "myapp-2.0.pkg", "created_at": "2024-02-01T00:00:00.000Z"}
    bare_utils.custom_apps = [app1, app2]
    bare_utils.custom_app_name = "MyApp (kpkg)"
    bare_utils.default_auto_create = False
    bare_utils.default_dynamic_lookup = False
    bare_utils.ss_category_id = None
    bare_utils.custom_app_enforcement = "continuously_enforce"
    bare_utils.tenant_url = "https://test.kandji.io"
    mocker.patch.object(bare_utils, "slack_notify")

    assert bare_utils._find_lib_item_match() is None
