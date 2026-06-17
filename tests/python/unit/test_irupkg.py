import json
import os
import platform

import pytest
from irupkg.irupkg import (
    Irupkg,
    IrupkgError,
    IrupkgResult,
    PackageOptions,
    _run,
    main,
    process_brew,
    process_pkg,
)


@pytest.fixture
def make_irupkg(tmp_path):
    def _make(filename="app.pkg", opts=None):
        pkg = tmp_path / filename
        pkg.write_bytes(b"xar!")
        return Irupkg(str(pkg), opts=opts, parent_dir=tmp_path)

    return _make


@pytest.fixture
def make_configured_irupkg(make_irupkg):
    """irupkg instance pre-populated with the attrs create/update API payload
    paths read at runtime. Overrides win over defaults."""

    def _make(**overrides):
        kp = make_irupkg()
        defaults = {
            "install_type": "zip",
            "unzip_location": "/Applications",
            "custom_app_name": "MyApp (irupkg)",
            "custom_app_enforcement": "no_enforcement",
            "test_app": False,
            "ss_category_id": "ss-id-123",
            "api_custom_apps_url": "https://test.api.iru.com/api/v1/library/custom-apps",
            "auth_headers": {"Authorization": "Bearer x"},
            "params": {},
            "dry_run": False,
            "s3_key": "s3-key-abc",
            "pkg_name": "app.zip",
        }
        for attr, value in {**defaults, **overrides}.items():
            setattr(kp, attr, value)
        return kp

    return _make


# ---------------------------------------------------------------------------
# irupkg.__init__
# ---------------------------------------------------------------------------


def test_init_raises_irupkg_error_when_path_missing():
    with pytest.raises(IrupkgError, match="does not exist"):
        Irupkg(str(os.path.join("nonexistent", "does-not-exist.pkg")))


@pytest.mark.parametrize(
    ("testname", "prod_name_follows_name"),
    [
        pytest.param("TestApp Test", True, id="testname-given-sets-prod-name"),
        pytest.param(None, False, id="no-testname-leaves-prod-name-none"),
    ],
)
def test_init_derives_prod_name_from_testname(make_irupkg, testname, prod_name_follows_name):
    name = "TestApp"
    kp = make_irupkg(opts=PackageOptions(name=name, testname=testname))
    assert kp.arg_prod_name == (name if prod_name_follows_name else None)


# ---------------------------------------------------------------------------
# _run (internal CLI implementation -- tested directly to avoid sys.argv)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "match"),
    [
        pytest.param([], "No PKG/DMG path or Homebrew cask provided", id="no_pkg_or_brew"),
        pytest.param(["-p", "/a.pkg", "-p", "/b.pkg", "-n", "MyApp"], "ambiguous", id="ambiguous_multi_item"),
        pytest.param(["--not-a-real-flag"], "Unrecognized arguments", id="unrecognized_args"),
    ],
)
def test_run_raises_irupkg_error(args, match):
    with pytest.raises(IrupkgError, match=match):
        _run(args)


def test_run_raises_irupkg_error_when_superuser(monkeypatch, mocker):
    mocker.patch("irupkg.irupkg.os.geteuid", return_value=0)
    monkeypatch.delenv("ENV_KEYSTORE", raising=False)
    with pytest.raises(IrupkgError, match="superuser"):
        _run([])


def test_run_skips_superuser_guard_when_env_keystore_truthy(monkeypatch, mocker):
    # Truthy ENV_KEYSTORE must clear the no-root guard; assert we reach the
    # downstream "no PKG/cask" error rather than the superuser one.
    mocker.patch("irupkg.irupkg.os.geteuid", return_value=0)
    monkeypatch.setenv("ENV_KEYSTORE", "1")
    with pytest.raises(IrupkgError, match="No PKG/DMG path or Homebrew cask provided"):
        _run([])


def test_main_converts_irupkg_error_to_sys_exit(monkeypatch):
    # Simulate CLI invocation with no args -- _run() raises IrupkgError, main() exits 1
    monkeypatch.setattr("sys.argv", ["irupkg"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


@pytest.mark.skipif(platform.system() != "Darwin", reason="setup.zsh is only invoked on Darwin")
def test_setup_mode_materializes_packaged_setup_zsh(mocker, tmp_path):
    """`irupkg --setup` copies the packaged setup.zsh into parent_dir, seeds a default config.json, and invokes it.

    Guards the uv-tool-install path where the .pkg postinstall never ran -- without
    config.json on disk, setup.zsh's plutil reads would all error out.
    """
    mocker.patch("irupkg.irupkg._resolve_parent_dir", return_value=tmp_path)
    mocker.patch("irupkg.irupkg.os.geteuid", return_value=1000)
    fake_run = mocker.patch("irupkg.irupkg.subprocess.run")

    _run(["--setup"])

    materialized = tmp_path / "setup.zsh"
    assert materialized.exists()
    seeded = tmp_path / "config.json"
    assert seeded.exists()
    assert json.loads(seeded.read_text())["iru"]["token_name"] == "IRUPKG_TOKEN"
    fake_run.assert_called_once_with(["/bin/zsh", str(materialized)], check=False)


# ---------------------------------------------------------------------------
# Public API: process_pkg / process_brew
# ---------------------------------------------------------------------------


def test_process_pkg_returns_failed_result_on_missing_path(tmp_path):
    missing = tmp_path / "nope.pkg"
    result = process_pkg(missing, parent_dir=tmp_path)
    assert result.action == "failed"
    assert result.source == str(missing)
    assert "does not exist" in result.error


def test_process_brew_returns_failed_result_when_fetch_fails(tmp_path, mocker):
    mocker.patch("irupkg.irupkg.source_from_brew", return_value=None)
    result = process_brew("not-a-real-cask", parent_dir=tmp_path)
    assert result.action == "failed"
    assert result.source == "not-a-real-cask"
    assert "not-a-real-cask" in result.error


def test_irupkg_main_reports_skipped_when_all_iterations_short_circuit(make_irupkg, mocker):
    kp = make_irupkg(opts=PackageOptions(name="Firefox"))

    def fake_get_metadata(self):
        self.audit_script = "audit_app_and_version.zsh"

    def fake_populate(self):
        self.custom_app_enforcement = "no_enforcement"
        self.app_names = {"prod_name": "Firefox (irupkg)"}
        self.dry_run = False

    def fake_create_update(self):
        # Mimic the shasum-match short-circuit in update_custom_app
        self.skipped_iterations += 1

    mocker.patch.object(Irupkg, "get_install_media_metadata", autospec=True, side_effect=fake_get_metadata)
    mocker.patch.object(Irupkg, "populate_from_config", autospec=True, side_effect=fake_populate)
    mocker.patch.object(Irupkg, "iru_customize_create_update", autospec=True, side_effect=fake_create_update)

    result = kp.main()

    assert result.action == "skipped"
    assert result.error is None


def test_process_brew_labels_source_as_cask_name(tmp_path, mocker):
    pkg = tmp_path / "downloaded.pkg"
    pkg.write_bytes(b"xar!")
    mocker.patch("irupkg.irupkg.source_from_brew", return_value=str(pkg))
    mocker.patch(
        "irupkg.irupkg.Irupkg.main",
        return_value=IrupkgResult(source=str(pkg), action="succeeded"),
    )

    result = process_brew("firefox", parent_dir=tmp_path)

    # process_brew should overwrite the result source with the cask name
    assert result.source == "firefox"
    assert result.action == "succeeded"


# ---------------------------------------------------------------------------
# create_custom_app / update_custom_app: ZIP install_type payload propagation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("install_type", "expect_unzip_location"),
    [
        pytest.param("zip", True, id="zip-propagates-unzip_location"),
        pytest.param("package", False, id="non-zip-omits-unzip_location"),
    ],
)
def test_create_custom_app_payload_includes_unzip_location_only_for_zip(
    make_configured_irupkg, mocker, fake_response_factory, install_type, expect_unzip_location
):
    kp = make_configured_irupkg(install_type=install_type)

    mocker.patch.object(Irupkg, "upload_custom_app", return_value=True)
    mocker.patch.object(Irupkg, "_validate_response", return_value=True)
    post_mock = mocker.patch(
        "irupkg.irupkg.requests.post",
        return_value=fake_response_factory(status_code=201, body={"id": "x", "name": kp.custom_app_name}),
    )

    assert kp.create_custom_app() is True

    payload = post_mock.call_args.kwargs["json"]
    assert payload.get("unzip_location") == (kp.unzip_location if expect_unzip_location else None)


def test_update_custom_app_zip_propagates_unzip_location_in_patch_payload(
    make_configured_irupkg, mocker, fake_response_factory
):
    kp = make_configured_irupkg(install_type="zip")
    existing_li = {
        "name": kp.custom_app_name,
        "id": "li-uuid-789",
        "install_enforcement": "no_enforcement",
        "sha256": "remote-sha-differs",
    }

    mocker.patch.object(Irupkg, "_validate_response", return_value=True)
    mocker.patch.object(Irupkg, "_find_lib_item_match", return_value=existing_li)
    mocker.patch("irupkg.irupkg.sha256_file", return_value="local-sha")
    mocker.patch.object(Irupkg, "upload_custom_app", return_value=True)
    mocker.patch(
        "irupkg.irupkg.requests.get",
        return_value=fake_response_factory(status_code=200, body={"results": []}),
    )
    patch_mock = mocker.patch(
        "irupkg.irupkg.requests.patch",
        return_value=fake_response_factory(status_code=200, body={"id": existing_li["id"]}),
    )

    assert kp.update_custom_app() is True

    payload = patch_mock.call_args.kwargs["json"]
    assert payload["unzip_location"] == kp.unzip_location


_ALL_DIR_VARS = ["IRUPKG_LOCAL_DIR", "KPKG_LOCAL_DIR", "IRUPKG_CONFIG_DIR", "KPKG_CONFIG_DIR"]


@pytest.mark.parametrize("active_vars,expected_key,expect_warning_substr", [
    ({"IRUPKG_LOCAL_DIR": "a"},                              "a", None),
    ({"KPKG_LOCAL_DIR": "b"},                               "b", "KPKG_LOCAL_DIR is deprecated"),
    ({"IRUPKG_LOCAL_DIR": "a", "KPKG_LOCAL_DIR": "b"},      "a", None),
    ({"IRUPKG_CONFIG_DIR": "c"},                            "c", None),
    ({"KPKG_CONFIG_DIR": "d"},                              "d", "KPKG_CONFIG_DIR is deprecated"),
])
def test_resolve_parent_dir_env_var_precedence(
    tmp_path, monkeypatch, capsys, active_vars, expected_key, expect_warning_substr
):
    from irupkg.irupkg import _resolve_parent_dir
    for var in _ALL_DIR_VARS:
        monkeypatch.delenv(var, raising=False)
    for var, key in active_vars.items():
        monkeypatch.setenv(var, str(tmp_path / key))
    assert _resolve_parent_dir() == (tmp_path / expected_key).resolve()
    err = capsys.readouterr().err
    if expect_warning_substr:
        assert expect_warning_substr in err
    else:
        assert err == ""


def test_resolve_parent_dir_legacy_darwin_dir_migration(tmp_path, monkeypatch, capsys):
    from pathlib import Path
    from irupkg.irupkg import _resolve_parent_dir
    for var in _ALL_DIR_VARS:
        monkeypatch.delenv(var, raising=False)
    old_path = tmp_path / "Library" / "KandjiPackages"
    new_path = tmp_path / "Library" / "IruPackages"
    old_path.mkdir(parents=True)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert _resolve_parent_dir() == new_path
    assert not old_path.exists()
    assert new_path.exists()
    assert "Migrated data directory" in capsys.readouterr().err


def test_resolve_parent_dir_legacy_darwin_dir_fallback(tmp_path, monkeypatch, capsys):
    import irupkg.irupkg as _mod
    from pathlib import Path
    from irupkg.irupkg import _resolve_parent_dir
    for var in _ALL_DIR_VARS:
        monkeypatch.delenv(var, raising=False)
    old_path = tmp_path / "Library" / "KandjiPackages"
    old_path.mkdir(parents=True)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(_mod, "_warned_legacy", False)

    def _raise(self, target):
        raise OSError("cross-device link")

    monkeypatch.setattr(Path, "rename", _raise)
    assert _resolve_parent_dir() == old_path
    assert "data directory has moved" in capsys.readouterr().err
    # warning fires only once per process
    _resolve_parent_dir()
    assert capsys.readouterr().err == ""


def test_resolve_parent_dir_deprecation_warning_fires_once(tmp_path, monkeypatch, capsys):
    from irupkg.irupkg import _resolve_parent_dir
    for var in _ALL_DIR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KPKG_LOCAL_DIR", str(tmp_path))
    _resolve_parent_dir()
    _resolve_parent_dir()
    assert capsys.readouterr().err.count("KPKG_LOCAL_DIR is deprecated") == 1


def test_resolve_parent_dir_neither_set_returns_new_default(tmp_path, monkeypatch, capsys):
    from pathlib import Path
    from irupkg.irupkg import _resolve_parent_dir
    for var in _ALL_DIR_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Neither old nor new dir exists — should return new default with no warning
    result = _resolve_parent_dir()
    assert result == tmp_path / "Library" / "IruPackages"
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# kpkg shim (_compat._kpkg_alias) -- invoking kpkg runs irupkg and emits
# exactly one deprecation line to stderr.
# ---------------------------------------------------------------------------


def test_kpkg_shim_warns_and_delegates_to_main(monkeypatch, capsys):
    from irupkg._compat import _kpkg_alias
    monkeypatch.setattr("sys.argv", ["kpkg", "--version"])
    _kpkg_alias()
    stderr = capsys.readouterr().err
    assert stderr.count("'kpkg' is deprecated") == 1
