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

import difflib
import hashlib
import importlib.resources
import json
import logging
import os
import platform
import plistlib
import re
import shlex
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from fileinput import FileInput
from functools import reduce
from pathlib import Path, PosixPath
from subprocess import PIPE, STDOUT, run
from urllib.parse import urlsplit, urlunsplit

import requests
from packaging import version as packaging_version

###########################
######### LOGGING #########
###########################

log = logging.getLogger(__name__)

_warned_legacy_keychain: set[str] = set()

# Bound every outbound HTTP call so a hung upstream (Iru/Slack/brew.sh) can't
# stall the scheduler loop indefinitely. requests defaults to no timeout.
HTTP_TIMEOUT = 30


def env_keystore_enabled() -> bool:
    """True when ENV_KEYSTORE is set to an explicit truthy value.

    Presence-only checks let unrelated processes that happen to export the var
    (or set it empty) bypass the root-execution guard and force-flip the
    keystore config. Require an explicit opt-in instead.
    """
    return os.environ.get("ENV_KEYSTORE", "").strip().lower() in ("1", "true", "yes", "on")


def source_from_brew(brew_name):
    """Fetches the download for a Homebrew package and returns local path"""
    downloader = Utilities()
    log.info(f"brew fetching '{brew_name}'...")
    # If not macOS, specify newest macOS version/arch for correct fetch
    os_arch = ""
    if platform.system() != "Darwin":
        brew_api = "https://formulae.brew.sh/api/formula/curl.json"
        newest_macos = next(
            k
            for k in requests.get(brew_api, timeout=HTTP_TIMEOUT).json()["bottle"]["stable"]["files"]
            if "linux" not in k
        )
        os_arch = f"--os {newest_macos.split('_', 1)[-1]} --arch arm"
    try:
        brew_out = downloader._run_command(f"brew fetch {os_arch} --cask {brew_name}")
    except FileNotFoundError:
        log.fatal(f"Failed to run 'brew fetch {brew_name}'")
        log.fatal("Confirm Homebrew is installed/available in PATH and try again")
        sys.exit(1)
    if brew_out is False:
        log.error(f"Failed to fetch {brew_name} -- skipping...")
        log.error(f"Run 'brew search --cask {brew_name}' to validate cask name")
        return None
    download_path = downloader._run_command(f"brew --cache {os_arch} --cask {brew_name}")
    if not download_path or not Path(download_path).exists():
        log.error(f"Could not determine download path for '{brew_name}'")
        return None
    log.info(f"Downloaded '{brew_name}' to '{download_path}'")
    return download_path


def sha256_file(file_path):
    """Returns a SHA256 hash for a provided file"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


class Utilities:
    """Class to hold utility functions for Iru Custom App creation and management"""

    #####################################
    ######### PRIVATE FUNCTIONS #########
    #####################################

    def _run_command(self, shell_exec, nostderr=False):
        """Runs a shell command and returns the response"""
        log.debug(f"Running shell command: '{shell_exec}'")
        raw_out = run(shlex.split(shell_exec), stdout=PIPE, stderr=STDOUT, shell=False, check=False)
        exit_code = raw_out.returncode
        decoded_out = raw_out.stdout.decode().strip()
        if exit_code > 0:
            if nostderr is False:
                log.error(f"'{shell_exec}' failed with exit code {exit_code} and output '{decoded_out}'")
            return False
        return decoded_out

    def _ensure_https(self, url):
        """Parses provided URL, formats, and returns to ensure proper scheme for cURL"""
        parsed_url = urlsplit(url)
        if not parsed_url.scheme or parsed_url.scheme == "http":
            netloc = parsed_url.netloc or parsed_url.path
            path = parsed_url.path if parsed_url.netloc else ""
            new_url = parsed_url._replace(scheme="https", netloc=netloc, path=path)
            return urlunsplit(new_url)
        return url

    ######################
    # Request Funcs
    ######################

    def _validate_response(self, response, action):
        """Check HTTP response from cURL command; if healthy, take action
        according to the provided method where "get" assigns list of custom apps to var;
        "get_selfservice" populates categories from Self Service; "presign" assigns S3 response for upload URL
        "upload" reports upload success; "create"/"update" reports success, posting Custom App details to Slack
        HTTP response of 503 means an upload is still processing and will retry after 5 seconds
        Anything else is treated as an error and notifies to Slack with HTTP code and response
        Identified HTTP code 401 adds language to validate permissions for the passed token"""
        http_code = response.status_code
        if http_code <= 204:
            # Identify specified action and invoke func
            match action.lower():
                case "get":
                    self.custom_apps = response.json().get("results")
                case "get_selfservice":
                    self.self_service = response.json()
                case "presign":
                    self.s3_generated_req = response.json()
                case "upload":
                    self.pkg_uploaded = True
                    log.info(f"Successfully uploaded '{self.pkg_name}'!")
                    # Initial sleep allowing S3 to process upload
                    time.sleep(5)
                case "create" | "update":
                    custom_app_id = response.json().get("id")
                    custom_name = response.json().get("name")
                    custom_app_enforcement = response.json().get("install_enforcement")
                    config_named_enforcement = self._parse_enforcement(custom_app_enforcement)
                    custom_app_url = os.path.join(self.tenant_url, "library", "custom-apps", custom_app_id)
                    log.info(f"SUCCESS: Custom App {action.capitalize()}")
                    log.info(f"Custom App '{custom_name}' available at '{custom_app_url}'")
                    app_vers = getattr(self, "app_vers", None)
                    version_line = f"*Version*: `{app_vers}`\n" if app_vers else ""
                    self.slack_notify(
                        "SUCCESS",
                        f"Custom App {action.capitalize()}d",
                        f"*Name*: `{custom_name}`\n*ID*: `{custom_app_id}`\n"
                        f"*Media*: `{self.pkg_name}`\n{version_line}"
                        f"*Enforcement*: `{config_named_enforcement}`",
                        title_link=custom_app_url,
                    )
                case _:
                    log.info(
                        f"Assignment for 'action' must be one of [get|get_selfservice|presign|upload|create|update]; got '{action}'"
                    )
                    return False
            return True
        elif http_code == 503 and action.lower() in ("update", "create"):
            log.warning(f"(HTTP {http_code}): {response.json().get('detail')}")
            log.info("Retrying in five seconds...")
            time.sleep(5)
            return (
                self.create_custom_app()
                if action.lower() == "create"
                else self.update_custom_app()
                if action.lower() == "update"
                else None
            )
        else:
            error_body = f"`{self.custom_app_name}`/`{self.pkg_name}` failed to {action}: `{response}`"
            if http_code == 401:
                error_body += "\nValidate token is set and try again"
            elif http_code == 403:
                error_body += "\nValidate token permissions and try again"
            log.fatal(f"Failed to {action.capitalize()} Custom App (HTTP {http_code})\n{error_body}")
            self.slack_notify(
                "ERROR",
                f"Failed to {action.capitalize()} Custom App (HTTP {http_code})",
                f"{error_body}",
            )
            sys.exit(1)

    ######################
    # Audit Script Funcs
    ######################

    def _customize_audit_for_upload(self):
        """Finally a worthy Python replacement for sed
        Gets current TS and iters over audit script line by line
        Searches for our keys and updates them with assigned vals
        Creates a backup file before modification"""
        epoch_now = datetime.now().strftime("%s")
        with FileInput(files=self.audit_script_path, inplace=True, backup=".bak", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()  # noqa: PLW2901
                if "APP_NAME=" in line and hasattr(self, "app_name"):
                    line = f'APP_NAME="{self.app_name}"'  # noqa: PLW2901
                elif "BUNDLE_ID=" in line and hasattr(self, "bundle_id"):
                    line = f'BUNDLE_ID="{self.bundle_id}"'  # noqa: PLW2901
                elif "PKG_ID=" in line and hasattr(self, "pkg_id"):
                    line = f'PKG_ID="{self.pkg_id}"'  # noqa: PLW2901
                elif "MINIMUM_ENFORCED_VERSION=" in line and hasattr(self, "app_vers"):
                    line = f'MINIMUM_ENFORCED_VERSION="{self.app_vers}"'  # noqa: PLW2901
                elif "CREATION_TIMESTAMP=" in line:
                    line = f'CREATION_TIMESTAMP="{epoch_now}"'  # noqa: PLW2901
                elif "DAYS_UNTIL_ENFORCEMENT=" in line:
                    line = (  # noqa: PLW2901
                        f"DAYS_UNTIL_ENFORCEMENT={self.test_delay}"
                        if self.test_app is True
                        else f"DAYS_UNTIL_ENFORCEMENT={self.prod_delay}"
                        if (self.prod_app is True or self.prod_delay)
                        else line
                    )
                # Print here writes to file vs. stdout
                print(line)

    def _restore_audit(self):
        """Overwrite customized audit script with clean backup"""
        shutil.move(self.audit_script_path + ".bak", self.audit_script_path)

    def _ensure_audit_script(self):
        """Resolve the audit script, falling back through CWD and the packaged copy.

        The script is mutated in place (with a .bak backup) during uploads, so the
        bundled resource is copied into `audit_script_path` rather than executed
        from `site-packages` directly.
        """
        if os.path.exists(self.audit_script_path):
            return
        for candidate in [Path.cwd(), Path.cwd().parent]:
            path = candidate / self.audit_script
            if path.exists():
                self.audit_script_path = str(path)
                return
        try:
            packaged = importlib.resources.files("irupkg.scripts").joinpath(self.audit_script)
            with importlib.resources.as_file(packaged) as src:
                os.makedirs(os.path.dirname(self.audit_script_path), exist_ok=True)
                shutil.copy(src, self.audit_script_path)
            return
        except (FileNotFoundError, ModuleNotFoundError):
            pass
        raise FileNotFoundError(
            f"Audit script not found at {self.audit_script_path}. "
            "Set IRUPKG_LOCAL_DIR to the repo root, set IRUPKG_CONFIG_DIR to a directory "
            "containing audit_app_and_version.zsh, or copy the script there manually."
        )

    ######################
    # Token Lookup Funcs
    ######################

    def _env_token_get(self, item_name):
        """Searches ENV for str `item_name`"""
        token = os.environ.get(item_name, None)
        # Also search for val from uppercase ENV name
        upper_token = os.environ.get(item_name.upper(), None)
        if token is None:
            token = upper_token if upper_token is not None else None
        return token

    def _keychain_token_get(self, item_name):
        """Retrieves and returns a secret stored at `item_name` in the keychain"""
        decoded_out = self._run_command(
            f"/usr/bin/security find-generic-password -w -s {item_name} -a 'irupkg'"
        )
        if decoded_out is not False:
            return decoded_out
        # Fall back to legacy 'kpkg' account for users who haven't run migrate_from_legacy yet
        decoded_out = self._run_command(
            f"/usr/bin/security find-generic-password -w -s {item_name} -a 'kpkg'"
        )
        if decoded_out is not False:
            if item_name not in _warned_legacy_keychain:
                _warned_legacy_keychain.add(item_name)
                print(
                    f"warning: keychain token '{item_name}' found under legacy 'kpkg' account; "
                    "run 'irupkg --setup' to migrate it to the 'irupkg' account",
                    file=sys.stderr,
                )
            return decoded_out
        return None

    def _retrieve_token(self, item_name):
        """Return token by name, searching configured keystores then falling back to ENV on non-Darwin platforms."""
        token_val = None
        if self.token_keystores.get("environment"):
            token_val = self._env_token_get(item_name)
        if not token_val and self.token_keystores.get("keychain"):
            token_val = self._keychain_token_get(item_name)
        # On macOS, config.json is authoritative; ENV_KEYSTORE=1 is the opt-in (force-flips it in configs.py).
        if not token_val and platform.system() != "Darwin":
            token_val = self._env_token_get(item_name)
        return token_val

    ######################
    # Source info from PKG
    ######################
    def _expand_pkg_get_info(self, id_query=False, cleanup=False):
        """Explodes a provided PKG at self.pkg_path into a temp dir Locates Info.plist for app within
        If multiple, selects Info.plist for largest app bundle within PKG
        Reads in BID, version, and .app name and assigns to self. If unable to locate, raises RuntimeError
        and proceeds with PackageInfo lookup to enforce install/version from PKG metadata; ends run with temp dir delete
        """

        def _get_dir_size(path="."):
            """Subfunc to iterate over a dir and return sum total bytesize
            Defaults to local directory with "." if no arg passed"""
            total = 0
            with os.scandir(path) as directory:
                for entry in directory:
                    # Ignore symlinks that could lead to inf recursion
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += _get_dir_size(entry.path)
            return total

        def _get_largest_entry(file_list):
            """Locates largest directory housing file from a list of files"""

            # Create tmp dict
            dir_sizes = {}
            # Iter and assign plist as key and parent dir size as val
            for file in file_list:
                dir_sizes[file] = _get_dir_size(os.path.dirname(file))
            # Get file associated with largest size
            likely_file = max(dir_sizes, key=dir_sizes.get)
            return likely_file

        def _pkg_expand(src, dst):
            """Expand a .pkg file.
            On macOS, uses pkgutil --expand-full.
            On Linux, uses libarchive-c to extract XAR then decompress each Payload (cpio.gz)."""
            if platform.system() == "Darwin":
                shell_cmd = f"pkgutil --expand-full '{src}' '{dst}'"
                return self._run_command(shell_cmd) is not False

            # Linux path -- requires libarchive-c (optional dependency)
            logging.getLogger("libarchive").setLevel(logging.WARNING)
            try:
                import libarchive  # noqa: PLC0415
            except (ImportError, AttributeError):
                log.fatal("libarchive-c and the system libarchive library are required on Linux")
                log.fatal("Install with: apt-get install libarchive13 && pip install libarchive-c")
                return False

            os.makedirs(dst, exist_ok=True)
            old_cwd = os.getcwd()
            src = os.path.abspath(src)

            # Pass 1: extract the outer XAR archive (gets PackageInfo, Distribution, Payload files)
            try:
                os.chdir(dst)
                libarchive.extract_file(src)
            except Exception as err:
                log.error(f"Failed to extract XAR archive '{src}': {err}")
                return False
            finally:
                os.chdir(old_cwd)

            # Pass 2: decompress each Payload (gzip+cpio) into its parent component dir
            for payload_path in Path(dst).glob("**/Payload"):
                try:
                    os.chdir(str(payload_path.parent))
                    libarchive.extract_file(str(payload_path))
                except Exception as err:
                    log.debug(f"Could not decompress Payload at {payload_path}: {err}")
                finally:
                    os.chdir(old_cwd)

            return True

        def _zip_extract(src, dst):
            """Extract a ZIP archive to dst using stdlib zipfile."""
            try:
                os.makedirs(dst, exist_ok=True)
                with zipfile.ZipFile(src, "r") as zf:
                    zf.extractall(dst)
                return True
            except Exception as err:
                log.error(f"Failed to extract ZIP '{src}': {err}")
                return False

        def _dmg_attach(src, dst, retry=True):
            """Subprocess runs hdiutil attach
            on source src, mounting at destination dst.
            retry=False prevents infinite recursion when the detach-and-reattach fallback also fails."""
            # Shell out to do DMG attach and validate success
            shell_cmd = f"hdiutil attach '{src}' -mountpoint '{dst}' -nobrowse -noverify -noautoopen"
            if self._run_command(shell_cmd) is not False:
                return True
            if not retry:
                return False
            # Run with shell=True to allow pipe
            shell_cmd = f"hdiutil info | grep '{src}' -A20 | grep -m 1 '/dev/disk' | awk '{{print $1}}'"
            raw_out = run(shell_cmd, stdout=PIPE, stderr=STDOUT, shell=True, check=False)
            mount_point = raw_out.stdout.decode().strip()
            log.debug(f"Located existing mount point for DMG at {mount_point}")
            log.debug(f"Attempting unmount of {mount_point}...")
            if _dmg_detach(mount_point) is not False:
                return _dmg_attach(src, dst, retry=False)
            return False

        def _dmg_detach(dst):
            """Subprocess runs hdiutil detach on destination dst"""
            shell_cmd = f"hdiutil detach '{dst}' -force"
            if self._run_command(shell_cmd) is not False:
                return True
            return False

        def _dmg_extract_linux(src, dst):
            """Extract a DMG on Linux using 7z. No root required.
            Handles HFS+, APFS, and APM-based DMGs. Non-zero exit is acceptable
            if app content was actually extracted (e.g. skipped symlinks)."""
            os.makedirs(dst, exist_ok=True)
            shell_cmd = f"7z x '{src}' -o'{dst}' -y"
            raw_out = run(shlex.split(shell_cmd), stdout=PIPE, stderr=STDOUT, shell=False, check=False)
            exit_code = raw_out.returncode
            decoded_out = raw_out.stdout.decode().strip()
            if exit_code == 0:
                return True
            log.debug(f"7z exited {exit_code} for '{os.path.basename(src)}'; checking for extracted content")
            if next(Path(dst).glob("**/*.app"), None) or next(Path(dst).glob("**/Info.plist"), None):
                return True
            log.error(f"'{shell_cmd}' failed with exit code {exit_code} and output '{decoded_out}'")
            return False

        def _has_applications_symlink_alias(dmg_path):
            """Determines if mounted DMG contains symlink or alias pointing to /Applications"""
            # If Applications symlink in DMG, likely a drag 'n' drop .app
            applications_dmg = False
            try:
                applications_dmg = next(dmg_path.glob("**/Applications")).is_symlink()
            except StopIteration:
                applications_dmg = False
                # If no symlink, check for alias
                # Checking for aliases and where they point is a pain
                for file in list(dmg_path.glob("*")):
                    # Run file on each file to check for kind Alias
                    if "Alias" in self._run_command(f"file -b '{file.absolute().as_posix()}'"):
                        log.debug(f"Found alias {file.absolute().as_posix()}")
                        # If found, grep to validate bookmark points to /Applications
                        if (
                            self._run_command(f"grep 'book.*mark.*Applications' '{file.absolute().as_posix()}'")
                            is not False
                        ):
                            log.debug(f"Found alias {file} pointing to /Applications")
                            applications_dmg = True
            return applications_dmg

        def _plist_find_return(exploded_pkg):
            """Locates all Info.plists within a provided expanded PKG path
            Identifies likely plist for core app (if multiple), populating
            dict with bundle ID, name, and version; returns dict and plist path"""
            # Make pathlib.Path obj from exploded PKG
            expanded_pkg_path = Path(exploded_pkg)
            # Find all Info.plists
            info_plist_paths = expanded_pkg_path.glob("**/Info.plist")
            # Rule out Info.plists in nonstandard dirs
            core_app_plists = [
                plist
                for plist in info_plist_paths
                if "Contents/Info.plist" in plist.as_posix()
                and all(
                    folder not in plist.as_posix()
                    for folder in (
                        "Extensions/",
                        "Frameworks/",
                        "Helpers/",
                        "Library/",
                        "MacOS/",
                        "PlugIns/",
                        "Resources/",
                        "SharedSupport/",
                        "opt/",
                        "bin/",
                    )
                )
            ]

            # If more than one found
            if len(core_app_plists) > 1:
                log.debug(f"Found multiple ({len(core_app_plists)}) core plists:\n{core_app_plists}")
                likely_plist = _get_largest_entry(core_app_plists)
            elif len(core_app_plists) == 1:
                likely_plist = core_app_plists[0]
            else:
                # If no plists found, raise RuntimeError to proceed with PackageInfo lookup
                raise RuntimeError

            log.debug(f"Found application plist at path '{likely_plist}'")

            # Quickly iter and assign all plist values we want
            def lookup_from_plist():
                with open(likely_plist, "rb") as f:
                    plist = plistlib.load(f)
                return {
                    k: plist.get(k)
                    for k in ("CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleDisplayName", "CFBundleName")
                }

            # Run and return
            return lookup_from_plist(), likely_plist

        def _pkg_metadata_find_return(exploded_pkg):
            """Locates all identifying metadata files within an expanded PKG path
            If multiple PackageInfo, attempts query of product vers from Distribution
            If PackageInfo, identifies likely PackageInfo (if multiple), populating values
            for PKG ID and PKG version if set and returning"""

            def _parse_pkg_xml_id_name(xml_file):
                """Parses PKG ID and version from either Distribution
                or PackageInfo XML file; returns tuple of ID and version"""
                # Convert to str if PosixPath
                if type(xml_file) is PosixPath:
                    xml_file = xml_file.as_posix()
                with open(xml_file) as f:
                    parsed_xml = ET.parse(f)
                if "Distribution" in xml_file:
                    try:
                        distro_pkg_info = parsed_xml.find("product").attrib
                    except AttributeError:
                        return None, None
                    pkg_id = distro_pkg_info.get("id")
                    pkg_vers = distro_pkg_info.get("version")
                elif "PackageInfo" in xml_file:
                    pkginfo_info = parsed_xml.getroot()
                    pkg_id = pkginfo_info.get("identifier")
                    pkg_vers = pkginfo_info.get("version")
                return pkg_id, pkg_vers

            # Make pathlib.Path obj from exploded PKG
            expanded_pkg_path = Path(exploded_pkg)
            # Find all Distribution/PackageInfo files
            distro_files = list(expanded_pkg_path.glob("**/Distribution"))
            # Find and sort PackageInfo(s) by size (largest parent dir first)
            package_infos = sorted(
                expanded_pkg_path.glob("**/PackageInfo"), key=lambda x: _get_dir_size(x.parent), reverse=True
            )

            # If more than one found
            if len(package_infos) > 1:
                # If map defined, search keys for PKG ID matching lookup
                if self.package_map:
                    for info in package_infos:
                        pkg_id, pkg_vers = _parse_pkg_xml_id_name(info)
                        if pkg_id in self.package_map.keys() and pkg_vers:
                            log.debug(f"Found matching PackageInfo file from PKG ID mapping '{pkg_id}'")
                            return pkg_id, pkg_vers
                # If no map, but Distro file found, source product vers and match to largest PackageInfo
                if distro_files:
                    distro_id, distro_vers = _parse_pkg_xml_id_name(distro_files[0])
                    log.debug(f"Found Distribution file with ID '{distro_id}' and version '{distro_vers}'")
                    # Match Distro vers to PackageInfo, assign vers, and return
                    # Assigns first match, so largest matching PKG by size is used
                    if distro_vers is not None:
                        for info in package_infos:
                            pkg_id, pkg_vers = _parse_pkg_xml_id_name(info)
                            if pkg_vers == distro_vers and pkg_id:
                                log.debug(f"Found PackageInfo file '{pkg_id}' matching Distro vers '{distro_vers}'")
                                return pkg_id, pkg_vers
                # If no map and no Distro file. get PackageInfo from largest dir by size
                likely_pkginfo = package_infos[0]
            elif len(package_infos) == 1:
                likely_pkginfo = package_infos[0]
            else:
                # Nothing returned? Raise
                log.error("No PackageInfo file found in PKG!")
                log.error(package_infos)
                raise Exception

            log.debug(f"Found PackageInfo file at path '{likely_pkginfo}'")

            # Read PackageInfo XML and parse PKG ID/version
            pkg_id, pkg_vers = _parse_pkg_xml_id_name(likely_pkginfo)
            if pkg_id and pkg_vers:
                return pkg_id, pkg_vers
            else:
                log.error("One of PKG ID/PKG version missing from PackageInfo!")
                log.error(f"See below for full PackageInfo output:\n{likely_pkginfo}")
                raise Exception

        def _pkg_expand_cleanup():
            """Cleans up temp dir and unmounts DMG if necessary"""
            if not hasattr(self, "dmg_is_mounted"):
                return True
            if self.dmg_is_mounted is True:
                _dmg_detach(self.tmp_dmg_mount)
                time.sleep(1)  # Allow OS to fully release the mount before rmtree
            try:
                self.temp_dir.cleanup()
            except OSError:
                log.debug("Attempting DMG unmount and cleaning up...")
                _dmg_detach(self.tmp_dmg_mount)
                time.sleep(2)
                try:
                    self.temp_dir.cleanup()
                except OSError:
                    log.error(f"Unable to clean up temp dir at {self.temp_dir.name}!")
                    return False
            log.debug(f"Cleaned up temp dir at {self.temp_dir.name}")
            return True

        ##############
        #### MAIN ####
        ##############

        # Process cleanup first if set
        if cleanup is True:
            _pkg_expand_cleanup()
            return True
        # Create temp dir and assign var for expanded PKG
        # Skip assignment if self vars already set
        self.temp_dir = tempfile.TemporaryDirectory() if self.temp_dir is None else self.temp_dir
        self.tmp_pkg_path = (
            os.path.join(self.temp_dir.name, self.pkg_name) if self.tmp_pkg_path is None else self.tmp_pkg_path
        )
        self.tmp_dmg_mount = (
            os.path.join(self.temp_dir.name, "dmg_mount") if self.tmp_dmg_mount is None else self.tmp_dmg_mount
        )
        try:
            self.copied_pkg_path
        except AttributeError:
            self.copied_pkg_path = None
        app_installer_path, chosen_pkg = None, None
        self.dmg_is_mounted = False
        if self.install_type == "image":
            if platform.system() == "Darwin":
                if _dmg_attach(self.pkg_path, self.tmp_dmg_mount) is False:
                    log.error("Unable to parse files as DMG failed to attach")
                    return Exception
                self.dmg_is_mounted = True
            else:
                if _dmg_extract_linux(self.pkg_path, self.tmp_dmg_mount) is False:
                    log.error("Unable to parse files as DMG failed to extract")
                    return Exception
            mounted_dmg = Path(self.tmp_dmg_mount)
            # If Applications symlink/alias in DMG, likely a drag 'n' drop .app
            applications_dmg = _has_applications_symlink_alias(mounted_dmg)
            # Locate either .app or .pkg within mounted DMG
            app_check = list(mounted_dmg.glob("**/*.app"))
            # Extend search to pkg and mpkg
            pkg_check = [file for file in mounted_dmg.glob("**/*") if file.suffix in (".pkg", ".mpkg")]
            # Logic check for Applications symlink first
            if applications_dmg is True:
                app_installer_path = self.tmp_dmg_mount
            # If that fails, use PKG even if .app present
            elif pkg_check:
                if len(pkg_check) > 1:
                    log.warning("Found multiple PKGs within DMG! Using largest as source...")
                    chosen_pkg = _get_largest_entry(pkg_check)
                else:
                    chosen_pkg = pkg_check[0]
            # Finally, proceed with .app
            elif app_check:
                app_installer_path = self.tmp_dmg_mount
            else:
                log.error("No .app, PKG, or Applications symlink found in extracted DMG")
                log.error("APFS-formatted DMGs require 7-Zip 21.07+ (install 'apt 7zip', not 'p7zip-full')")
                raise Exception
        if self.install_type == "zip":
            if _zip_extract(self.pkg_path, self.tmp_pkg_path) is False:
                log.error("Unable to parse files as ZIP failed to extract")
                raise Exception
            app_installer_path = self.tmp_pkg_path
        if self.install_type == "package" or chosen_pkg is not None:
            chosen_pkg = chosen_pkg or self.pkg_path
            log.debug(f"Selected '{chosen_pkg}' for remaining operations...")
            # If PKG expansion fails, raise Exception
            if not os.path.exists(self.tmp_pkg_path) and _pkg_expand(chosen_pkg, self.tmp_pkg_path) is False:
                log.error(f"Unable to parse files as PKG '{chosen_pkg}' failed to expand")
                raise Exception
            # If install type differs from package, copy PKG to tmp dir and call func again
            if self.install_type != "package":
                # Copy PKG so we can clean up our temp dir now
                self.copied_pkg_path = shutil.copy2(chosen_pkg, self.parent_dir)
                log.debug(f"Copied '{chosen_pkg}' to '{self.copied_pkg_path}'")
                self.pkg_path = self.copied_pkg_path
                # Need to reassign values since the PKG, not DMG, is now our source
                self.get_install_media_metadata(lookup_again=True)
            app_installer_path = app_installer_path or self.tmp_pkg_path
        # Just looking for installer/app ID if id_query is True
        if id_query is True:
            log.debug("Running ID query for installer media")
            if self.install_type == "package":
                self.map_id, _app_vers = _pkg_metadata_find_return(self.tmp_pkg_path)
            elif self.install_type == "image":
                plist_values, likely_plist = _plist_find_return(app_installer_path)
                self.map_id = plist_values["CFBundleIdentifier"]
            elif self.install_type == "zip":
                plist_values, _ = _plist_find_return(app_installer_path)
                self.map_id = plist_values["CFBundleIdentifier"]
            # Don't clean up dir if we're just querying for PKG ID
            # We'll be back later for the full app info
            return True
        # Try populating from Info.plist
        try:
            plist_values, likely_plist = _plist_find_return(app_installer_path)

            try:
                self.bundle_id = plist_values["CFBundleIdentifier"]
                self.app_vers = plist_values["CFBundleShortVersionString"]
            except KeyError as err:
                log.error(f"Could not read one or more required key(s) from plist! {' '.join(err.args)}")
                raise Exception

            # Try getting .app name from abs path of Info.plist
            likely_app_name = Path(likely_plist).parents[1].name
            bundle_display = plist_values.get("CFBundleDisplayName")
            bundle_name = plist_values.get("CFBundleName")
            # Dir could be named Payload in PKG, so validate name ends in .app
            # Otherwise assign as CFBundleDisplayName + .app, with CFBundleName as backup if DisplayName undefined
            # CFBundleName isn't 100% match for actual app bundle name, so BID used for primary validation instead
            self.app_name = (
                likely_app_name
                if likely_app_name.endswith(".app")
                else (bundle_display + ".app")
                if bundle_display is not None
                else (bundle_name + ".app")
                if bundle_name is not None
                else None
            )
            log.debug(
                f"\nApplication Name: '{self.app_name}'\nBundle Identifier: '{self.bundle_id}'\nApplication Version: '{self.app_vers}'"
            )
        # If no valid plist found, proceed with PackageInfo lookup
        except RuntimeError:
            log.warning("No valid app plist found in PKG!")
            log.info("Attempting lookup from PackageInfo file...")
            self.pkg_id, self.app_vers = _pkg_metadata_find_return(app_installer_path)
            log.info(f"Found PKG ID '{self.pkg_id}' with PKG Version '{self.app_vers}'")
            log.info("Will be used for audit enforcement if enabled")

    ######################
    # Custom LI Find Funcs
    ######################

    def _find_lib_item_match(self):
        """Searches for custom app to update from existing items in Iru library
        If none match, attempts to find custom app dynamically by PKG name similarity
        if more than one match found, collates metadata for matches and reports to Slack with error"""
        # Locate custom app by name
        log.info(f"Searching for '{self.custom_app_name}' from list of custom apps")
        app_picker = [app for app in self.custom_apps if self.custom_app_name == app.get("name")]
        # If not found, try to find dynamically
        if not app_picker:
            log.warning(f"No existing LI found for provided name '{self.custom_app_name}'!")
            if self.default_auto_create is True:
                log.info("Creating as new custom app...")
                return False
            if self.default_dynamic_lookup is True:
                log.info("Will try dynamic lookup from provided PKG...")
                return self._find_lib_item_dynamic()
        elif len(app_picker) > 1:
            # More than one hit, attempt to find match by SS category
            if self.ss_category_id and self.custom_app_enforcement == "no_enforcement":
                app_picker_by_ss = [
                    app
                    for app in app_picker
                    if app.get("show_in_self_service") is True
                    and app.get("self_service_category_id") == self.ss_category_id
                ]
                if len(app_picker_by_ss) == 1:
                    return next(iter(app_picker_by_ss))
            if self.default_dynamic_lookup is True:
                log.warning(f" More than one match ({len(app_picker)}) returned for provided LI name!")
                log.info("Will try dynamic lookup from provided PKG...")
                return self._find_lib_item_dynamic(app_picker)
            # If we get here, means we couldn't decide on a single match
            # Create Slack body str and notify of duplicates
            slack_body = ""
            # Iter over custom_apps
            for custom_app in app_picker:
                custom_app_id = custom_app.get("id")
                # Get PKG name without abs path
                custom_app_pkg = os.path.basename(custom_app.get("file_key"))
                custom_app_created = custom_app.get("created_at")
                custom_app_created_fmt = (
                    datetime.strptime(custom_app_created, "%Y-%m-%dT%H:%M:%S.%fZ")
                    .astimezone()
                    .strftime("%m/%d/%Y @ %I:%M %p")
                )
                custom_app_updated = custom_app.get("file_updated")
                custom_app_url = os.path.join(self.tenant_url, "library", "custom-apps", custom_app_id)
                custom_app_url = self._ensure_https(custom_app_url)
                # Append matching custom app names/MD to Slack body to post
                slack_body += f"*<{custom_app_url}|Custom App Created _{custom_app_created_fmt}_>*\n*PKG*: `{custom_app_pkg}` (*uploaded* _{custom_app_updated}_)\n\n"
            log.error(
                f"More than one match ({len(app_picker)}) returned for provided LI name! Cannot upload...\n{slack_body}"
            )
            self.slack_notify(
                "ERROR",
                f"Found Duplicates of Custom App {self.custom_app_name}",
                f"{slack_body}",
            )
            # Return None to bypass remaining steps
            return None
        try:
            return next(iter(app_picker))
        except StopIteration:
            return False

    def _find_lib_item_dynamic(self, possible_apps={}):
        """Uses SequenceMatcher to find most similarly named PKG in Iru to the newly built PKG
        Requires a minimum ratio of .8 suggesting high probability of match; takes matching PKGs
        and filters out any not matching the existing LI name (if provided); sorts by semantic version
        and if multiple matches found, iterates to find oldest Custom App entry and assigns as selection"""

        ####################
        # Dynamic population
        ####################
        # Define a function to parse the datetime strings
        def parse_dt(dt_str):
            """Parses datetime strings from Iru API into datetime objects"""
            try:
                return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S.%fZ").astimezone()
            except ValueError:
                return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%SZ").astimezone()

        # Get PKG names (no path) if .pkg is suffix
        all_pkg_names = [
            os.path.basename(app.get("file_key")) for app in self.custom_apps if app.get("file_key").endswith(".pkg")
        ]
        # Create dict to hold PKG names and their similarity scores
        similarity_scores = {}

        for pkg in all_pkg_names:
            # re.sub to remove the _ + random UUID chars prepended to .pkg
            similarity_scores[pkg] = difflib.SequenceMatcher(
                None, re.sub(r"_\w{8}(?=.pkg)", "", pkg), self.pkg_name
            ).ratio()

        # Sort dict by similarity scores
        sorted_similar_pkgs = dict(sorted(similarity_scores.items(), key=lambda k: k[1], reverse=True))

        # Gaudy gauntlet of regex formatting to sanitize the version
        re_replacements = {r"_\w{8}(?=.pkg)": "", r"[ ]": ".", "[^0-9\\.]": "", r"[.]{2,}": ".", r"^\.|\.$": ""}
        # Setting limit to .85 is the sweet spot to account for variations in versions
        # Still high enough to exclude both version and name changes (reducing false positives)
        ratio_limit = 0.85
        # Grab all PKG names that are above our sim threshold
        possible_pkgs = [pkg for pkg in sorted_similar_pkgs.keys() if sorted_similar_pkgs.get(pkg) >= ratio_limit]

        # If possible_apps defined, we were given a specific name to validate against
        provided_app_name = None
        if possible_apps:
            matching_pkgs = []
            for possible in possible_apps:
                # Any matches are added to matching_pkgs list
                matching_pkgs.extend(pkg for pkg in possible_pkgs if pkg in possible.get("file_key"))
            # One or more matches, reassign var
            if matching_pkgs:
                possible_pkgs = matching_pkgs
            # Assign provided_app_name as unique name from possible_apps (should only be one)
            provided_app_name = "".join({possible.get("name") for possible in possible_apps})

        # Dict to hold PKG names and their sanitized vers strs for semantic parsing
        pkgs_versions = {
            maybepkg: reduce(
                lambda parsed_vers, match_replace: re.sub(*match_replace, parsed_vers),
                re_replacements.items(),
                maybepkg,
            )
            for maybepkg in possible_pkgs
        }

        # Sort PKGs according to semantic versioning
        pkgs_versions_sorted = dict(
            sorted(pkgs_versions.items(), key=lambda k: packaging_version.parse(k[1]), reverse=True)
        )

        try:
            custom_app = None
            # Iter over it and grab first item with highest vers
            custom_pkg_name, custom_pkg_vers = next(iter(pkgs_versions_sorted.items()))

            # Get custom PKG name with highest version
            highest_vers = [
                pkg for pkg in pkgs_versions_sorted.keys() if custom_pkg_vers in pkgs_versions_sorted.get(pkg)
            ]
            # Check if more than one vers found matching highest
            if len(highest_vers) > 1:
                # Create dict to hold PKG names and their mod dates
                pkg_custom_app_updated = {}
                for pkg in highest_vers:
                    try:
                        # Find the matching app record
                        app_record = next(app for app in self.custom_apps if pkg in app.get("file_key"))
                        pkg_uploaded = app_record.get("file_updated")
                        custom_li_modified = app_record.get("updated_at")
                        # Append to dict
                        pkg_custom_app_updated[pkg] = {
                            "pkg_uploaded": pkg_uploaded,
                            "custom_li_modified": custom_li_modified,
                        }
                    # Not found if searching only names matching user input
                    except StopIteration:
                        pass
                # Find the oldest app by first pkg_uploaded, and if identical, custom_li_modified
                oldest_app = min(
                    pkg_custom_app_updated,
                    key=lambda key: (
                        parse_dt(pkg_custom_app_updated[key]["pkg_uploaded"]),
                        parse_dt(pkg_custom_app_updated[key]["custom_li_modified"]),
                    ),
                )
                custom_pkg_name = oldest_app

            # Assign this as our best guess PKG
            matching_entry = [app for app in self.custom_apps if custom_pkg_name in app.get("file_key")]
            if len(matching_entry) > 1:
                if provided_app_name is not None:
                    matching_entry = [app for app in matching_entry if provided_app_name in app.get("name")]
            custom_app = next(iter(matching_entry))
            custom_app_id = custom_app.get("id")
            custom_name = custom_app.get("name")
            log.info(f"Found match '{custom_name}' with ID '{custom_app_id}' for provided PKG")
            log.info("Proceeding to update...")
            return custom_app
        except StopIteration as si:
            log.error(f"Found no match for provided LI name! Error {si}; cannot upload...")
            return False

    ####################################
    ######### PUBLIC FUNCTIONS #########
    ####################################

    def slack_notify(self, status, text_header, text_payload, title_link=None):
        """Posts to an indicated Slack channel, accepting arguments for
        text_header (header), text_payload (body), and opt arg title_link (header link)"""
        # Return if no val found for Slack webhook
        if self.slack_channel is None:
            return False

        if status == "SUCCESS":
            # Set alert color to green
            color = "00FF00"
        elif status == "WARNING":
            # Set alert color to orange
            color = "E8793B"
        elif status == "ERROR":
            # Set alert color to red
            color = "FF0000"

        # Construct payload
        slack_payload = {"attachments": [{"color": color, "title": f"{status}: {text_header}", "text": text_payload}]}
        if title_link:
            title_link = self._ensure_https(title_link)
            slack_payload["attachments"][0]["title_link"] = title_link
        slack_response = requests.post(
            self.slack_channel, headers=self.headers, data=json.dumps(slack_payload), timeout=HTTP_TIMEOUT
        )
        if slack_response.status_code <= 204:
            log.info("Successfully posted message to Slack channel")
        else:
            log.error(f"Failed to post {text_payload} to Slack channel! Got {slack_response.text}")
