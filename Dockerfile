# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Runtime deps for PKG/DMG extraction on Linux + Homebrew prerequisites
# Install 7-Zip 26.01 directly from upstream: Debian's 7zip package (22.01) lacks
# the improved DMG support (APM+HFS+, APFS) added in 23.01+.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    file \
    jq \
    libarchive13 \
    xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && ARCH=$(uname -m | sed 's/x86_64/x64/;s/aarch64/arm64/') \
    && curl -fsSLo /tmp/7z.tar.xz "https://www.7-zip.org/a/7z2601-linux-${ARCH}.tar.xz" \
    && tar -xf /tmp/7z.tar.xz -C /usr/local/bin 7zz \
    && ln -sf /usr/local/bin/7zz /usr/local/bin/7z \
    && rm /tmp/7z.tar.xz

# Create irupkg user and prepare linuxbrew directory
RUN useradd -m -u 1000 irupkg && mkdir -p /home/linuxbrew/.linuxbrew && chown -R irupkg:irupkg /home/linuxbrew

# Install Homebrew as irupkg user
USER irupkg
RUN NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

ENV PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:${PATH}"

# Install Python deps separately from source so source changes don't re-install deps
USER root
WORKDIR /irupkg
COPY pyproject.toml uv.lock ./
RUN uv export --no-emit-project --no-dev -q \
    | uv pip install --system --no-cache -r /dev/stdin

# Source comes last -- only busts the final layer
COPY . .
RUN uv pip install --system --no-cache --no-deps ".[linux]" && chmod +x /irupkg/scheduler.sh /irupkg/entrypoint.sh

# Deploy config.json to irupkg's XDG data directory if present in build context
RUN mkdir -p /home/irupkg/.local/share/irupkg && chown -R irupkg:irupkg /home/irupkg/.local
COPY --chown=irupkg:irupkg audit_app_and_version.zsh config.jso[n] brew_cron.jso[n] package_map.jso[n] /home/irupkg/.local/share/irupkg/

# Pin the data dir to the baked path above; without this the tool resolves it via
# platformdirs app name and would fall back to the root-owned WORKDIR copy of the
# audit script. KPKG_LOCAL_DIR is read by code prior to the SYS-2807 env var
# rename, IRUPKG_LOCAL_DIR after it.
ENV KPKG_LOCAL_DIR=/home/irupkg/.local/share/irupkg \
    IRUPKG_LOCAL_DIR=/home/irupkg/.local/share/irupkg

# ---------------------------------------------------------------------------
# Runtime environment variables required (pass via -e or CI secret injection):
#   IRU_API_URL       your Iru tenant API URL  (e.g. tenant.api.iru.com)
#   IRUPKG_TOKEN      your Iru API token value
#                     (rename via IRUPKG_TOKEN_NAME if desired)
#
# Optional:
#   SLACK_TOKEN       Slack webhook URL; when set, Slack notifications are
#                     auto-enabled without needing "enabled: true" in config.json
#                     (rename via webhook_name in config.json if desired)
#
# Note: KANDJI_API_URL and KANDJI_TOKEN are still accepted but emit a
#       deprecation warning. Prefer IRU_API_URL and IRUPKG_TOKEN.
#
# Example:
#   docker run --rm \
#     -e IRU_API_URL=tenant.api.iru.com \
#     -e IRUPKG_TOKEN=$MY_SECRET \
#     -v "$(pwd)":/pkgs \
#     irupkg -b slack
# ---------------------------------------------------------------------------

# Run as irupkg so Homebrew (installed as irupkg) works at runtime
USER irupkg

ENTRYPOINT ["/irupkg/entrypoint.sh"]
