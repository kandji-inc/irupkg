import hashlib
import os
import platform

import pytest
from irupkg.helpers.utils import Utilities, _warned_legacy_keychain, env_keystore_enabled, sha256_file


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
# sha256_file -- directly relevant to the Iru identity-check that drives
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
        pytest.param("my.iru.com", "https://my.iru.com", id="bare-hostname"),
        pytest.param("http://my.iru.com", "https://my.iru.com", id="upgrades-http"),
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
    monkeypatch.setenv("IRUPKG_TOKEN", "tok_env")
    assert bare_utils._retrieve_token("IRUPKG_TOKEN") == "tok_env"


@pytest.mark.skipif(platform.system() != "Darwin", reason="ENV fallback is omitted only on Darwin")
def test_retrieve_token_skips_env_fallback_on_darwin(bare_utils, monkeypatch):
    bare_utils.token_keystores = {"environment": False, "keychain": False}
    monkeypatch.setenv("IRUPKG_TOKEN", "tok_env")
    assert bare_utils._retrieve_token("IRUPKG_TOKEN") is None


def test_retrieve_token_returns_none_when_env_unset_and_keystores_disabled(bare_utils, monkeypatch):
    bare_utils.token_keystores = {"environment": False, "keychain": False}
    monkeypatch.delenv("IRUPKG_TOKEN", raising=False)
    monkeypatch.delenv("irupkg_token", raising=False)
    assert bare_utils._retrieve_token("IRUPKG_TOKEN") is None


def test_retrieve_token_calls_keychain_when_env_absent(bare_utils, monkeypatch, mocker):
    bare_utils.token_keystores = {"environment": True, "keychain": True}
    monkeypatch.delenv("IRUPKG_TOKEN", raising=False)
    monkeypatch.delenv("irupkg_token", raising=False)
    mocker.patch("irupkg.helpers.utils.Utilities._keychain_token_get", return_value="keychain_val")
    assert bare_utils._retrieve_token("IRUPKG_TOKEN") == "keychain_val"


# ---------------------------------------------------------------------------
# Utilities._keychain_token_get -- dual-account lookup: tries 'irupkg' first,
# falls back to legacy 'kpkg' account with a one-time warning per token name.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_legacy_keychain_warnings():
    _warned_legacy_keychain.clear()
    yield
    _warned_legacy_keychain.clear()


def test_keychain_token_get_returns_irupkg_account(bare_utils, mocker, capsys):
    mocker.patch.object(bare_utils, "_run_command", return_value="secret_val")
    result = bare_utils._keychain_token_get("MY_TOKEN")
    assert result == "secret_val"
    bare_utils._run_command.assert_called_once_with(
        "/usr/bin/security find-generic-password -w -s MY_TOKEN -a 'irupkg'"
    )
    assert capsys.readouterr().err == ""


def test_keychain_token_get_falls_back_to_kpkg_account_with_warning(bare_utils, mocker, capsys):
    mocker.patch.object(
        bare_utils, "_run_command",
        side_effect=[False, "legacy_val"],
    )
    result = bare_utils._keychain_token_get("MY_TOKEN")
    assert result == "legacy_val"
    assert capsys.readouterr().err.count("legacy 'kpkg' account") == 1


def test_keychain_token_get_warning_fires_once_per_token(bare_utils, mocker, capsys):
    mocker.patch.object(bare_utils, "_run_command", side_effect=[False, "v1", False, "v2"])
    bare_utils._keychain_token_get("MY_TOKEN")
    bare_utils._keychain_token_get("MY_TOKEN")
    assert capsys.readouterr().err.count("legacy 'kpkg' account") == 1


def test_keychain_token_get_returns_none_when_both_accounts_missing(bare_utils, mocker):
    mocker.patch.object(bare_utils, "_run_command", return_value=False)
    assert bare_utils._keychain_token_get("MY_TOKEN") is None


# ---------------------------------------------------------------------------
# Utilities._ensure_audit_script -- resolves the audit script through parent_dir,
# CWD/CWD-parent, then the packaged copy from the irupkg.scripts subpackage.
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


MATCHING_APP = {"id": "abc-123", "name": "MyApp (irupkg)", "file_key": "myapp.pkg"}
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
    bare_utils.custom_app_name = "MyApp (irupkg)"
    bare_utils.default_auto_create = True
    bare_utils.default_dynamic_lookup = False
    assert bare_utils._find_lib_item_match() == expected


def test_find_lib_item_match_returns_none_on_multiple_matches(bare_utils, mocker):
    app1 = {
        "id": "id-1",
        "name": "MyApp (irupkg)",
        "file_key": "myapp-1.0.pkg",
        "created_at": "2024-01-01T00:00:00.000Z",
        "file_updated": "2024-01-01",
    }
    app2 = {**app1, "id": "id-2", "file_key": "myapp-2.0.pkg", "created_at": "2024-02-01T00:00:00.000Z"}
    bare_utils.custom_apps = [app1, app2]
    bare_utils.custom_app_name = "MyApp (irupkg)"
    bare_utils.default_auto_create = False
    bare_utils.default_dynamic_lookup = False
    bare_utils.ss_category_id = None
    bare_utils.custom_app_enforcement = "continuously_enforce"
    bare_utils.tenant_url = "https://test.iru.com"
    mocker.patch.object(bare_utils, "slack_notify")

    assert bare_utils._find_lib_item_match() is None
