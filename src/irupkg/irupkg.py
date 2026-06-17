# Created 03/05/24; NRJA
# Updated 04/15/24; NRJA
# Updated 05/21/24; NRJA
# Updated 06/03/26; kandji-danielchapa
################################################################################################
# License Information
################################################################################################
#
# Copyright 2026 Iru, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
# to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or
# substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
# PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
# FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
#
################################################################################################

"""Iru Packages (irupkg): standalone tool for programmatic management of Iru Custom Apps"""

#######################
####### IMPORTS #######
#######################

import argparse
import importlib.resources
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Literal

import requests
from platformdirs import user_data_dir

from .helpers.configs import Configurator, build_default_config
from .helpers.utils import HTTP_TIMEOUT, Utilities, env_keystore_enabled, sha256_file, source_from_brew

###########################
######### LOGGING #########
###########################

log = logging.getLogger(__name__)

_warned_legacy: bool = False

##############################
######### BRANDING ###########
##############################

CLI_NAME = "irupkg"
CLI_BRANDING = "Iru Packages"

##############################
######### EXCEPTIONS #########
##############################


class IrupkgError(Exception):
    """Raised for expected irupkg runtime errors (bad input, missing config, etc.)."""


#############################
######### ARGUMENTS #########
#############################


@dataclass
class IrupkgResult:
    """Outcome of a single irupkg processing run.

    `source` is the original input (a PKG/DMG/ZIP path or a Homebrew cask name).
    `action` is "succeeded" on a normal run, "dry_run" when dry mode was set,
    or "failed" on any error. `pkg_name` is the resolved Iru Custom App name
    when known, and `error` carries the stringified exception on failure.
    """

    source: str
    pkg_name: str | None = None
    action: Literal["succeeded", "skipped", "failed", "dry_run"] = "succeeded"
    error: str | None = None


@dataclass
class PackageOptions:
    """CLI options passed through to irupkg for a single package run."""

    name: str | None = None
    testname: str | None = None
    sscategory: str | None = None
    zzcategory: str | None = None
    dry: bool = False
    create: bool = False
    unzip_location: str | None = None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CLI_NAME,
        description=f"{CLI_BRANDING}: standalone tool for programmatic management of Iru Custom Apps",
    )
    parser.add_argument(
        "-p",
        "--pkg",
        action="append",
        required=False,
        metavar="PATH",
        help="Path to PKG/DMG/ZIP for Iru upload; multiple items can be specified so long as no name/category flags (-n/-t/-s/-z) are passed",
    )
    parser.add_argument(
        "-b",
        "--brew",
        action="append",
        required=False,
        metavar="CASK",
        help="Homebrew cask name which sources PKG/DMG/ZIP; multiple items can be specified so long as no name/category flags (-n/-t/-s/-z) are passed",
    )
    parser.add_argument(
        "-u",
        "--unzip-location",
        action="store",
        required=False,
        dest="unzip_location",
        help="Unzip destination path for ZIP custom apps (default: /Applications)",
    )
    parser.add_argument(
        "-n",
        "--name",
        action="store",
        required=False,
        help="Name of Iru Custom App to create/update",
    )
    parser.add_argument(
        "-t",
        "--testname",
        action="store",
        required=False,
        help="Name of Iru Custom App (test) to create/update",
    )
    parser.add_argument(
        "-s",
        "--sscategory",
        action="store",
        required=False,
        help="Iru Self Service category aligned with --name",
    )
    parser.add_argument(
        "-z",
        "--zzcategory",
        action="store",
        required=False,
        help="Iru Self Service category aligned with --testname",
    )
    parser.add_argument(
        "-c",
        "--create",
        action="store_true",
        required=False,
        default=False,
        help="Creates a new Custom App, even if duplicate entry (by name) already exists",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        required=False,
        default=False,
        help="Sets logging level to debug with maximum verbosity",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        required=False,
        default=False,
        help=f"Returns the current version of {CLI_BRANDING} and exits",
    )
    parser.add_argument(
        "-S",
        "--setup",
        action="store_true",
        required=False,
        default=False,
        help="Interactive config generator; creates config.json and configures token keystore",
    )
    parser.add_argument(
        "-y",
        "--dry",
        action="store_true",
        required=False,
        default=False,
        help="Sets dry run, returning (not executing) changes to stdout as they would have been made in Iru",
    )
    return parser


def format_stdout(body):
    """Formats provided str with #s to create a header"""
    hashed_body = f"####### {body} #######"
    hashed_header_footer = "#" * len(hashed_body)
    hashed_out = f"\n\n{hashed_header_footer}\n{hashed_body}\n{hashed_header_footer}\n"
    return hashed_out


class Irupkg(Configurator, Utilities):
    def __init__(
        self,
        path_to_pkg: str,
        opts: PackageOptions | None = None,
        parent_dir: Path | None = None,
    ) -> None:
        """Creates an object for Iru API interaction.

        Args:
            path_to_pkg: Path to the PKG/DMG/ZIP to upload.
            opts: Upload options. Defaults to PackageOptions() with all defaults.
            parent_dir: irupkg config/data directory. Defaults to the same resolution
                as the CLI: IRUPKG_LOCAL_DIR --> KPKG_LOCAL_DIR --> IRUPKG_CONFIG_DIR --> KPKG_CONFIG_DIR --> macOS dir --> platform default.
        """
        if path_to_pkg is None:
            raise IrupkgError("No PKG path provided (use flag -p/--pkg)")
        if not os.path.exists(path_to_pkg):
            raise IrupkgError(f"Provided path '{path_to_pkg}' does not exist!")
        opts = opts or PackageOptions()
        self.arg_pkg_path = path_to_pkg
        self.parent_dir = parent_dir if parent_dir is not None else _resolve_parent_dir()
        self.arg_app_name = opts.name
        self.arg_test_name = opts.testname
        self.arg_prod_name = opts.name if opts.testname is not None else None
        self.arg_ss_category = opts.sscategory
        self.arg_test_category = opts.zzcategory
        self.arg_dry_run = opts.dry
        self.arg_create_new = opts.create
        self.arg_unzip_location = opts.unzip_location
        self.pkg_path = None
        self.pkg_uploaded = False

    @classmethod
    def from_brew(
        cls,
        cask_name: str,
        opts: PackageOptions | None = None,
        parent_dir: Path | None = None,
    ) -> "Irupkg":
        """Fetch a Homebrew cask and return an Irupkg instance ready to process it.

        Args:
            cask_name: Homebrew cask name (e.g. "firefox").
            opts: Upload options. Defaults to PackageOptions() with all defaults.
            parent_dir: irupkg config/data directory. Auto-resolved when not provided.

        Raises:
            IrupkgError: If the cask cannot be fetched.
        """
        path = source_from_brew(cask_name)
        if path is None:
            raise IrupkgError(f"Failed to fetch Homebrew cask '{cask_name}'")
        return cls(path_to_pkg=path, opts=opts, parent_dir=parent_dir)

    ####################################
    ######### PUBLIC FUNCTIONS #########
    ####################################

    def upload_custom_app(self):
        """Calls func to generate S3 presigned URL (response assigned to self.s3_generated_req)
        Formats presigned URL response to cURL syntax valid for form submission, also appending path to PKG
        Assigns upload form and POST URL to vars for cURL execution
        Runs command and validates output when returning self._validate_response()"""

        def _generate_s3_req():
            """Generates an S3 presigned URL to upload a PKG"""
            post_url = self.api_upload_pkg_url
            form_data = {"name": self.pkg_name}
            response = requests.post(
                post_url, headers=self.auth_headers, params=self.params, json=form_data, timeout=HTTP_TIMEOUT
            )
            return self._validate_response(response, "presign")

        if self.pkg_uploaded is True:
            log.info("PKG already uploaded... Continuing")
            return True

        if not _generate_s3_req():
            return False

        # Assign S3 return data to vars
        upload_url = self.s3_generated_req.get("post_url")
        s3_data = self.s3_generated_req.get("post_data")
        self.s3_key = self.s3_generated_req.get("file_key")

        if self.dry_run is True:
            log.info(f"DRY RUN: Would upload PKG '{self.pkg_path} as POST to '{upload_url}'")
            return True
        log.info(f"Beginning file upload of '{self.pkg_name}'...")
        with open(self.pkg_path, "rb") as pkg_file:
            s3_data["file"] = pkg_file
            # Intentionally untimed: The transfer is monotonic and the connection has its own keepalive/abort behaviour
            # Prefer an honest "let it finish" to a partial upload on a bad day.
            response = requests.post(upload_url, files=s3_data)
        return self._validate_response(response, "upload")

    def create_custom_app(self):
        """Assigns creation data and POST URL to vars for cURL execution
        Runs command and validates output when returning self._validate_response()"""
        # Assign initial data with known vars
        create_data = {
            "name": self.custom_app_name,
            "install_type": self.install_type,
            "install_enforcement": self.custom_app_enforcement,
        }
        if self.custom_app_enforcement == "continuously_enforce":
            with open(self.audit_script_path) as f:
                audit_script = f.read()
            create_data["audit_script"] = audit_script
        elif self.custom_app_enforcement == "no_enforcement":
            # If no enforcement, set to show in Self Service
            create_data["show_in_self_service"] = True
            # Setting Self Service also requires a category
            if self.test_app is True:
                # If test app, assign test category ID
                create_data["self_service_category_id"] = self.test_category_id
            else:
                # Otherwise assign as prod app
                create_data["self_service_category_id"] = self.ss_category_id
        if self.install_type == "zip":
            create_data["unzip_location"] = self.unzip_location
        if self.upload_custom_app() is not True:
            return False
        create_data["file_key"] = self.s3_key
        # Set POST URL
        post_url = self.api_custom_apps_url
        if self.dry_run is True:
            log.info(
                f"DRY RUN: Would create Custom App '{self.custom_app_name}' with POST to '{post_url}' and fields '{create_data}'"
            )
            return True
        response = requests.post(
            post_url, headers=self.auth_headers, params=self.params, json=create_data, timeout=HTTP_TIMEOUT
        )
        return self._validate_response(response, "create")

    def update_custom_app(self):
        """Assigns update data and PATCH URL to vars for cURL execution
        Runs command and validates output when returning self._validate_response()"""

        def get_custom_apps():
            """Queries all custom apps from Iru tenant; assigns GET URL to var for cURL execution
            Runs command and validates output when returning self._validate_response()"""
            get_url = self.api_custom_apps_url
            response = requests.get(get_url, headers=self.auth_headers, timeout=HTTP_TIMEOUT)
            # Assigns self.custom_apps
            return self._validate_response(response, "get")

        # Raise if our custom apps GET fails
        if not get_custom_apps():
            raise Exception

        lib_item_dict = None
        if self.custom_app_name is not None:
            lib_item_dict = self._find_lib_item_match()

        # Returns None if multiple matches, False if no matches
        if lib_item_dict is None:
            return False
        if lib_item_dict is False:
            if self.default_auto_create is True:
                return self.create_custom_app()
            else:
                log.error("Could not locate existing custom app to update")
                log.error("Auto-create is disabled -- skipping remaining steps")
                return False

        # Assign existing LI name, UUID, enforcement, and sha256
        lib_item_name = lib_item_dict.get("name")
        lib_item_uuid = lib_item_dict.get("id")
        lib_item_enforcement = lib_item_dict.get("install_enforcement")
        lib_item_shasum = lib_item_dict.get("sha256")

        # Get sha256 of local media
        local_media_shasum = sha256_file(self.pkg_path)

        log.debug(f"Local Media SHA: '{local_media_shasum}'")
        log.debug(f"Existing LI SHA: '{lib_item_shasum}'")

        if local_media_shasum == lib_item_shasum:
            log.warning(f"Pending upload '{self.pkg_name}' identical to existing '{lib_item_name}' installer")
            log.info("Skipping upload/update...\n")
            self.skipped_iterations += 1
            return True
        log.info(f"Proceeding to update existing custom app '{lib_item_name}'")

        if self.upload_custom_app() is not True:
            return False

        # Update body with updated package location once uploaded
        update_data = {"file_key": self.s3_key}
        if self.install_type == "zip":
            update_data["unzip_location"] = self.unzip_location
        # Validate enforcement of existing LI
        if lib_item_enforcement == "continuously_enforce":
            # If existing LI enforcement differs from set value, override var to Iru value
            if self.custom_app_enforcement != lib_item_enforcement:
                log.info("Existing app enforcement differs from local config... Deferring to Iru enforcement type")
                # This info is needed for auditing/enforcement, so split the PKG and find if req values unset
                try:
                    self.app_vers
                    log.debug("Skipping PKG expansion as app version already known")
                except (AttributeError, NameError):
                    log.debug("Proceeding with PKG expansion to populate ID/version...")
                    self._expand_pkg_get_info()
                # Call audit customization here since not invoked earlier
                self._customize_audit_for_upload()
                self.custom_app_enforcement = lib_item_enforcement
            with open(self.audit_script_path) as f:
                audit_script = f.read()
                update_data["audit_script"] = audit_script
        patch_url = os.path.join(self.api_custom_apps_url, lib_item_uuid)
        if self.dry_run is True:
            log.info(
                f"DRY RUN: Would update Custom App '{lib_item_name}' with PATCH to '{patch_url}' and fields '{update_data}'"
            )
            return True
        response = requests.patch(
            patch_url, headers=self.auth_headers, params=self.params, json=update_data, timeout=HTTP_TIMEOUT
        )
        return self._validate_response(response, "update")

    def iru_customize_create_update(self):
        """Parent function to process any audit script updates and
        either create a net new or update an existing custom app"""
        if self.custom_app_enforcement == "continuously_enforce":
            self._ensure_audit_script()
            self._customize_audit_for_upload()
        # If flag override is set, create new app regardless of existing
        if self.arg_create_new is True:
            self.create_custom_app()
        else:
            self.update_custom_app()
        self._restore_audit() if self.custom_app_enforcement == "continuously_enforce" else True

    def main(self) -> IrupkgResult:
        """Main function to execute irupkg.

        Returns an IrupkgResult summarising the run. Failures are captured into
        the result rather than raised, so callers can branch on result.action.
        """
        source = self.arg_pkg_path
        result = IrupkgResult(source=source)
        self.skipped_iterations = 0
        try:
            try:
                self.get_install_media_metadata()
            except OSError as exc:
                result.action = "failed"
                result.error = str(exc)
                return result
            # Reads config and assigns needed vars for runtime
            # Also validates and populates values for Iru/Slack (if defined)
            self.populate_from_config()
            self.audit_script_path = os.path.join(self.parent_dir, self.audit_script)

            if self.custom_app_enforcement == "continuously_enforce":
                # This info is needed for auditing/enforcement, so split the PKG and find it
                self._expand_pkg_get_info()

            ###################
            #### MAIN EXEC ####
            ###################
            # Iterate over dict specifying app type and name
            for key, value in self.app_names.items():
                if key == "test_name":
                    self.custom_app_name = value
                    self.test_app, self.prod_app = True, False
                elif key == "prod_name":
                    self.custom_app_name = value
                    self.test_app, self.prod_app = False, True
                else:
                    self.test_app, self.prod_app = False, False
                # Main func for processing Cr/Up ops
                self.iru_customize_create_update()
            result.pkg_name = getattr(self, "custom_app_name", None) or getattr(self, "pkg_name", None)
            if getattr(self, "dry_run", False):
                result.action = "dry_run"
            elif self.skipped_iterations > 0 and self.skipped_iterations == len(self.app_names):
                # All app-variant iterations short-circuited on identical-shasum
                result.action = "skipped"
            else:
                result.action = "succeeded"
        except Exception as exc:
            result.action = "failed"
            result.error = str(exc) or exc.__class__.__name__
            log.exception("irupkg run failed")
        finally:
            # Clean up copied PKG if it exists
            if hasattr(self, "copied_pkg_path") and self.copied_pkg_path is not None:
                try:
                    os.remove(self.copied_pkg_path)
                except PermissionError:
                    shutil.rmtree(self.copied_pkg_path)
                except OSError:
                    pass
            # Clean up temp dir used for PKG/DMG expansion
            try:
                self._expand_pkg_get_info(cleanup=True)
            except Exception:
                pass
        return result


##############
#### BODY ####
##############


def _get_version() -> str:
    try:
        return _pkg_version("irupkg")
    except Exception:
        return "unknown"


def _resolve_parent_dir() -> Path:
    """Resolve the irupkg config/data directory from environment variables or platform defaults.

    Resolution order: IRUPKG_LOCAL_DIR --> KPKG_LOCAL_DIR --> IRUPKG_CONFIG_DIR --> KPKG_CONFIG_DIR -->
    platform default. Emits one deprecation warning per process when a KPKG_* env var or legacy
    data directory is used.
    """
    global _warned_legacy

    if os.environ.get("IRUPKG_LOCAL_DIR"):
        return Path(os.environ["IRUPKG_LOCAL_DIR"])

    if os.environ.get("KPKG_LOCAL_DIR"):
        if not _warned_legacy:
            _warned_legacy = True
            print("WARNING: KPKG_LOCAL_DIR is deprecated, use IRUPKG_LOCAL_DIR instead", file=sys.stderr)
        return Path(os.environ["KPKG_LOCAL_DIR"])

    if os.environ.get("IRUPKG_CONFIG_DIR"):
        return Path(os.environ["IRUPKG_CONFIG_DIR"]).expanduser().resolve()

    if os.environ.get("KPKG_CONFIG_DIR"):
        if not _warned_legacy:
            _warned_legacy = True
            print("WARNING: KPKG_CONFIG_DIR is deprecated, use IRUPKG_CONFIG_DIR instead", file=sys.stderr)
        return Path(os.environ["KPKG_CONFIG_DIR"]).expanduser().resolve()

    if platform.system() == "Darwin":
        new_path = Path.home() / "Library" / "IruPackages"
        old_path = Path.home() / "Library" / "KandjiPackages"
        if new_path.exists():
            return new_path
        if old_path.exists():
            try:
                old_path.rename(new_path)
                print(f"Migrated data directory from {old_path} to {new_path}.", file=sys.stderr)
            except OSError:
                if not _warned_legacy:
                    _warned_legacy = True
                    print(
                        f"WARNING: data directory has moved from {old_path} to {new_path}. "
                        "Move your files to the new location to silence this warning.",
                        file=sys.stderr,
                    )
                return old_path
        return new_path

    # Legacy ~/Library/KandjiPackages only exists on macOS
    new_path = Path(user_data_dir("irupkg", "Iru"))
    old_path = Path(user_data_dir("kpkg", "Kandji"))
    if new_path.exists() or not old_path.exists():
        return new_path
    if not _warned_legacy:
        _warned_legacy = True
        print(
            f"WARNING: data directory has moved from {old_path} to {new_path}. "
            "Move your files to the new location to silence this warning.",
            file=sys.stderr,
        )
    return old_path


def _process_one(
    path: str,
    opts: PackageOptions,
    parent_dir: Path | None,
    source_label: str | None = None,
) -> IrupkgResult:
    """Construct an irupkg instance for a single local artifact and run it.

    `source_label` overrides the result's `source` field (used by process_brew
    to report the original cask name rather than the downloaded file path).
    """
    resolved_parent = parent_dir if parent_dir is not None else _resolve_parent_dir()
    try:
        irupkg = Irupkg(path_to_pkg=path, opts=opts, parent_dir=resolved_parent)
    except IrupkgError as exc:
        return IrupkgResult(source=source_label or path, action="failed", error=str(exc))
    result = irupkg.main()
    if source_label is not None:
        result.source = source_label
    return result


def _configure_logging(debug: bool = False) -> None:
    """Attach the irupkg log file + stream handlers to the root logger if it has
    none yet. Shared by the CLI and the library entry points so REPL callers
    see the same INFO output the CLI does without configuring logging
    themselves. No-op when handlers are already attached."""
    if logging.getLogger().handlers:
        return
    hostname = platform.node()
    path_to_log = str(_resolve_parent_dir() / "irupkg.log")
    os.makedirs(os.path.dirname(path_to_log), exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="{asctime} " + f"[{hostname}]" + ": {levelname}: {message}",
        handlers=[logging.FileHandler(path_to_log), logging.StreamHandler()],
        style="{",
        datefmt="%Y-%m-%d %I:%M:%S %p",
    )


def process_pkg(
    path: str | Path,
    *,
    name: str | None = None,
    testname: str | None = None,
    sscategory: str | None = None,
    zzcategory: str | None = None,
    dry: bool = False,
    create: bool = False,
    unzip_location: str | None = None,
    parent_dir: Path | None = None,
    debug: bool = False,
) -> IrupkgResult:
    """Upload a local PKG/DMG/ZIP to Iru as a Custom App.

    Mirrors the behaviour of `irupkg -p <path>` for a single artifact and returns
    an IrupkgResult instead of logging-only output. Failures are captured in the
    result (action="failed") rather than raised.

    Logging: initializes the root logger at INFO (or DEBUG when `debug=True`)
    if no handlers are attached yet. Callers that have already configured
    logging keep their existing setup -- this is a no-op in that case.
    """
    _configure_logging(debug=debug)
    opts = PackageOptions(
        name=name,
        testname=testname,
        sscategory=sscategory,
        zzcategory=zzcategory,
        dry=dry,
        create=create,
        unzip_location=unzip_location,
    )
    return _process_one(str(path), opts, parent_dir)


def process_brew(
    cask: str,
    *,
    name: str | None = None,
    testname: str | None = None,
    sscategory: str | None = None,
    zzcategory: str | None = None,
    dry: bool = False,
    create: bool = False,
    unzip_location: str | None = None,
    parent_dir: Path | None = None,
    debug: bool = False,
) -> IrupkgResult:
    """Fetch a Homebrew cask and upload the resulting artifact to Iru.

    Mirrors the behaviour of `irupkg -b <cask>` for a single cask and returns an
    IrupkgResult. If the cask cannot be fetched the result is marked failed.

    Logging: initializes the root logger at INFO (or DEBUG when `debug=True`)
    if no handlers are attached yet. Callers that have already configured
    logging keep their existing setup -- this is a no-op in that case.
    """
    _configure_logging(debug=debug)
    downloaded = source_from_brew(cask)
    if downloaded is None:
        return IrupkgResult(source=cask, action="failed", error=f"Failed to fetch Homebrew cask '{cask}'")
    opts = PackageOptions(
        name=name,
        testname=testname,
        sscategory=sscategory,
        zzcategory=zzcategory,
        dry=dry,
        create=create,
        unzip_location=unzip_location,
    )
    return _process_one(downloaded.strip(), opts, parent_dir, source_label=cask)


def run_setup(target_dir: Path) -> None:
    """Interactive config generator for the non-macOS fallback path.
    macOS callers hand off to setup.zsh (which owns Keychain storage); this
    function is only reached when setup.zsh isn't present, so ENV storage
    is the only supported keystore here."""
    print("\nirupkg interactive setup\n")
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / "config.json"

    api_url = input("Iru API URL (e.g., tenant.api.iru.com): ").strip()
    token_name = input("Iru API token name [IRUPKG_TOKEN]: ").strip() or "IRUPKG_TOKEN"

    # Load existing config if present so we don't clobber unrelated fields
    existing = {}
    if config_path.exists():
        with open(config_path) as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                pass

    config = {**build_default_config(api_url, token_name, use_env=True, use_keychain=False), **existing}
    config["iru"] = {"api_url": api_url, "token_name": token_name}
    config["token_keystore"] = {"environment": True, "keychain": False}

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2, sort_keys=True)

    print(f"\nConfig written to: {config_path}")
    env_path = target_dir / ".env"
    print("\nSet your token environment variable(s) before running irupkg:")
    print(f"  echo 'export {token_name}=\"\"' >> {env_path}")
    print(f"  echo 'export SLACK_TOKEN=\"\"' >> {env_path}   # optional")
    print(f"  source {env_path}")


def _run(argv: list[str] | None = None) -> None:
    """Core entry-point logic. Raises IrupkgError for expected error conditions.
    When argv is None, sys.argv is used (normal CLI behavior). Pass a list of
    strings to drive irupkg programmatically, e.g. _run(['-p', 'app.pkg', '-y'])."""
    _SETUP_FLAGS = {"-S", "--setup"}
    _IRUPKG_OWN_FLAGS = {"-d", "--debug"}
    raw = list(argv) if argv is not None else sys.argv[1:]
    setup_mode = bool(_SETUP_FLAGS & set(raw))

    if setup_mode:
        # Pre-scan raw args: extract irupkg-only flags, forward everything else to setup.zsh.
        # This avoids argparse consuming flags shared between irupkg and setup.zsh (e.g. -h, -b, -c).
        debug = bool(_IRUPKG_OWN_FLAGS & set(raw))
        setup_args = [a for a in raw if a not in _SETUP_FLAGS | _IRUPKG_OWN_FLAGS]
        args = None
    else:
        args, unknown = _build_parser().parse_known_args(argv)
        if unknown:
            raise IrupkgError(f"Unrecognized arguments: {' '.join(unknown)}")
        debug = args.debug
        setup_args = []

    if os.geteuid() == 0 and not env_keystore_enabled():
        raise IrupkgError("irupkg should NOT be run as superuser!")

    _configure_logging(debug=debug)

    parent_dir = _resolve_parent_dir()

    if setup_mode:
        if platform.system() == "Darwin":
            macos_setup = parent_dir / "setup.zsh"
            try:
                packaged = importlib.resources.files("irupkg.scripts").joinpath("setup.zsh")
                with importlib.resources.as_file(packaged) as src:
                    parent_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy(src, macos_setup)
            except (FileNotFoundError, ModuleNotFoundError):
                pass
            if macos_setup.exists():
                # setup.zsh reads/writes config.json via plutil; seed a default skeleton on first
                # run so the interactive prompts have a file to populate instead of stderr-spamming.
                config_path = parent_dir / "config.json"
                if not config_path.exists():
                    with open(config_path, "w") as f:
                        json.dump(
                            build_default_config(
                                "TENANT.api.iru.com",
                                "IRUPKG_TOKEN",
                                use_env=False,
                                use_keychain=False,
                            ),
                            f,
                            indent=2,
                            sort_keys=True,
                        )
                else:
                    try:
                        with open(config_path) as f:
                            config_data = json.load(f)
                        if "kandji" in config_data and "iru" not in config_data:
                            config_data["iru"] = config_data.pop("kandji")
                            with open(config_path, "w") as f:
                                json.dump(config_data, f, indent=2, sort_keys=True)
                            print("Migrated config.json key 'kandji' to 'iru'.", file=sys.stderr)
                    except (OSError, ValueError):
                        pass
                subprocess.run(["/bin/zsh", str(macos_setup), *setup_args], check=False)
                return
        run_setup(parent_dir)
        return

    vers = _get_version()

    if args.version:
        print(f"{CLI_BRANDING}: {vers}")
        return

    if args.pkg is None and args.brew is None:
        raise IrupkgError("No PKG/DMG path or Homebrew cask provided (use flag -p/--pkg or -b/--brew)")

    # Pair each resolved local path with the label used for logs/results
    # (the cask name for brew inputs, the path itself for direct -p inputs).
    items: list[tuple[str, str | None]] = []
    if args.pkg is not None:
        items.extend((p, None) for p in args.pkg)
    if args.brew is not None:
        for brew in args.brew:
            downloaded_brew = source_from_brew(brew)
            if downloaded_brew is not None:
                items.append((downloaded_brew, brew))

    if len(items) > 1 and any((args.name, args.testname, args.sscategory, args.zzcategory)):
        raise IrupkgError(
            "Multiple brew casks/installers provided, but flags passed for name/category are ambiguous -- "
            "use package map or defaults to populate metadata when specifying multiple items"
        )

    log.info(format_stdout(f"{CLI_BRANDING} ({vers})"))

    opts = PackageOptions(
        name=args.name,
        testname=args.testname,
        sscategory=args.sscategory,
        zzcategory=args.zzcategory,
        dry=args.dry,
        create=args.create,
        unzip_location=args.unzip_location,
    )

    any_failed = False
    for path, label in items:
        log.info(f"\nProcessing '{os.path.basename(path)}'")
        result = _process_one(path.strip(), opts, parent_dir, source_label=label)
        if result.action == "failed":
            any_failed = True
            log.error(f"Failed to process '{label or os.path.basename(path)}': {result.error}")
    log.info(format_stdout(f"{CLI_BRANDING} Runtime Complete"))
    if any_failed:
        sys.exit(1)


def main() -> None:
    """CLI entry point. Not intended for programmatic use -- construct irupkg directly instead."""
    try:
        _run()
    except IrupkgError as e:
        log.fatal(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
