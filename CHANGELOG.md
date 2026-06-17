## v2.1.0
---
#### **Breaking changes**:
- The tool has been renamed from `kpkg` to `irupkg` (Iru Packages).
  - The `kpkg` command remains available as a deprecated alias and will print a warning to stderr on each invocation. Update scripts and workflows to use `irupkg`.
  - Python import path changed: `import irupkg` (was `import kpkg`); `IrupkgResult` and `IrupkgError` replace `KpkgResult` and `KpkgError`.
  - The `new_app_naming` config default is now `APPNAME (irupkg)` -- existing `config.json` files with `APPNAME (kpkg)` continue to work unchanged.
- `KANDJI_API_URL` --> `IRU_API_URL`, `KANDJI_TOKEN` --> `IRUPKG_TOKEN`. Update scripts, CI workflows, and `.env` files to use the new names; the legacy names still work but print a deprecation warning to stderr on each invocation.
- `KANDJI_TOKEN_NAME` --> `IRUPKG_TOKEN_NAME`. The default token name is now `IRUPKG_TOKEN` (was `KANDJI_TOKEN`). Update the value stored in your keystore and any references in `config.json` or environment variables.
- `KPKG_LOCAL_DIR` --> `IRUPKG_LOCAL_DIR`, `KPKG_CONFIG_DIR` --> `IRUPKG_CONFIG_DIR`. Update any scripts or CI workflows that set these variables; the legacy names still work but print a deprecation warning to stderr.
- `config.json` top-level key `"kandji"` --> `"iru"`. Existing config files with `"kandji"` continue to work but print a deprecation warning to stderr; rename the key to silence it.
- Default data directories: `~/Library/KandjiPackages` --> `~/Library/IruPackages` (macOS), `~/.local/share/kpkg` --> `~/.local/share/irupkg` (Linux). If only the old directory exists, irupkg continues to use it and prints a warning to stderr; move it to the new location to silence the warning.
- macOS data directory: `~/Library/KandjiPackages` --> `~/Library/IruPackages`. One-shot migration runs automatically on `.pkg` upgrade and on first `irupkg-setup` invocation.
- macOS LaunchAgent label: `io.kandji.kpkg.brewcron` --> `com.iru.irupkg.brewcron`. Prior agent is unloaded and removed automatically during migration.
- macOS enforcement-delay plist: `/Library/Preferences/io.kandji.enforcement.delay.plist` --> `/Library/Preferences/com.iru.irupkg.enforcement.delay.plist`. Any in-flight enforcement delays recorded under the old path are not migrated; affected apps may re-prompt for enforcement once after upgrade.
- macOS bundle ID: `io.kandji.kpkg` --> `com.iru.irupkg`.
- `kpkg-setup` --> `irupkg-setup`. The `kpkg-setup` symlink is retained as a deprecated alias pointing at the same target.
- Docker Linux OS user renamed: `kpkg` --> `irupkg`; WORKDIR `/kpkg` --> `/irupkg`; data directory `/home/kpkg/.local/share/kpkg` --> `/home/irupkg/.local/share/irupkg`. Rebuild the image after pulling.
- `docker-compose.yml` service renamed: `kpkg-scheduler` --> `irupkg-scheduler`. Update any scripts or tooling that reference `docker compose up kpkg-scheduler`.
- GitHub Actions workflow renamed: `kpkg-brew-scheduler.yml` --> `irupkg-brew-scheduler.yml`; env var `KPKG_LOCAL_DIR` --> `IRUPKG_LOCAL_DIR` in workflow env block.

## v.2.0.0
---
#### **Breaking changes**:
- macOS install path moved from `/usr/local/bin/` to `~/.local/bin/` for both `kpkg` and `kpkg-setup`.
  - The `.pkg` installer now builds a wheel and runs `uv tool install` as the console user, then calls `uv tool update-shell` to inject `~/.local/bin` into the shell profile.
  - `kpkg-setup` is symlinked into `~/.local/bin` pointing at `~/Library/KandjiPackages/setup.zsh`.
  - `postinstall` removes the legacy `/usr/local/bin/kpkg` and `/usr/local/bin/kpkg-setup` symlinks plus the `~/Library/KandjiPackages/kpkg` + `.kpkg_py_framework` PyInstaller payload from prior versions. Open a fresh shell (or `source ~/.local/bin/env`) after upgrade if either binary isn't on `PATH` immediately
- Python 3.13+ is now required for `uv tool install kpkg`, `uvx kpkg`, and source installs. The macOS `.pkg` installer is unaffected as `uv` provisions a managed Python automatically
- `ENV_KEYSTORE` no longer *gates* environment-variable token lookup, but *promotes* it to the primary source (checked before Keychain on macOS).
  - On macOS, ENV is consulted only when `token_keystore.environment` is `true` (or `ENV_KEYSTORE=1` is set)
  - On Linux/Docker/CI, ENV is always tried as a final fallback.
  - `ENV_KEYSTORE` is now evaluated as a truthy value (`1`/`true`/`yes`/`on`) rather than mere presence
- Module layout moved from top-level `kpkg.py` + `helpers/` to `src/kpkg/` package layout; affects only callers that imported submodules directly (e.g. `from helpers.configs import ...`)
#### **Features**:
- Added ZIP (`.zip`) support as a new Custom App install type
  - `kpkg` detects `.zip` files by magic bytes and sets `install_type: zip` in the Kandji API payload
  - The `.app` bundle inside the ZIP is extracted to a temp dir; `Info.plist` is read for bundle ID, version, and app name (same metadata path as DMG drag-and-drop apps)
  - `unzip_location` (default: `/Applications`) is included in the create/update API payload and is configurable via:
    - CLI flag `-u`/`--unzip-location`
    - Per-app `unzip_location` key in `package_map.json`
    - `zz_defaults.unzip_location` in `config.json`
- Added Linux and Docker support
  - `Dockerfile` provided for container-based deployments (CI/CD pipelines, Linux hosts)
  - PKG extraction on Linux via `libarchive-c`; DMG extraction via 7-Zip 26.01 (installed from upstream, supporting APFS and APM+HFS+ images)
  - Token is automatically read from environment variable
  - Brew is installed on Linux and available for package fetch/metadata consumption
  - Cross-platform `brew fetch`: on Linux, dynamically resolves the newest macOS bottle tag from Homebrew's API to pass correct `--os`/`--arch` flags
    - e.g. `--os tahoe --arch arm`
- Added Docker Compose scheduled runner (`docker-compose.yml` + `scheduler.sh`) for loop-based cask monitoring at a configurable interval
  - Reads `brew_cron.json` each cycle so config changes take effect without restart
  - `brew_cron.json` is volume-mounted from the host; edits are picked up on the next iteration without rebuilding the image
  - Environment variable validation at compose level -- container refuses to start if `KANDJI_API_URL`/`KANDJI_TOKEN` are missing
  - `entrypoint.sh` supports ad-hoc `docker run` usage; auto-reads `brew_cron.json` when no args are provided
  - `.env.example` provided; copy to `.env` and populate `KANDJI_API_URL`, `KANDJI_TOKEN`, and optionally `SLACK_TOKEN`
- Added interactive setup wizard: `kpkg -S`/`--setup`
  - Delegates to `setup.zsh` in `~/Library/KandjiPackages` when running on macOS host; falls back to a pure-Python wizard for portable, container, or `uv`-run installs
  - All `kpkg-setup` flags are supported when delegating (e.g. `kpkg --setup -b`, `kpkg --setup -r`, `kpkg --setup -h`); shared flags are correctly forwarded rather than intercepted by `kpkg`'s own argument parser
- Added ENV-based auto-configuration: if `KANDJI_API_URL` is set and no `config.json` exists on disk, `kpkg` auto-generates one from environment variables at runtime (uses `KANDJI_TOKEN_NAME` if set, else `KANDJI_TOKEN`)
- Added GitHub Actions scheduled workflow (`.github/workflows/kpkg-brew-scheduler.yml`) as a third runtime mode alongside the macOS LaunchAgent and Docker Compose scheduler -- installs uv, 7-Zip 26.01 (with pinned sha256), and Homebrew on `ubuntu-latest`, runs `brew update`, then invokes `kpkg -b` for every cask in `brew_cron.json`
  - Reads `KANDJI_API_URL`, `KANDJI_TOKEN`, and `SLACK_TOKEN` from repo secrets; pins `KPKG_LOCAL_DIR` to the checked-out workspace
  - `schedule:` trigger is committed in disabled form (`0 */2 * * *`, every two hours) -- enable after merging to the default branch since GitHub only fires `schedule` from `main`
- Added a small public Python API: `kpkg.process_pkg(path, ...)` and `kpkg.process_brew(cask, ...)` return a `KpkgResult(source, pkg_name, action, error)` instead of logging-only output, so callers can branch on success/failure and run `kpkg` programmatically without invoking the CLI; `__init__.py` is trimmed to expose just `KpkgError`, `KpkgResult`, `PackageOptions`, `process_brew`, `process_pkg`
- Added ENV-based Slack auto-enable: if `SLACK_TOKEN` (or the name configured in `slack.webhook_name`) is present in the environment and environment-based token lookup is enabled (the default in auto-generated configs, or forced via `ENV_KEYSTORE=1`), Slack notifications are automatically enabled without requiring `"enabled": true` in `config.json`
- `libarchive-c` is included as a platform-gated required dependency (`sys_platform == 'linux'`); `uv tool run kpkg.whl` automatically installs it without needing to specify `[linux]` extras
#### **Bug fixes**:
- Fixed app name parsing for installer filenames where the version number immediately follows the app name without whitespace (e.g. `Rectangle0.91.dmg` now correctly resolves to `Rectangle` instead of `Rectangle0`)
- Improved DMG cleanup reliability -- added short delays after unmount to allow the OS to fully release the mount point before temp dir removal
#### **Miscellaneous**:
- ENV token lookup is attempted as a final fallback on Linux/Docker/CI without needing `ENV_KEYSTORE=1`. On macOS the fallback is omitted; `config.json` controls which sources are consulted, and `ENV_KEYSTORE=1` is the explicit opt-in
- Auto-config generation from environment variables now requires only `KANDJI_API_URL` (`ENV_KEYSTORE` no longer required as a separate trigger)
- Modernized build tooling: `pyproject.toml` + `uv` replace the manual Python framework download in `build.zsh`
- Consolidated the version source of truth: the wheel version is now read dynamically from the top-level `VERSION` file (no more duplicate `src/kpkg/resources/VERSION` or literal in `pyproject.toml` to keep in sync)
- `audit_app_and_version.zsh` and `setup.zsh` are now bundled into the wheel/sdist (via a `kpkg.scripts` subpackage); `kpkg -p ...` and `kpkg --setup` materialize the packaged copies into `parent_dir` on first use when they're not already present, so `uv tool install git+https://github.com/kandji-inc/irupkg.git` is self-contained
- Improved Homebrew cache path detection: now uses `brew --cache --cask` + directory glob instead of fragile output string parsing
- Added unit test suite (`tests/python/`) covering token retrieval, config parsing, API response validation, enforcement mapping, and app/library matching (`uv run pytest tests/python/`), plus bats coverage for `setup.zsh` argument parsing and helper functions (`tests/test_setup.bats`)

## v.1.1.0
---
#### **Features**:
- Added new functionality to configure/create a background service (macOS LaunchAgent) to periodically execute `kpkg -b` against a list of brew casks
  - **kpkg Brew Cron** enables recurring Homebrew cask checks, automatically uploading/deploying new versions to Kandji
  - Initial configuration and service can be created by running `kpkg-setup -b`; see [updated README documentation](README.md#brew-cron) for more detail
#### **Bug fixes**:
- Improved error handling/response when a required token is invalid/missing
- Improved HTTP status code handling for Kandji API responses
- Improved secrets population in ENV to update an existing token entry when running a reset
- Resolved a rare issue where Slack webhook validation could fail during initial setup
#### **Miscellaneous**:
- Improved dynamic naming for Kandji custom apps to filter out versions detected in installer media

## v.1.0.2
---
#### **Features**:
- Added shasum comparison for pending uploads and existing apps, skipping installer upload/Library Item update if `sha256` hashes match
#### **Bug fixes**:
- Resolved an issue where certain `.pkg` values were assigned out of order with package mapping enabled
- Resolved an issue where a false positive was erroneously logged when a matching map value was not found
#### **Miscellaneous**:
- Modified order in which upload occurs so certain create/update checks run first
- Added `-f` flag (equivalent to `--no-rcs`) to `#!/bin/zsh` in `audit_app_and_version.zsh` and installer `postinstall`
  - This suppresses the evaluation/execution of user `zsh` dotfiles during runtime

## v.1.0.1
---
#### **Bug fixes**:
- Resolved an issue where a config-defined Self Service category missing in Kandji caused an error
- Resolved an issue cleaning up a mounted `.dmg` at the end of runtime
#### **Miscellaneous**:
- Added new GH templates for feature requests and bug reports

## v.1.0.0
---
### INITIAL RELEASE
- Initial release of Kandji Packages!
- [See here](README.md) for more detail
