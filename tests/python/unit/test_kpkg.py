import json
import os
import platform

import pytest
from kpkg.kpkg import (
    KPKG,
    KpkgError,
    KpkgResult,
    PackageOptions,
    _run,
    main,
    process_brew,
    process_pkg,
)


@pytest.fixture
def make_kpkg(tmp_path):
    def _make(filename="app.pkg", opts=None):
        pkg = tmp_path / filename
        pkg.write_bytes(b"xar!")
        return KPKG(str(pkg), opts=opts, parent_dir=tmp_path)

    return _make


@pytest.fixture
def make_configured_kpkg(make_kpkg):
    """KPKG instance pre-populated with the attrs create/update API payload
    paths read at runtime. Overrides win over defaults."""

    def _make(**overrides):
        kp = make_kpkg()
        defaults = {
            "install_type": "zip",
            "unzip_location": "/Applications",
            "custom_app_name": "MyApp (kpkg)",
            "custom_app_enforcement": "no_enforcement",
            "test_app": False,
            "ss_category_id": "ss-id-123",
            "api_custom_apps_url": "https://test.api.kandji.io/api/v1/library/custom-apps",
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
# KPKG.__init__
# ---------------------------------------------------------------------------


def test_init_raises_kpkg_error_when_path_missing():
    with pytest.raises(KpkgError, match="does not exist"):
        KPKG(str(os.path.join("nonexistent", "does-not-exist.pkg")))


@pytest.mark.parametrize(
    ("testname", "prod_name_follows_name"),
    [
        pytest.param("TestApp Test", True, id="testname-given-sets-prod-name"),
        pytest.param(None, False, id="no-testname-leaves-prod-name-none"),
    ],
)
def test_init_derives_prod_name_from_testname(make_kpkg, testname, prod_name_follows_name):
    name = "TestApp"
    kp = make_kpkg(opts=PackageOptions(name=name, testname=testname))
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
def test_run_raises_kpkg_error(args, match):
    with pytest.raises(KpkgError, match=match):
        _run(args)


def test_run_raises_kpkg_error_when_superuser(monkeypatch, mocker):
    mocker.patch("kpkg.kpkg.os.geteuid", return_value=0)
    monkeypatch.delenv("ENV_KEYSTORE", raising=False)
    with pytest.raises(KpkgError, match="superuser"):
        _run([])


def test_run_skips_superuser_guard_when_env_keystore_truthy(monkeypatch, mocker):
    # Truthy ENV_KEYSTORE must clear the no-root guard; assert we reach the
    # downstream "no PKG/cask" error rather than the superuser one.
    mocker.patch("kpkg.kpkg.os.geteuid", return_value=0)
    monkeypatch.setenv("ENV_KEYSTORE", "1")
    with pytest.raises(KpkgError, match="No PKG/DMG path or Homebrew cask provided"):
        _run([])


def test_main_converts_kpkg_error_to_sys_exit(monkeypatch):
    # Simulate CLI invocation with no args -- _run() raises KpkgError, main() exits 1
    monkeypatch.setattr("sys.argv", ["kpkg"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


@pytest.mark.skipif(platform.system() != "Darwin", reason="setup.zsh is only invoked on Darwin")
def test_setup_mode_materializes_packaged_setup_zsh(mocker, tmp_path):
    """`kpkg --setup` copies the packaged setup.zsh into parent_dir, seeds a default config.json, and invokes it.

    Guards the uv-tool-install path where the .pkg postinstall never ran -- without
    config.json on disk, setup.zsh's plutil reads would all error out.
    """
    mocker.patch("kpkg.kpkg._resolve_parent_dir", return_value=tmp_path)
    mocker.patch("kpkg.kpkg.os.geteuid", return_value=1000)
    fake_run = mocker.patch("kpkg.kpkg.subprocess.run")

    _run(["--setup"])

    materialized = tmp_path / "setup.zsh"
    assert materialized.exists()
    seeded = tmp_path / "config.json"
    assert seeded.exists()
    assert json.loads(seeded.read_text())["kandji"]["token_name"] == "KANDJI_TOKEN"
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
    mocker.patch("kpkg.kpkg.source_from_brew", return_value=None)
    result = process_brew("not-a-real-cask", parent_dir=tmp_path)
    assert result.action == "failed"
    assert result.source == "not-a-real-cask"
    assert "not-a-real-cask" in result.error


def test_kpkg_main_reports_skipped_when_all_iterations_short_circuit(make_kpkg, mocker):
    kp = make_kpkg(opts=PackageOptions(name="Firefox"))

    def fake_get_metadata(self):
        self.audit_script = "audit_app_and_version.zsh"

    def fake_populate(self):
        self.custom_app_enforcement = "no_enforcement"
        self.app_names = {"prod_name": "Firefox (kpkg)"}
        self.dry_run = False

    def fake_create_update(self):
        # Mimic the shasum-match short-circuit in update_custom_app
        self.skipped_iterations += 1

    mocker.patch.object(KPKG, "get_install_media_metadata", autospec=True, side_effect=fake_get_metadata)
    mocker.patch.object(KPKG, "populate_from_config", autospec=True, side_effect=fake_populate)
    mocker.patch.object(KPKG, "kandji_customize_create_update", autospec=True, side_effect=fake_create_update)

    result = kp.main()

    assert result.action == "skipped"
    assert result.error is None


def test_process_brew_labels_source_as_cask_name(tmp_path, mocker):
    pkg = tmp_path / "downloaded.pkg"
    pkg.write_bytes(b"xar!")
    mocker.patch("kpkg.kpkg.source_from_brew", return_value=str(pkg))
    mocker.patch(
        "kpkg.kpkg.KPKG.main",
        return_value=KpkgResult(source=str(pkg), action="succeeded"),
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
    make_configured_kpkg, mocker, fake_response_factory, install_type, expect_unzip_location
):
    kp = make_configured_kpkg(install_type=install_type)

    mocker.patch.object(KPKG, "upload_custom_app", return_value=True)
    mocker.patch.object(KPKG, "_validate_response", return_value=True)
    post_mock = mocker.patch(
        "kpkg.kpkg.requests.post",
        return_value=fake_response_factory(status_code=201, body={"id": "x", "name": kp.custom_app_name}),
    )

    assert kp.create_custom_app() is True

    payload = post_mock.call_args.kwargs["json"]
    assert payload.get("unzip_location") == (kp.unzip_location if expect_unzip_location else None)


def test_update_custom_app_zip_propagates_unzip_location_in_patch_payload(
    make_configured_kpkg, mocker, fake_response_factory
):
    kp = make_configured_kpkg(install_type="zip")
    existing_li = {
        "name": kp.custom_app_name,
        "id": "li-uuid-789",
        "install_enforcement": "no_enforcement",
        "sha256": "remote-sha-differs",
    }

    mocker.patch.object(KPKG, "_validate_response", return_value=True)
    mocker.patch.object(KPKG, "_find_lib_item_match", return_value=existing_li)
    mocker.patch("kpkg.kpkg.sha256_file", return_value="local-sha")
    mocker.patch.object(KPKG, "upload_custom_app", return_value=True)
    mocker.patch(
        "kpkg.kpkg.requests.get",
        return_value=fake_response_factory(status_code=200, body={"results": []}),
    )
    patch_mock = mocker.patch(
        "kpkg.kpkg.requests.patch",
        return_value=fake_response_factory(status_code=200, body={"id": existing_li["id"]}),
    )

    assert kp.update_custom_app() is True

    payload = patch_mock.call_args.kwargs["json"]
    assert payload["unzip_location"] == kp.unzip_location
