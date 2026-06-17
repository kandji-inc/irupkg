# Iru Packages (`irupkg`)

Standalone tool for programmatic management of Iru Custom Apps

## Table of Contents
- [About](#about)
- [Migrating from kpkg](#migrating-from-kpkg)
- [Prerequisites](#prerequisites)
- [Breaking Changes (2.0.0)](#breaking-changes-200)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Configuration Options](#configuration-options)
  - [Iru Packages Config](#iru-packages-config)
  - [Command Line Flags](#command-line-flags)
  - [Package Map](#package-map)
  - [Brew Cron](#brew-cron)
- [Runtime Considerations](#runtime-considerations)
  - [Supported Custom Apps](#supported-custom-apps)
  - [Linux / Docker](#linux--docker)
  - [Enforcements](#enforcements)
  - [Custom App Behavior](#custom-app-behavior)
- [Technical Details](#technical-details)
  - [Secrets Management](#secrets-management)
  - [Iru Token Permissions](#iru-token-permissions)
  - [Slack Token Setup](#slack-token-setup)
  - [config.json](#configjson)
  - [package_map.json](#package_mapjson)
  - [brew_cron.json](#brew_cronjson)
  - [irupkg Flags](#irupkg-flags)
  - [irupkg-setup Flags](#irupkg-setup-flags)
  - [Audit/Enforcement Examples](#audit-enforcement-examples)

## About
A command-line tool designed for programmatic management of Iru Custom Apps

Configurable in a variety of ways, Iru Packages can be used to create, update, and enforce Custom Apps in Iru

Fully open source, we welcome contributions and feedback to improve the tool!

## Migrating from kpkg

> [!IMPORTANT]
> **`kpkg` has been renamed to `irupkg` as of v2.1.0.**
> The `kpkg` command still works but prints a deprecation warning to stderr on every invocation.
> Update your scripts and workflows to use `irupkg`.

**What changed:**
- CLI command: `kpkg` --> `irupkg`
- Python import: `import kpkg` --> `import irupkg`
- Python classes: `KpkgResult` --> `IrupkgResult`, `KpkgError` --> `IrupkgError`
- Environment variables: `KPKG_LOCAL_DIR`/`KPKG_CONFIG_DIR` --> `IRUPKG_LOCAL_DIR`/`IRUPKG_CONFIG_DIR` (legacy names still work but print a deprecation warning)
- Default data directories: `~/Library/KandjiPackages` --> `~/Library/IruPackages` (macOS), `~/.local/share/kpkg` --> `~/.local/share/irupkg` (Linux); if only the old directory exists, it is still used (with a warning)
- `new_app_naming` config default: `APPNAME (kpkg)` --> `APPNAME (irupkg)` (existing `config.json` files are unaffected)

## Prerequisites

- Iru API token ([required permissions](#iru-token-permissions))
- Slack webhook token (optional; [setup instructions](#slack-token-setup))
- Python 3.13+ for `uv tool install irupkg`, `uvx irupkg`, and source installs (the macOS `.pkg` installer is unaffected -- `uv` provisions a managed Python for you)

## Breaking Changes (`2.0.0`)

If you are upgrading from a `1.x` release, read these first:

- **Install paths moved from `/usr/local/bin/` to `~/.local/bin/`** for both `kpkg` and `irupkg-setup`. The macOS `.pkg` installer builds a wheel, runs `uv tool install` as the console user, and calls `uv tool update-shell` to inject `~/.local/bin` into your shell profile. `irupkg-setup` is symlinked into `~/.local/bin` pointing at `~/Library/IruPackages/setup.zsh`; `kpkg-setup` is kept as a deprecated alias pointing at the same target.
  - `postinstall` removes the legacy `/usr/local/bin/kpkg` and `/usr/local/bin/kpkg-setup` symlinks plus the older `~/Library/KandjiPackages/kpkg` + `.kpkg_py_framework` PyInstaller payload. If either binary isn't on `PATH` after upgrade, open a new shell or `source ~/.local/bin/env`
- **Python 3.13+ required** for non-`.pkg` installs (see [Prerequisites](#prerequisites))
- **`ENV_KEYSTORE=1` semantics changed.** It no longer *gates* environment-variable token lookup, but instead *promotes* ENV to the primary source (checked before Keychain). On Linux/Docker/CI, ENV is always tried as a final fallback. On macOS, ENV is consulted only when `token_keystore.environment` is `true` (or `ENV_KEYSTORE=1` is set)

## Quick Start

Choose the method that fits your environment:

<details>
<summary><strong>macOS <code>.pkg</code> installer</strong></summary>

```sh
# 1. Download and install the latest release
#    https://github.com/kandji-inc/irupkg/releases/latest
#    Right-click the .pkg -> Open, then follow the prompts

# 2. Run interactive setup (writes ~/Library/IruPackages/config.json)
irupkg-setup

# 3. Upload a local installer
irupkg -p /path/to/App.pkg

# -- or -- fetch and upload a Homebrew cask
irupkg -b google-chrome
```

> **Note:** Both binaries install into `~/.local/bin/` on 2.0.0+ -- if either isn't on `PATH` after install, open a fresh shell or `source ~/.local/bin/env`. Upgrading from 1.x? See [Breaking Changes](#breaking-changes-200).

> **Tip:** `irupkg --setup` is equivalent to `irupkg-setup` and accepts the same flags (e.g. `-b` for Brew Cron, `-r` to reset credentials). LaunchAgent-related flags (`-b`/`-a`/`-u`) are macOS-only; on Linux/Docker/uv use the Docker Compose scheduler or GitHub Actions workflow (see [Linux / Docker](#linux--docker)).

> [!NOTE]
> `kpkg-setup` still works as a deprecated alias for `irupkg-setup`.

> **Note:** `kpkg` still works as a deprecated alias and will print a warning to stderr. Update your invocations to `irupkg`.

</details>

<details>
<summary><strong>macOS / Linux <code>uv</code></strong></summary>

> **Upgrading from a local `.whl`:** Use `--force` to overwrite the existing install:
> ```sh
> uv tool install --force dist/irupkg-2.x.x-py3-none-any.whl
> ```

```sh
# 1. Install from GitHub
uv tool install git+https://github.com/kandji-inc/irupkg.git

# -- or pin to a release --
uv tool install git+https://github.com/kandji-inc/irupkg.git@v2.1.0

# 2. Run interactive setup
irupkg --setup

# 3. Upload a local installer
irupkg -p /path/to/App.pkg

# -- or -- fetch and upload a Homebrew cask
irupkg -b google-chrome
```

> **Tip:** Run without installing permanently: `uvx --from git+https://github.com/kandji-inc/irupkg.git irupkg --setup`, then `uvx --from git+https://github.com/kandji-inc/irupkg.git irupkg -p /path/to/App.pkg`.
>
> `audit_app_and_version.zsh` and `setup.zsh` are bundled into the wheel; irupkg materializes them into the resolved data directory (`IRUPKG_LOCAL_DIR` --> `IRUPKG_CONFIG_DIR` --> `~/Library/IruPackages` on macOS, or `platformdirs` default elsewhere). `setup.zsh` is refreshed from the wheel on every `--setup` run; `audit_app_and_version.zsh` is written on first use only. No `IRUPKG_CONFIG_DIR` or working-directory gymnastics required.

> **Note:** `kpkg` still works as a deprecated alias and will print a warning to stderr. Update your invocations to `irupkg`.

</details>

<details>
<summary><strong>Docker / Linux</strong></summary>

```sh
# 1. Populate credentials
cp .env.example .env          # set IRU_API_URL and IRUPKG_TOKEN (and optionally SLACK_TOKEN)

# 2. Build the image
docker build -t irupkg .

# 3. Upload a local installer
docker run --rm --env-file .env \
  -v "$(pwd)":/pkgs \
  irupkg -p /pkgs/App.pkg

# -- or -- fetch and upload a Homebrew cask
docker run --rm --env-file .env \
  irupkg -b google-chrome
```

For the recurring `docker compose` scheduler, see [Linux / Docker](#linux--docker).

</details>

<details>
<summary><strong>Python API</strong></summary>

`irupkg` is also installable as an importable module (`uv pip install .` from the project root, or `uv add irupkg` from another project).

```python
import irupkg

# Upload a local installer
result = irupkg.process_pkg(
    "/path/to/App.pkg",
    name="MyApp",
    sscategory="Productivity",
)
if result.action == "failed":
    raise RuntimeError(result.error)

# Fetch and upload a Homebrew cask
irupkg.process_brew("google-chrome")

# Many casks -- just a loop
results = [irupkg.process_brew(c, dry=True) for c in ("firefox", "slack", "zoom")]
for r in results:
    print(r.source, r.action)
```

Both functions return an `IrupkgResult(source, pkg_name, action, error)`, where `action` is `"succeeded"`, `"skipped"` (no-op because the existing Custom App's installer sha256 already matches), `"failed"`, or `"dry_run"`. They accept the same options the CLI exposes via flags -- `name`, `testname`, `sscategory`, `zzcategory`, `dry`, `create`, `unzip_location`, plus an optional `parent_dir` to override config discovery (otherwise the same `IRUPKG_LOCAL_DIR` / `IRUPKG_CONFIG_DIR` / platform-default resolution as the CLI is used).

The same `config.json` and token keystore that the CLI uses are required; run `irupkg --setup` (or `irupkg-setup`) once to generate them before calling either function.

</details>

## Usage

`irupkg` (*macOS and Linux*) and `irupkg-setup` (*macOS-only*) are the two Iru Packages binaries

`irupkg` is the main executable, and must be called with one of two required flags: `-p`/`-b`
  - `-p` accepts a local path to a valid `.pkg`, `.dmg`, or `.zip` for upload
  - `-b` accepts a Homebrew cask name; `brew` download must be a valid `.pkg`, `.dmg`, or `.zip` for upload

### Examples

#### Uploading to Iru from existing download:

- Download the latest Google Chrome installer [here](https://dl.google.com/dl/chrome/mac/universal/stable/gcem/GoogleChrome.pkg)
- Run `irupkg -p /path/to/downloaded/GoogleChrome.pkg`
- If the package is not found in Iru, Iru Packages will create a new Custom App
- If the package is found (by name), Iru Packages will update the existing Custom App

```
2024-04-15 04:40:17 PM [MacBook Pro]: INFO: Processing 'GoogleChrome.pkg'
2024-04-15 04:40:19 PM [MacBook Pro]: INFO: Located matching map value 'com.google.Chrome' from PKG/DMG
2024-04-15 04:40:19 PM [MacBook Pro]: INFO: Beginning file upload of 'GoogleChrome.pkg'...
2024-04-15 04:41:09 PM [MacBook Pro]: INFO: Successfully uploaded 'GoogleChrome.pkg'!
2024-04-15 04:41:09 PM [MacBook Pro]: INFO: Searching for 'Google Chrome (GA)' from list of custom apps
2024-04-15 04:41:09 PM [MacBook Pro]: WARNING: (HTTP 503): The upload is still being processed.
2024-04-15 04:41:09 PM [MacBook Pro]: INFO: Retrying in five seconds...
2024-04-15 04:41:14 PM [MacBook Pro]: INFO: Searching for 'Google Chrome (GA)' from list of custom apps
2024-04-15 04:41:15 PM [MacBook Pro]: INFO: SUCCESS: Custom App Update
2024-04-15 04:41:15 PM [MacBook Pro]: INFO: Custom App 'Google Chrome (GA)' available at 'https://accuhive.iru.com/library/custom-apps/1436cf21-a777-49c4-8e4c-386ca3107a9a'
2024-04-15 04:41:15 PM [MacBook Pro]: INFO: Successfully posted message to Slack channel
2024-04-15 04:41:15 PM [MacBook Pro]: INFO: Searching for 'Google Chrome (Patch Testers)' from list of custom apps
2024-04-15 04:41:16 PM [MacBook Pro]: INFO: SUCCESS: Custom App Update
2024-04-15 04:41:16 PM [MacBook Pro]: INFO: Custom App 'Google Chrome (Patch Testers)' available at 'https://accuhive.iru.com/library/custom-apps/e2c2b6ce-da42-4b54-9f85-2abfaf8f4274'
2024-04-15 04:41:16 PM [MacBook Pro]: INFO: Successfully posted message to Slack channel
```
|<img src="https://github.com/kandji-inc/support/assets/27963671/23123c99-b9ba-4286-afec-87852928a5bf" width="400">|
|:-:|
|Slack notifications sent to channel from local run|

#### Uploading to Iru, sourcing/downloading updates from Homebrew

- Ensure Homebrew is installed on-disk with `brew` available in your `PATH`
  - If not installed, download the [latest installer package here](https://github.com/Homebrew/brew/releases/latest)
- Recommend running `brew search --casks CASK` to confirm the correct cask name

```
➜  ~ brew search --casks googlechrome
==> Casks
google-chrome
```
- Run `irupkg -b CASK`
- Will use local download if present, otherwise fetches from Homebrew
- Same as above, if found in Iru, will update the existing Custom App, else creates new

```
2024-04-15 04:39:34 PM [MacBook Pro]: INFO: brew fetching 'coteditor'...
2024-04-15 04:39:36 PM [MacBook Pro]: INFO: Downloaded 'coteditor' to '~/Library/Caches/Homebrew/downloads/2f159e4270397f68161b6a891ab35a32085f02dbfef6c61191251ccd0278e2eb--CotEditor_4.7.4.dmg'
... (same upload + Custom App update flow as above) ...
2024-04-15 04:39:51 PM [MacBook Pro]: INFO: SUCCESS: Custom App Update
2024-04-15 04:39:51 PM [MacBook Pro]: INFO: Custom App 'CotEditor (Testing)' available at 'https://accuhive.iru.com/library/custom-apps/80db3b94-0a9c-4dfc-8191-6c982141a7e6'
```
|<img src="https://github.com/kandji-inc/support/assets/27963671/3b0971d7-70a5-42dd-809c-98b658915f8a" width="600">|
|:-:|
|Slack notifications sent to channel from brew run|

---

## Configuration Options

Iru Packages supports both runtime flags and centralized options for customizing your PKG/DMG/ZIP --> Iru workflow

Configuration files are stored in:
- **macOS**: `~/Library/IruPackages`
- **Linux**: `~/.local/share/irupkg`

Both paths can be overridden with `IRUPKG_LOCAL_DIR` or `IRUPKG_CONFIG_DIR` environment variables.

### Iru Packages Config

- `config.json` includes defaults if no per-recipe settings are found
  - Config can be modified as desired to set preferred defaults
  - [See below](#configjson) for an overview of available options and a sample config

### Command Line Flags

- `irupkg` accepts optional args to set/override the following:
  - Always create new Custom App
  - Dry run of Iru Packages (do not modify Iru)
  - Custom app name
  - Custom app name (test)
  - Self Service category
  - Self Service category (test)
  - Unzip destination path (for ZIP apps)
  - Interactive setup (for non-`.pkg` / container installs)
  - [See below](#irupkg-flags) for detailed usage instructions

> [!NOTE]
> If multiple configuration types are set during runtime, those passed via command line supersede any mappings

### Package Map

- A package map (`package_map.json`) can be defined to associate packages by ID to Iru Custom Apps
  - Key is the package ID
    - Run `irupkg-setup -i` to identify the package ID for one or multiple `.pkg`s (also accepts `.dmg`s)
    - [See below](#irupkg-setup-flags) for full usage instructions for `irupkg-setup`
  - Below values can be defined in-map:
    - Custom app name (`prod_name`)
    - Custom app name (test) (`test_name`)
    - Self Service category (`ss_category`)
    - Self Service category (test) (`test_category`)
    - Unzip destination path for ZIP apps (`unzip_location`; overrides `zz_defaults.unzip_location`)
  - [See below](#package_mapjson) for a sample config

> [!TIP]
> Running `irupkg-setup -m` exports a .csv containing Custom App names and Self Service categories to help populate `package_map.json`

### Brew Cron

- Version `1.1.0` introduces new functionality to configure/create a background service (macOS LaunchAgent) to periodically execute `irupkg -b` against a defined list of brew casks
  - A list of available Homebrew casks can be found [here](https://formulae.brew.sh/cask)
  - Service runtime logs to `~/Library/IruPackages/irupkg.log`, same as ad hoc `irupkg` executions
- To get started, run `irupkg-setup -b`, which:
  - Creates/loads a config file (`brew_cron.json`) defined with a list of Homebrew casks and runtime frequency
  - Populates from config and writes an agent to `~/Library/LaunchAgents/com.iru.irupkg.brewcron.plist`
    - The LaunchAgent is immediately loaded, then runs every `n` hours thereafter to check for Homebrew cask updates
      - If the brew source package matches the Iru installer (by shasum), `irupkg` skips the upload
      - If a change is detected, `irupkg` uploads the new version to Iru (and updates the accompanying audit script if enabled)
- If `~/Library/IruPackages/brew_cron.json` is missing, an interactive setup first asks how often to run (in hours), and which casks to monitor

```
04:59:44 PM : brew_cron.json config is missing or invalid
Create it now? (Y/N):y
Enter value for how frequently cron brew should run (in hours) (e.g. 1 – 168):
8
04:59:47 PM : irupkg brew cron will run every 8 hours

Enter value for brew casks which should run, comma-separated (e.g. google-chrome,firefox,slack):
grandperspective,suspicious-package

04:59:55 PM : Confirmed below casks are valid:

grandperspective,suspicious-package

04:59:56 PM : Wrote service with above casks scoped to ~/Library/LaunchAgents/com.iru.irupkg.brewcron.plist
04:59:56 PM : Successfully bootstrapped ~/Library/LaunchAgents/com.iru.irupkg.brewcron.plist -- service is now active
04:59:56 PM : Run the following to monitor progress (CTRL+C to quit):
tail -f ~/Library/IruPackages/irupkg.log
```

> [!NOTE]
> Once the service is activated via the LaunchAgent, you may see a Notification Center message display `"zsh" is an item that can run in the background.`

- If an existing config is present at `~/Library/IruPackages/brew_cron.json` when running `irupkg-setup -b`, the LaunchAgent is refreshed with those values and reloaded ([sample config](#brew_cronjson))
- Interactively add additional casks to `brew_cron.json` by calling `irupkg-setup -b -a`
- You can also add/remove casks by editing `brew_cron.json` directly
  - **NOTE**: If `brew_cron.json` is directly modified, run `irupkg-setup -b` to reload the LaunchAgent with the updated config
- To uninstall the Brew Cron service (unload and remove LaunchAgent), run `irupkg-setup -b -u`
  - This does **not** remove `brew_cron.json`, so the service can be reloaded at any time by re-running `irupkg-setup -b`

> [!TIP]
> On Linux/Docker, the same `brew_cron.json` format drives the Docker Compose scheduled runner. See [Linux / Docker](#linux--docker) for setup.

## Runtime Considerations

### Supported Custom Apps
- Currently, installer packages, disk images, and ZIP archives are supported by this project
  - Packages include flat, component, and distribution types (`.pkg`/`.mpkg`)
  - Disk image contents may include `.app` or `.pkg` (`.dmg`)
  - ZIP archives (`.zip`) containing a `.app` bundle; metadata is read from `Info.plist` inside the extracted `.app`; original `.zip` is uploaded to Iru with `install_type: zip`
- `.pkg`/`.dmg`/`.zip` uploads can be configured with any Iru enforcement type (see below)
  - This includes installers whose payloads are app bundles (`.app`) or command line tools/binaries
    - Audit/enforcement criteria are determined from:
      - An app bundle's `Info.plist`
      - A binary's installer package metadata (must contain version)

### Linux / Docker

irupkg supports Linux via the provided `Dockerfile`. This is useful for CI/CD pipelines or Linux hosts where the macOS `.pkg` installer is unavailable.

**Quick start:**
```sh
docker build -t irupkg .
docker run --rm \
  -e IRU_API_URL=tenant.api.iru.com \
  -e IRUPKG_TOKEN=your_token_here \
  -v "$(pwd)":/pkgs \
  irupkg -p /pkgs/YourApp.pkg
```

**Required environment variables:**
- `IRU_API_URL` — your Iru tenant API URL (e.g., `tenant.api.iru.com`)
- `IRUPKG_TOKEN` — your Iru API token value (name configurable via `IRUPKG_TOKEN_NAME`)

> `KANDJI_API_URL` and `KANDJI_TOKEN` are still accepted but will emit a deprecation warning. Rename to `IRU_API_URL` and `IRUPKG_TOKEN`.

**Optional environment variables:**
- `SLACK_TOKEN` -- Slack incoming webhook URL; when set in the environment **and** environment-based token lookup is enabled (the default for auto-generated configs, or forced anywhere via `ENV_KEYSTORE=1`), Slack notifications are automatically enabled without editing `config.json`. The variable name can be overridden via `slack.webhook_name` in `config.json`.

When `IRU_API_URL` is set and no `config.json` exists, irupkg auto-generates one from environment variables — no separate setup step required. Config is written to `~/.local/share/irupkg/config.json` on Linux. See [Secrets Management](#secrets-management) for the full ENV/Keychain lookup order.

For non-container Linux or `uv`-run installs, run `irupkg --setup` for interactive config setup.

**Scheduled runner (Docker Compose):**

The included `docker-compose.yml` runs `irupkg` on a recurring interval, reading cask names and frequency from `brew_cron.json`:

```sh
cp .env.example .env         # populate IRU_API_URL and IRUPKG_TOKEN
docker compose up -d          # start the scheduler
docker compose logs -f        # tail logs
docker compose down           # stop
```

`brew_cron.json` is volume-mounted read-only from the host. Edits take effect on the next scheduler iteration without rebuilding the image. See [brew_cron.json](#brew_cronjson) for the config format.

> [!NOTE]
> Use `docker compose up -d` (not `docker compose restart`) after modifying `docker-compose.yml` so Compose recreates the container with updated config.

> [!IMPORTANT]
> **arm64 binaries only**
> - On Linux, `brew fetch` is invoked with `--arch arm` plus a dynamically resolved `--os <macos_codename>` (e.g. `--os tahoe`)
>   - The codename is read at runtime from `https://formulae.brew.sh/api/formula/curl.json` so `irupkg` always tracks the newest macOS bottle tag Homebrew is publishing.
> - Casks are always sourced as their arm64 (Apple silicon) variant regardless of the host's architecture. If your fleet still needs Intel binaries, source them from macOS or modify `source_from_brew` in `src/irupkg/helpers/utils.py` accordingly.

> [!NOTE]
> The Compose scheduler runs `brew update` before each cycle so cask formulas (and their recorded `sha256`s) stay in sync with upstream. Without this, the container's tap is frozen at image-build time. Ad hoc `docker run irupkg -b <cask>` invocations do not auto-update; rebuild the image periodically or run `brew update` manually inside the container.

**Scheduled runner (GitHub Actions):**

`.github/workflows/irupkg-brew-scheduler.yml` provides a third runtime mode for environments where neither a long-lived container nor a macOS LaunchAgent is desirable. The workflow:

- Runs on `ubuntu-latest`, installs `uv`, Homebrew (Linuxbrew), and 7-Zip `26.01` (tarball pinned by `sha256`)
- Executes `brew update`, reads cask names from `irupkg/brew_cron.json`, and invokes `uv run irupkg -b <cask>...` for each
- Reads `IRU_API_URL`, `IRUPKG_TOKEN`, and (optionally) `SLACK_TOKEN` from repo secrets; pins `IRUPKG_LOCAL_DIR` to the workspace so irupkg never touches `$HOME`

The `schedule:` trigger is committed in disabled form (cron, every two hours, `0 */2 * * *`); GitHub only fires `schedule` from the default branch, so uncomment it after merging to `main`. Until then, the workflow can be invoked manually via `workflow_dispatch`.

### Enforcements
- Iru Packages supports three enforcement types (configurable in `config.json`), which sets enforcement type for new Custom Apps:
  - `audit_enforce` (Default)
  - `install_once`
  - `self_service`
- When updating _existing_ Custom Apps, Iru Packages will respect the enforcement type already set in Iru
- If method can't be read from `config.json`, enforcement defaults to `install_once`

> [!NOTE]
> When a Self Service category is defined via command line/map, enforcement is automatically set to `self_service` (ignoring `config.json`) during new app creation

#### `audit_enforce`
- Setting `audit_enforce` bundles `audit_app_and_version.zsh` for the Custom App's Audit Script during creation
  - App name, identifier, and version details are automatically populated in the audit script prior to upload
  - Subsequent updates to apps with audit enforcement receive an updated audit script with latest app info, version, and enforcement dates
- Up to two Custom App names can be specified (via command line or map), one for production workflows (`prod_name`) and the other for testing (`test_name`)
  - Production defaults to **5 days** prior to enforcement, with testing set to **0 days** (immediate enforcement)
    - Days until enforcement values are configurable in `config.json`
  - If `audit_enforce` is set but no values provided for `prod_name` or `test_name`, Iru Packages still uses the prod delay set in `config.json`
    - If delay values are removed from `config.json`, Iru Packages will fall back to an enforcement delay of **3 days**
- [See below](#audit-enforcement-examples) for Iru audit/enforcement output examples
- If enforcement is due, but the app in use by the user, the user will be prompted to close the app, else delay one hour
![Delay Available](https://github.com/kandji-inc/support/assets/27963671/c74148c5-5e8e-4673-a04e-e2ef480604f7)
- Once the delay has lapsed, the user will again be prompted to quit, but with no delay option
![Enforcement Due](https://github.com/kandji-inc/support/assets/27963671/8c4496ae-1c82-4297-a5c2-f0dc616c4f39)

> [!CAUTION]
> `audit_app_and_version.zsh` immediately installs the custom app if not found on-disk!
>
> Otherwise, waits until deadline to validate installed version matches or exceeds the enforced

#### `self_service`
- With `self_service` enforcement, it is recommended to define a category via command line/map for `ss_category` (accompanying `prod_name`)
  - If not, will fall back to defined `self_service_category` (Default: `Apps`)
- Test workflows can be used with Self Service, but also recommend defining `test_category` (accompanying `test_name`)
  - Otherwise, falls back to `test_self_service_category` (Default: `Utilities`)
    - Default Self Service categories are configurable in `config.json`

[See here](https://docs.iru.com/en/endpoint/library/library-items-profiles/custom-apps-overview) for more information regarding Iru Custom App enforcement

### Custom App Behavior

#### New Custom Apps
- If no value is provided for `custom_app.prod_name` in recipe/override XML, the naming convention will be taken from the `config.json` default

#### Dynamic Lookup
- Iru Packages supports dynamic lookup, used as a fallback if a definitive Custom App cannot be found by name
  - Configurable in `config.json` under `zz_defaults.dynamic_lookup`
- Lack of definitive Custom App includes both matching duplicates (by name) as well as when no matches are found
  - For duplicates by name, if dynamic lookup is disabled, duplicates are posted to Slack with metadata (creation date, etc.)
  - For no matches by name, if dynamic lookup is disabled, Iru Packages will create a new entry if so configured, otherwise exit
- During dynamic lookup, Iru Packages detects all existing Custom Apps and identifies any that are similar by name to the provided installation media (PKG/DMG/ZIP)
  - Of those, the highest version(s) will be detected from the PKG/DMG/ZIP name (given standard formatting NAME-VERSION.pkg)
  - If multiple highest versions are detected (compared via semantic version), the oldest Custom App by last modification is selected for update

> [!CAUTION]
> Dynamic lookup will replace a Custom App's previous package without confirmation!
>
> This may have unintended impact, so recommend first testing with dry run enabled (`-y`)

## Technical Details

<details id="secrets-management">
<summary><strong>Secrets Management</strong></summary>

- Iru Packages supports two keystore options for storing tokens:
  - `environment` variables (`ENV`)
    - During `irupkg-setup`, secret storage in the user's dotfile is determined from the default shell; `UserShell` from `dscl`
    - For `zsh`, `.zshenv` is used; for `bash`, `.bash_profile`; otherwise, `.profile`
    - On Linux/Docker/CI, ENV is always tried as a final fallback. On macOS, ENV is consulted only when `token_keystore.environment` is `true` (or `ENV_KEYSTORE=1` is set, which also promotes ENV to the primary source checked before Keychain)
  - macOS login keychain (for console user)
    - During `irupkg-setup`, keychain source is determined from `/usr/bin/security login-keychain`
    - Running either `irupkg-setup` or `irupkg` may prompt the user to unlock the keychain if locked before continuing

> **Caution:** Recommended use of this tool is on a Privileged Access Workstation/Hardened Device, accessible only to authorized users
>
> Storing secrets on-disk always poses some risk, so ensure proper security measures are in place

</details>

<details id="iru-token-permissions">
<summary><strong>Iru Token Permissions</strong></summary>

Configure your Iru bearer token to include the following scope:

- <ins>**Library**</ins>
  - `Create Custom App`
  - `Upload Custom App`
  - `Update Custom App`
  - `List Custom Apps`
  - `Get Custom App`
- <ins>**Self Service**</ins>
  - `List Self Service Categories`

Instructions for creating an Iru API token [can be found here](https://docs.iru.com/en/endpoint/api/iru-api-overview#iru-api-overview)

</details>

<details id="slack-token-setup">
<summary><strong>Slack Token Setup</strong></summary>

- Instructions for per-channel webhook generation can be [found here](https://api.slack.com/messaging/webhooks)
  - Webhook should be in the form `https://hooks.slack.com/services/XXXXXXXXX/XXXXXXXXXXX/XXXXXXXXXXXXXXXXXXXXXXXX`

</details>

<details id="configjson">
<summary><strong>config.json</strong></summary>

#### Required Keys
| Required Key          | Accepted Values            | Description                                                         | Default |
|-----------------------|----------------------------|---------------------------------------------------------------------|-------|
| `iru.api_url`         | `TENANT.api.[eu.]iru.com` or `TENANT.api.kandji.io` | Valid Iru API URL for API requests (iru.com is the current format; kandji.io is also accepted) |  |
| `iru.token_name`      | *Name of Iru token in keystore* | Name of Iru API token stored in keystore                             |`IRUPKG_TOKEN`|
| `li_enforcement.type`    | `audit_enforce`\|`install_once`\|`self_service`| Default enforcement type if no override specified | `audit_enforce` |
| `slack.enabled`        |`bool`<br />               | Toggle on/off Slack notifications for runtime | `false` |
| `slack.webhook_name`        | *Name of Slack token in keystore* | Token name with value `hooks.slack.com/services` | `SLACK_TOKEN` |
| `token_keystore`      | **`environment:`**`bool`<br />**`keychain:`**`bool` | Keystore source(s) to retrieve tokens (ENV is always tried as a final fallback regardless) | `true` <br /> `false` |
| `use_package_map`      | `bool`                      | Use PKG ID --> Iru mapping from `package_map.json`       | `false` |

#### Optional Keys
| Optional Key          | Accepted Values            | Description                                                         | Default |
|-----------------------|----------------------------|---------------------------------------------------------------------|---------|
| `li_enforcement.delays`  | **`prod:`**`int`<br />**`test:`**`int` | Number of days before app/version enforcement occurs | `5`<br /> `0`
| `zz_defaults.auto_create_app` | `bool`                      | If custom app cannot be found to update, create new         | `true`         |
| `zz_defaults.dry_run` | `bool`                      | Does not modify any Iru Custom Apps; shows instead what would have run | `false`         |
| `zz_defaults.dynamic_lookup`| `bool`                   | If custom app cannot be found to update, dynamically search and select | `false` |
| `zz_defaults.new_app_naming`      | `str`                       | Custom app naming convention if the name isn't otherwise specified   | `APPNAME (irupkg)` |
| `zz_defaults.self_service_category`| `str`                      | Self Service Category for `prod_name` if not otherwise specified          | `Apps` |
| `zz_defaults.test_self_service_category` | `str`               | Self Service Category for `test_name` if not otherwise specified     | `Utilities` |
| `zz_defaults.unzip_location` | `str`                             | Unzip destination path for ZIP custom apps                           | `/Applications` |

#### Example config.json
```json
{
  "iru" : {
    "api_url" : "TENANT.api.iru.com",
    "token_name" : "IRUPKG_TOKEN"
  },
  "li_enforcement" : {
    "delays" : {
      "prod" : 5,
      "test" : 0
    },
    "type" : "audit_enforce"
  },
  "slack" : {
    "enabled" : false,
    "webhook_name" : "SLACK_TOKEN"
  },
  "token_keystore" : {
    "environment" : true,
    "keychain" : false
  },
  "use_package_map" : false,
  "zz_defaults" : {
    "auto_create_app" : true,
    "dry_run" : false,
    "dynamic_lookup" : false,
    "new_app_naming" : "APPNAME (irupkg)",
    "self_service_category" : "Apps",
    "test_self_service_category" : "Utilities",
    "unzip_location" : "/Applications"
  }
}
```

</details>

<details id="package_mapjson">
<summary><strong>package_map.json</strong></summary>

#### Example Package Map
```json
{
  "sh.brew.homebrew": {
    "prod_name": "Homebrew",
    "test_name": "Homebrew (Beta Testers)",
    "ss_category": "Productivity",
    "test_category": "Utilities"
  },
  "com.amazon.aws.cli2": {
    "test_name": "Amazon AWS CLI (Devs)"
  },
  "com.cisco.pkg.anyconnect.vpn": {
    "prod_name": "Cisco AnyConnect",
    "test_name": "AnyConnect (Soak Test)"
  },
  "com.microsoft.wdav": {
    "prod_name": "Defender",
    "test_name": "Defender (Soak Test)"
  },
  "com.microsoft.word": {
    "prod_name": "Word",
    "test_name": "Word (Beta Channel)",
    "ss_category": "Apps",
    "test_category": "Apps"
  },
  "org.mozilla.firefox": {
    "prod_name": "Firefox (Browser)",
    "ss_category": "Productivity"
  }
}
```

</details>

<details id="brew_cronjson">
<summary><strong>brew_cron.json</strong></summary>

#### Example Brew Cron Conf
```json
{
  "brew_casks" : [
    "coteditor",
    "canva",
    "firefox",
    "google-chrome",
    "slack",
    "rectangle",
    "obsidian",
    "iterm2",
    "zed"
  ],
  "every_n_hours" : 2
}
```

</details>

<details id="irupkg-flags">
<summary><strong>irupkg Flags</strong></summary>

`irupkg` must be called with one of `-p`/`-b` to specify local PKG/DMG or Homebrew cask name.

`-p`/`-b` may be passed multiple times, so long as no name/category flags are also passed.

See below for full usage guide:

```
usage: irupkg [-h] [-p PATH] [-b CASK] [-u UNZIP_LOCATION] [-n NAME]
              [-t TESTNAME] [-s SSCATEGORY] [-z ZZCATEGORY] [-c] [-d] [-v]
              [-S] [-y]

Iru Packages: standalone tool for programmatic management of Iru Custom Apps

options:
  -h, --help            show this help message and exit
  -p, --pkg PATH        Path to PKG/DMG/ZIP for Iru upload; multiple items
                        can be specified so long as no name/category flags
                        (-n/-t/-s/-z) are passed
  -b, --brew CASK       Homebrew cask name which sources PKG/DMG/ZIP; multiple
                        items can be specified so long as no name/category
                        flags (-n/-t/-s/-z) are passed
  -u, --unzip-location UNZIP_LOCATION
                        Unzip destination path for ZIP custom apps (default:
                        /Applications)
  -n, --name NAME       Name of Iru Custom App to create/update
  -t, --testname TESTNAME
                        Name of Iru Custom App (test) to create/update
  -s, --sscategory SSCATEGORY
                        Iru Self Service category aligned with --name
  -z, --zzcategory ZZCATEGORY
                        Iru Self Service category aligned with --testname
  -c, --create          Creates a new Custom App, even if duplicate entry (by
                        name) already exists
  -d, --debug           Sets logging level to debug with maximum verbosity
  -v, --version         Returns the current version of Iru Packages and exits
  -S, --setup           Interactive config generator; creates config.json and
                        configures token keystore
  -y, --dry             Sets dry run, returning (not executing) changes to
                        stdout as they would have been made in Iru
```

</details>

<details id="irupkg-setup-flags">
<summary><strong>irupkg-setup Flags</strong></summary>

> **Note:** `irupkg-setup` is the zsh wizard that ships with the macOS `.pkg` installer; all flags listed below require it. Linux, container, and `uv tool`/`uvx` users instead invoke `irupkg --setup`, which delegates to `setup.zsh` when one is present (i.e. a prior `.pkg` install) or otherwise runs a Python wizard that handles `config.json` + token keystore setup interactively. The Python wizard does not consume `irupkg-setup` flags -- use the macOS `.pkg` install if you need `-i`/`-m`/`-r`/`-b`/etc.

`irupkg-setup` will run through initial setup to populate required variables if invoked without flags.

See below for full usage guide:

```
Usage: irupkg-setup [-h/--help|-a/--addcask|-b/--brewcron|-c/--config|-i/--idfind|-m/--map|-r/--reset|-u/--uninstall]

Conducts prechecks to ensure all required dependencies are available prior to runtime.
Once confirmed, reads and prompts to populate values in config.json if any are invalid.

Options:
-h, --help                       Show this help message and exit
-a, --addcask                    Prompt to add new cask values to brew_cron.json; write updated values to LaunchAgent and reload (must be paired with -b/--brewcron)
-b, --brewcron                   Prompt to populate brew_cron.json (if missing) or read in existing config; write provided values to LaunchAgent and load
-c, --config                     Configure config.json with required values for runtime (don't store secrets)
-i, --idfind                     Populate to CSV names and ids of provided installer media (accepts .pkg/dmg or dir of .pkgs/dmgs)
-m, --map                        Populate to CSV usable values for package_map.json
-r, --reset                      Prompt to reset/overwrite configurable variables/secrets
-u, --uninstall                  Unload and remove agent from ~/Library/LaunchAgents/com.iru.irupkg.brewcron.plist (must be paired with -b/--brewcron)
```

</details>

<details id="audit-enforcement-examples">
<summary><strong>Audit Enforcement Examples</strong></summary>

> #### App not found
> #### ⚠️ Fails audit/triggers install
```
Last Audit - 04/15/2024 at 1:51:31 PM
• Executing audit script...
• Script exited with non-zero status.
• Script results:
• Checking for 'Google Drive.app' install...
• 'Google Drive.app' not found. Triggering install...
```

> #### App found, version enforcement pending
> #### ✅ Passes audit/skips install
```
Last Audit - 04/15/2024 at 2:02:34 PM
• Executing audit script...
• Script exited with success.
• Script results:
• Checking for 'Google Drive.app' install...
• 'Google Drive.app' installed at '/Applications/Google Drive.app'
• Checking version enforcement...
• Update is due at 2024-04-20 11:49:30 PDT
• Will verify 'Google Drive.app' running at least version '90.0' in 4 days, 23 hours, 46 minutes, 57 seconds
```

> #### App found, version enforcement due
> #### Installed version newer/equal to enforced
> #### ✅ Passes audit/skips install
```
Last Audit - 04/15/2024 at 2:03:21 PM
• Executing audit script...
• Script exited with success.
• Script results:
• Checking for 'Google Drive.app' install...
• 'Google Drive.app' installed at '/Applications/Google Drive.app'
• Checking version enforcement...
• Enforcement was due at 2024-04-15 11:49:30 PDT
• Confirming 'Google Drive.app' version...
• Installed version '90.0' greater than or equal to enforced version '90.0'
```

> #### App found, version enforcement due
> #### Installed version older than required
> #### User requests one hour delay
> #### ✅ Passes audit/skips install
```
Last Audit - 04/15/2024 at 2:04:41 PM
• Executing audit script...
• Script exited with success.
• Script results:
• Checking for 'Google Drive.app' install...
• 'Google Drive.app' installed at '/Applications/Google Drive.app'
• Checking version enforcement...
• Enforcement was due at 2024-04-15 11:49:30 PDT
• Confirming 'Google Drive.app' version...
• Installed version '89.0' less than required version '90.0'
• Detected blocking process: 'Google Drive'
• No enforcement delay found for Google Drive.app
• User clicked Delay
• Writing enforcement delay for Google Drive.app to /Library/Preferences/com.iru.irupkg.enforcement.delay.plist
```

> #### App found, version enforcement due
> #### Installed version older than required
> #### User delay still active
> #### ✅ Passes audit/skips install
```
Last Audit - 04/15/2024 at 2:05:20 PM
• Executing audit script...
• Script exited with success.
• Script results:
• Checking for 'Google Drive.app' install...
• 'Google Drive.app' installed at '/Applications/Google Drive.app'
• Checking version enforcement...
• Enforcement was due at 2024-04-15 11:49:30 PDT
• Confirming 'Google Drive.app' version...
• Installed version '89.0' less than required version '90.0'
• Detected blocking process: 'Google Drive'
• Enforcement delay present for Google Drive.app
• User delay still pending; enforcing version 90.0 for Google Drive.app in 0 hours, 58 minutes, 59 seconds
```
> #### App found, version enforcement due
> #### Installed version older than required
> #### App is closed (regardless of user delay)
> #### ⚠️ Fails audit/triggers install
```
Last Audit - 04/15/2024 at 2:11:31 PM
• Executing audit script...
• Script exited with non-zero status.
• Script results:
• Checking for 'Google Drive.app' install...
• 'Google Drive.app' installed at '/Applications/Google Drive.app'
• Checking version enforcement...
• Enforcement was due at 2024-04-15 11:49:30 PDT
• Confirming 'Google Drive.app' version...
• Installed version '89.0' less than required version '90.0'
• No running process found for 'Google Drive.app'
• Upgrading 'Google Drive.app' to version '90.0'...
```
> #### App found, version enforcement due
> #### Installed version older than required
> #### User delay has expired
> #### ⚠️ Fails audit/triggers install
```
Last Audit - 04/15/2024 at 2:18:05 PM
• Executing audit script...
• Script exited with non-zero status.
• Script results:
• Checking for 'Google Drive.app' install...
• 'Google Drive.app' installed at '/Applications/Google Drive.app'
• Checking version enforcement...
• Enforcement was due at 2024-04-15 11:49:30 PDT
• Confirming 'Google Drive.app' version...
• Installed version '89.0' less than required version '90.0'
• Detected blocking process: 'Google Drive'
• Enforcement delay present for Google Drive.app
• Enforcement delay has expired for Google Drive.app 90.0
• User clicked Quit
• Upgrading 'Google Drive.app' to version '90.0'...
```

</details>
