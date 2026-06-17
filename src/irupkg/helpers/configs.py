# Created 01/16/24; NRJA
# Updated 02/20/24; NRJA
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

#######################
####### IMPORTS #######
#######################

import json
import logging
import os
import platform
import plistlib
import re
import sys
from urllib.parse import urlparse

import requests

from .utils import HTTP_TIMEOUT, env_keystore_enabled

###########################
######### LOGGING #########
###########################

log = logging.getLogger(__name__)

_warned_deprecated: set[str] = set()


def _deprecated_env(old_name: str, new_name: str | None = None) -> str:
    """Return the value of a deprecated env var, emitting a one-time warning to stderr."""
    val = os.environ.get(old_name, "")
    if val and old_name not in _warned_deprecated:
        _warned_deprecated.add(old_name)
        resolved = new_name if new_name is not None else old_name.replace("KANDJI_", "IRU_", 1)
        print(f"WARNING: {old_name} is deprecated, use {resolved} instead", file=sys.stderr)
    return val


def _deprecated_config_key(old_key: str, new_key: str) -> None:
    """Emit a one-time warning that a config.json top-level key has been renamed."""
    sentinel = f"config:{old_key}"
    if sentinel not in _warned_deprecated:
        _warned_deprecated.add(sentinel)
        print(
            f"WARNING: config.json key '{old_key}' is deprecated, rename to '{new_key}'. Run 'irupkg --setup' to migrate automatically.",
            file=sys.stderr,
        )


def build_default_config(api_url: str, token_name: str, use_env: bool = True, use_keychain: bool = False) -> dict:
    """Return a minimal valid irupkg config dict with sensible defaults.
    Single source of truth shared by _read_config() and run_setup()."""
    return {
        "iru": {"api_url": api_url, "token_name": token_name},
        "token_keystore": {"environment": use_env, "keychain": use_keychain},
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


class Configurator:
    """Reads and sets variables based on configured settings"""

    #####################################
    ######### PRIVATE FUNCTIONS #########
    #####################################

    def _parse_enforcement(self, enforcement):
        """Translates provided enforcement val between config values and API-valid values"""
        match enforcement.lower():
            case "audit_enforce":
                parsed_enforcer = "continuously_enforce"
            case "self_service":
                parsed_enforcer = "no_enforcement"
            case "continuously_enforce":
                parsed_enforcer = "audit_enforce"
            case "no_enforcement":
                parsed_enforcer = "self_service"
            case "install_once":
                parsed_enforcer = "install_once"
            case _:
                return False
        return parsed_enforcer

    def _read_config(self, iru_conf):
        """Read in configuration from defined conf path
        Building out full path to read and load as JSON data
        Return loaded JSON data once existence and validity are confirmed"""
        # Have to derive path this way in order to get the execution file origin
        config_path = os.path.join(self.parent_dir, iru_conf)
        if not os.path.exists(config_path):
            api_url = os.environ.get("IRU_API_URL") or _deprecated_env("KANDJI_API_URL")
            if not api_url:
                log.fatal(
                    f"irupkg config not found at '{config_path}'! "
                    "Run 'irupkg --setup' to generate it, or set IRU_API_URL and IRUPKG_TOKEN in the environment and try again"
                )
                sys.exit(1)
            token_name = (
                os.environ.get("IRUPKG_TOKEN_NAME")
                or _deprecated_env("KANDJI_TOKEN_NAME", "IRUPKG_TOKEN_NAME")
                or (
                    "KANDJI_TOKEN"
                    if (not os.environ.get("IRUPKG_TOKEN") and _deprecated_env("KANDJI_TOKEN", "IRUPKG_TOKEN"))
                    else "IRUPKG_TOKEN"
                )
            )
            auto_config = build_default_config(api_url, token_name)
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w") as f:
                json.dump(auto_config, f, indent=2, sort_keys=True)
            log.info(f"Auto-generated config at '{config_path}' from environment variables")
            return auto_config
        try:
            with open(config_path) as f:
                custom_config = json.loads(f.read())
        except ValueError as ve:
            log.fatal(
                f"Config at '{config_path}' is not valid JSON!\n{ve} -- validate file integrity for '{iru_conf}' and try again"
            )
            sys.exit(1)
        return custom_config

    def _populate_package_map(self):
        """Checks if recipe map is enabled and iters
        to match recipe with custom app name(s)/env(s)"""

        ############################
        # Populate Vars from Mapping
        ############################
        # Initialize vars
        self.package_map = None
        self.app_names = {}
        self.map_unzip_location = None
        if self.iru_config.get("use_package_map") is True:
            self.package_map = self._read_config(self.package_map_file)
            if self.package_map is False:
                log.error("Package map is enabled, but config is invalid!")
                raise Exception
            self._expand_pkg_get_info(id_query=True)

            for ident, apps in self.package_map.items():
                # Once matching PKG ID found, assign and exit loop
                if ident == self.map_id:
                    log.info(f"Located matching map value '{self.map_id}' from PKG/DMG")
                    self.app_names = apps
                    break
            if not self.app_names:
                log.warning(f"Package map enabled, but no match found for ID '{self.map_id}'!")
                log.info("Will use defaults if no args passed")
        self.map_ss_category = self.app_names.get("ss_category")
        self.map_test_category = self.app_names.get("test_category")
        self.map_unzip_location = self.app_names.pop("unzip_location", None)

        # Once assigned, remove from dict
        # This ensures we're only iterating over app names
        try:
            self.app_names.pop("ss_category")
        except KeyError:
            pass
        try:
            self.app_names.pop("test_category")
        except KeyError:
            pass

    def _set_defaults_enforcements(self):
        """Reads JSON config and sets enforcement based on
        defined value, otherwise defaults to install once"""
        if (default_vals := self.iru_config.get("zz_defaults")) is not None:
            self.default_auto_create = default_vals.get("auto_create_app")
            self.default_custom_name = default_vals.get("new_app_naming")
            self.default_dry_run = default_vals.get("dry_run")
            self.default_dynamic_lookup = default_vals.get("dynamic_lookup")
            self.default_ss_category = default_vals.get("self_service_category")
            self.test_default_ss_category = default_vals.get("test_self_service_category")
            self.default_unzip_location = default_vals.get("unzip_location", "/Applications")

        config_enforcement = self.iru_config.get("li_enforcement")
        enforcement_type = self._parse_enforcement(config_enforcement.get("type"))
        # Check if enforcement type specified, else default to once
        # May be overridden later based on recipe-specific mappings
        self.custom_app_enforcement = (
            "no_enforcement"
            if (self.map_ss_category or self.arg_ss_category or self.map_test_category or self.arg_test_category)
            is not None
            else enforcement_type or "install_once"
        )
        # Assign enforcement delays for audits
        if config_enforcement.get("delays"):
            self.test_delay = config_enforcement.get("delays").get("test")
            self.prod_delay = config_enforcement.get("delays").get("prod")

        self.dry_run = False
        if (self.arg_dry_run or self.default_dry_run) is True:
            log.info("DRY RUN: Will not make any Custom App modifications!\n\n\n")
            self.dry_run = True

        self.unzip_location = (
            self.arg_unzip_location
            or self.map_unzip_location
            or getattr(self, "default_unzip_location", None)
            or "/Applications"
        )

    def _set_custom_name(self):
        """Sets and populates self.app_names dict for later iter"""
        # Set assigned name from user passed flag
        self.assigned_name = self.arg_app_name or None
        # Set derived name from queried PKG/DMG metadata
        self.derived_name = self.install_name or self.pkg_path_name
        # If prod and test names defined, assign to dict (overwriting if necessary)
        if self.arg_prod_name is not None:
            self.app_names["prod_name"] = self.arg_prod_name
        if self.arg_test_name is not None:
            self.app_names["test_name"] = self.arg_test_name
        # If "undefined" is set as key name, this func is being called a second time
        # If a PKG is found within a DMG, we are overwriting self.derived_name
        # Run through logic gates again to see if re-assignment is necessary
        if not self.app_names or "undefined" in self.app_names.keys():
            # If not in config, check if custom name(s) passed as args
            if self.assigned_name is not None:
                self.custom_app_name = self.assigned_name
            elif self.default_custom_name is not None:
                self.custom_app_name = self.default_custom_name.replace("APPNAME", self.derived_name)
            # All else fails, assign as 'derived name (irupkg)'
            else:
                self.custom_app_name = f"{self.derived_name} (irupkg)"
            self.app_names["undefined"] = self.custom_app_name

    def _populate_self_service(self):
        def get_self_service():
            """Queries all Self Service categories from Iru tenant; assigns GET URL to var for cURL execution
            Runs command and validates output when returning self._validate_response()"""
            get_url = f"{self.iru_api_prefix}/self-service/categories"
            response = requests.get(url=get_url, headers=self.auth_headers, timeout=HTTP_TIMEOUT)
            return self._validate_response(response, "get_selfservice")

        def name_to_id(ss_name, ss_type):
            """Iterates over self_service list and assigns category ID to var"""
            # Iter over and find matching id for name
            ss_default = (
                self.default_ss_category
                if ss_type == "prod"
                else self.test_default_ss_category
                if ss_type == "test"
                else None
            )
            try:
                ss_assignment = next(
                    category.get("id") for category in self.self_service if category.get("name") == ss_name
                )
            except StopIteration:
                log.warning(
                    f"Provided category '{ss_name}' not found in Self Service!"
                ) if ss_name is not None else None
                try:
                    # Set category id to default (None check performed later)
                    ss_assignment = (
                        next(category.get("id") for category in self.self_service if category.get("name") == ss_default)
                        if ss_default
                        else None
                    )
                except StopIteration:
                    log.warning(f"Default category '{ss_default}' not found in Self Service!")
                    ss_assignment = None
            # Only reassign/override if not already set
            if ss_type == "prod":
                if ss_name is not None:
                    self.ss_category_id = ss_assignment
                else:
                    self.ss_category_id = self.ss_category_id if self.ss_category_id is not None else ss_assignment
            elif ss_type == "test":
                if ss_name is not None:
                    self.test_category_id = ss_assignment
                else:
                    self.test_category_id = (
                        self.test_category_id if self.test_category_id is not None else ss_assignment
                    )

        # Set category IDs to None
        self.ss_category_id, self.test_category_id = None, None

        ############################################
        # Assigns list of dicts to self.self_service
        get_self_service()

        # Create and iter over ad hoc lists with categories/envs
        # If both arg and mapping values defined, override with passed args
        for cat, env in zip(
            [self.map_ss_category, self.map_test_category, self.arg_ss_category, self.arg_test_category],
            ["prod", "test", "prod", "test"],
        ):
            name_to_id(cat, env)

    def _set_slack_config(self):
        """Checks if Slack token name is in config
        Looks up webhook and assigns for use in self.slack_notify()"""

        slack_token_name = self.slack_opts.get("webhook_name", "SLACK_TOKEN")
        # Auto-enable Slack when the webhook token is present in ENV (mirrors ENV_KEYSTORE behaviour)
        if not self.slack_opts.get("enabled") and self.token_keystores.get("environment"):
            if os.environ.get(slack_token_name) or os.environ.get(slack_token_name.upper()):
                self.slack_opts["enabled"] = True
        self.slack_channel = self._retrieve_token(slack_token_name) if self.slack_opts.get("enabled") else None

    def _set_iru_config(self):
        """Validates provided Iru API URL is valid for use
        Assigns prefix used for API calls + bearer token"""

        # Ensure API URL is prefixed with https://
        self.iru_api_url = self._ensure_https(self.iru_api_url)
        self.headers = {"Content-Type": "application/json"}

        iru_host = (urlparse(self.iru_api_url).hostname or "").lower()
        if iru_host == "iru.com" or iru_host.endswith(".iru.com"):
            # iru.com serves every tenant's console from TENANT.iru.com regardless of region,
            # so drop the ".api" label *and* any ".eu" locale (no migration check needed).
            self.tenant_url = self.iru_api_url.replace(".api.eu.", ".").replace(".api.", ".")
        else:
            # kandji.io stays valid even after a tenant migrates, so the console can live on
            # either domain -- check migration status to point console/library links at the right
            # one. Swapping ".api." --> ".gateway." preserves locale so US and EU hit different
            # hosts (TENANT.gateway.kandji.io vs TENANT.gateway.eu.kandji.io).
            migration_check_url = (
                self.iru_api_url.replace(".api.", ".gateway.") + "/main-backend/app/v1/company/auth-migration-status"
            )
            try:
                response = requests.get(url=migration_check_url, headers=self.headers, timeout=HTTP_TIMEOUT)
                migration_data = response.json()
                migration_status_known = True
            except (requests.RequestException, ValueError):
                migration_data = {}
                migration_status_known = False

            # Match the structured error code rather than a substring of stringified values;
            # otherwise a proxy error page or unrelated field that mentions "tenantNotFound"
            # hard-exits the process.
            if migration_data.get("error") == "tenantNotFound" or migration_data.get("code") == "tenantNotFound":
                log.fatal(f"ERROR: Provided Iru URL {self.iru_api_url} appears invalid! Cannot upload...")
                sys.exit(1)

            if migration_data.get("auth_migration_status") in ("STARTED", "COMPLETED"):
                # Migrated: console moved to the region-agnostic iru.com host (collapses any
                # ".eu" locale). Nudge the user to update their API URL to iru.com.
                self.tenant_url = (
                    self.iru_api_url.replace(".api.", ".")
                    .replace(".eu.kandji.io", ".kandji.io")
                    .replace(".kandji.io", ".iru.com")
                )
                log.warning(
                    f"API URL '{self.iru_api_url}' uses the deprecated kandji.io host. Switch 'iru.api_url' to "
                    "'TENANT.api.iru.com' ('TENANT.api.eu.iru.com' for EU tenants) in config.json, or "
                    "re-run 'irupkg --setup'. kandji.io support will be removed in a future major version."
                )
            else:
                # Unmigrated OR undetermined: console stays on the region-specific kandji.io host,
                # so keep any ".eu" locale and just drop ".api".
                self.tenant_url = self.iru_api_url.replace(".api.", ".")
                if migration_status_known:
                    log.info(
                        f"API URL '{self.iru_api_url}' uses the kandji.io host, which is correct for this "
                        "not-yet-migrated tenant. kandji.io support will be removed in a future major "
                        "version; switch to 'TENANT.api.iru.com' once your tenant migrates to Iru."
                    )
                else:
                    log.info(
                        f"Could not determine migration status for '{self.iru_api_url}' (gateway unreachable); "
                        "defaulting console/library links to the kandji.io host. If this tenant has already "
                        "migrated to Iru, those links may be stale, but should redirect to your Iru tenant."
                    )
        # Assign API domain
        self.iru_api_prefix = os.path.join(self.iru_api_url, "api", "v1")
        # Define API endpoints
        self.api_custom_apps_url = os.path.join(self.iru_api_prefix, "library", "custom-apps")
        self.api_upload_pkg_url = os.path.join(self.api_custom_apps_url, "upload")
        self.api_self_service_url = os.path.join(self.iru_api_prefix, "self-service", "categories")

        # Grab auth token for Iru API interactions
        irupkg_token = self._retrieve_token(self.irupkg_token_name)
        if irupkg_token is None:
            log.fatal(
                f"ERROR: Could not retrieve token value from key {self.irupkg_token_name}! Run 'irupkg --setup' and try again"
            )
            sys.exit(1)
        # Set headers/params for API calls. Cache-Control hints discourage any
        # intermediate CDN/proxy from returning a stale custom-app record after
        # we PATCH it -- observed read-after-write inconsistency from Iru.
        self.auth_headers = {
            "Authorization": f"Bearer {irupkg_token}",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        self.params = {"source": "irupkg"}

    ####################################
    ######### PUBLIC FUNCTIONS #########
    ####################################

    def get_install_media_metadata(self, lookup_again=False):
        """Populates PKG path and name, and validates file type
        to ensure either DMG or PKG is provided
        If DMG, runs diskutil image info to get volume name (macOS only)
        If PKG, runs installer pkginfo to get PKG name (macOS) or libarchive probe (Linux)
        Supports optional arg to re-trigger lookup of PKG if found in DMG
        If found, overrides app name to use PKG value vs. DMG"""
        self.pkg_path = self.pkg_path or self.arg_pkg_path
        self.pkg_name = os.path.basename(self.pkg_path)

        def _is_dmg_file(path):
            """Returns True if path is a DMG."""
            if platform.system() != "Darwin":
                return path.lower().endswith(".dmg")
            return self._run_command(f"hdiutil imageinfo -format '{path}'", nostderr=True) is not False

        def _is_pkg_file(path):
            """Returns True if path is a valid .pkg (XAR archive). Linux-safe."""
            if platform.system() == "Darwin":
                return self._run_command(f"installer -pkginfo -pkg '{path}'", nostderr=True) is not False
            try:
                with open(path, "rb") as f:
                    return f.read(4) == b"xar!"
            except OSError:
                return False

        def _is_zip_file(path):
            """Returns True if path is a ZIP archive (magic bytes PK\\x03\\x04)."""
            try:
                with open(path, "rb") as f:
                    return f.read(4) == b"PK\x03\x04"
            except OSError:
                return False

        if _is_dmg_file(self.pkg_path):
            self.install_type = "image"
        elif _is_pkg_file(self.pkg_path):
            self.install_type = "package"
        elif _is_zip_file(self.pkg_path):
            self.install_type = "zip"
        else:
            unsupported_type = self._run_command(f"file --mime-type -b '{self.pkg_path}'")
            log.error(f"File '{self.pkg_name}' is unsupported type '{unsupported_type}'")
            log.error(f"Confirm '{self.pkg_path}' is valid package/disk image")
            log.error(f"Skipping '{self.pkg_name}'...")
            raise OSError
        if self.install_type == "image":
            if platform.system() == "Darwin":
                shell_cmd = f"diskutil image info -plist '{self.pkg_path}'"
                diskutil_out = self._run_command(shell_cmd, nostderr=True)
                if diskutil_out is False:
                    log.warning("Could not retrieve diskutil info for provided DMG")
                    log.warning("Pending EULA may be blocking mount or invalid DMG")
                    self.install_name = None
                else:
                    diskutil_plist_out = plistlib.loads(diskutil_out.encode())
                    self.install_name = next(
                        disk.get("volume-name")
                        for disk in diskutil_plist_out.get("Partitions")
                        if "N/A" not in disk.get("volume-name")
                    )
            else:
                self.install_name = None
        elif self.install_type == "package":
            if platform.system() == "Darwin":
                shell_cmd = f"installer -pkginfo -pkg '{self.pkg_path}'"
                pkginfo_out = self._run_command(shell_cmd)
                try:
                    pkginfo_out = pkginfo_out.splitlines()[0]
                except (IndexError, AttributeError):
                    pass
                self.install_name = pkginfo_out if pkginfo_out is not False else None
            else:
                self.install_name = None
        elif self.install_type == "zip":
            self.install_name = None
        # non-capture group matches on optional 64 char hex string
        # capture matches one or more word/whitespace/dash chars (non-greedy) so hyphenated
        # app names like 'claude-devtools' survive intact
        # lookahead stops at: an optional separator followed by a dotted version (e.g. -1.2, _7.0, 0.95),
        # or at the trailing file extension (e.g. .dmg, .pkg, .zip)
        name_only_pattern = re.compile(r"(?:[a-f0-9]{64}--)?([\w\s-]+?)(?=[\s_-]?\d+\.\d+|\.[a-zA-Z]+$)")
        # Trailing architecture/platform suffixes that appear in upstream filenames but
        # aren't part of the user-facing app name (case-insensitive)
        arch_suffix_pattern = re.compile(r"[-_](aarch64|arm64|arm|x86_64|x86|amd64|intel|universal)$", re.IGNORECASE)
        if self.install_name:
            log.debug(f"regex searching {name_only_pattern} against {self.install_name}\nOutput is below:")
            # If PKG/DMG name found, strip out version and other metadata
            log.debug(re.search(name_only_pattern, self.install_name))
            try:
                self.install_name = re.search(name_only_pattern, self.install_name).group(1).rstrip("_")
                self.install_name = arch_suffix_pattern.sub("", self.install_name)
            except AttributeError as err:
                log.debug(f"Installer name {self.install_name} couldn't be filtered further; leaving unchanged\n{err}")
        # If no name returned from above, run PKG basename thru re filter to approximate a usable name
        if self.install_name:
            self.pkg_path_name = None
        else:
            self.pkg_path_name = re.search(name_only_pattern, os.path.basename(self.pkg_path)).group(1).rstrip("_")
            self.pkg_path_name = arch_suffix_pattern.sub("", self.pkg_path_name)
        if lookup_again is True:
            self._populate_package_map()
            self._set_defaults_enforcements()
            self._set_custom_name()

    def populate_from_config(self):
        """Read in configuration from defined conf path
        Building out full path to read and load as JSON data
        Return loaded JSON data once existence and validity are confirmed"""

        self.config_file = "config.json"
        self.package_map_file = "package_map.json"
        self.audit_script = "audit_app_and_version.zsh"
        # If env-specific custom app name(s) are defined, these'll be overwritten below
        self.test_app, self.prod_app = False, False
        # Temp dir/path for PKG/DMG expansion to be overwritten later
        self.temp_dir, self.tmp_pkg_path, self.tmp_dmg_mount = None, None, None
        # Populate config
        self.iru_config = self._read_config(self.config_file)
        if self.iru_config is False:
            raise Exception("ERROR: Config is invalid! Confirm file integrity and try again")
        try:
            if "iru" in self.iru_config:
                iru_conf = self.iru_config["iru"]
            elif "kandji" in self.iru_config:
                _deprecated_config_key("kandji", "iru")
                iru_conf = self.iru_config["kandji"]
            else:
                raise KeyError("config.json missing 'iru' (or legacy 'kandji') section")
            self.iru_api_url = iru_conf["api_url"]
            self.irupkg_token_name = iru_conf["token_name"]
            self.token_keystores = self.iru_config["token_keystore"]
            # Overwrite Iru API URL from ENV or keep as set in config
            self.iru_api_url = os.environ.get("IRU_API_URL") or _deprecated_env("KANDJI_API_URL") or self.iru_api_url
            # Overwrite keystore conf from ENV if set
            if env_keystore_enabled():
                self.token_keystores["environment"] = True
            # Sanity check values before continuing
            if "TENANT" in self.iru_api_url:
                log.fatal("Iru API URL is invalid! Run 'irupkg --setup' and try again")
                sys.exit(1)
            if not any(self.token_keystores.values()) and platform.system() == "Darwin":
                log.fatal("Token keystore is undefined! Run 'irupkg --setup' and try again")
                sys.exit(1)
            self.slack_opts = self.iru_config["slack"]
        except KeyError as err:
            log.fatal(f"Required key(s) are undefined! {' '.join(err.args)}")
            sys.exit(1)

        self._populate_package_map()
        self._set_defaults_enforcements()
        self._set_custom_name()
        self._set_slack_config()
        self._set_iru_config()
        self._populate_self_service()
