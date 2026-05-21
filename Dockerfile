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

# Create kpkg user and prepare linuxbrew directory
RUN useradd -m -u 1000 kpkg && mkdir -p /home/linuxbrew/.linuxbrew && chown -R kpkg:kpkg /home/linuxbrew

# Install Homebrew as kpkg user
USER kpkg
RUN NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

ENV PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:${PATH}"

# Install Python deps separately from source so source changes don't re-install deps
USER root
WORKDIR /kpkg
COPY pyproject.toml uv.lock ./
RUN uv export --no-emit-project --no-dev -q \
    | uv pip install --system --no-cache -r /dev/stdin

# Source comes last — only busts the final layer
COPY . .
RUN uv pip install --system --no-cache --no-deps ".[linux]" && chmod +x /kpkg/scheduler.sh /kpkg/entrypoint.sh

# Deploy config.json to kpkg's XDG data directory if present in build context
RUN mkdir -p /home/kpkg/.local/share/kpkg && chown -R kpkg:kpkg /home/kpkg/.local
COPY --chown=kpkg:kpkg audit_app_and_version.zsh config.jso[n] brew_cron.jso[n] package_map.jso[n] /home/kpkg/.local/share/kpkg/

# ---------------------------------------------------------------------------
# Runtime environment variables required (pass via -e or CI secret injection):
#   KANDJI_API_URL    your Kandji tenant API URL  (e.g. tenant.api.kandji.io)
#   KANDJI_TOKEN      your Kandji API token value
#                     (rename via KANDJI_TOKEN_NAME if desired)
#
# Optional:
#   SLACK_TOKEN       Slack webhook URL; when set, Slack notifications are
#                     auto-enabled without needing "enabled: true" in config.json
#                     (rename via webhook_name in config.json if desired)
#
# Example:
#   docker run --rm \
#     -e KANDJI_API_URL=tenant.api.kandji.io \
#     -e KANDJI_TOKEN=$MY_SECRET \
#     -v "$(pwd)":/pkgs \
#     kpkg -b slack
# ---------------------------------------------------------------------------

# Run as kpkg so Homebrew (installed as kpkg) works at runtime
USER kpkg

ENTRYPOINT ["/kpkg/entrypoint.sh"]
