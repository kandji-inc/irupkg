#!/bin/zsh
# Created 03/14/24; NRJA
# Updated 04/22/26; NRJA
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

##############################
########## VARIABLES #########
##############################

dir=$(dirname ${ZSH_ARGZERO})
version=$(cat "${dir}/VERSION")
identifier="com.iru.irupkg"
tmp_dir=$(mktemp -d)
payload_dir="${tmp_dir}/Payload/tmp"
scripts_dir="${tmp_dir}/Scripts"

##############################
########## FUNCTIONS #########
##############################

##############################################
# Builds a wheel via uv build and copies it
# to the payload staging area
##############################################
function build_wheel() {
    echo "Building irupkg wheel..."
    if ! build_output=$(uv build --wheel --out-dir "${tmp_dir}" "${dir}" 2>&1); then
        echo "ERROR: uv build failed"
        echo "${build_output}"
        exit 1
    fi
    wheel_path=$(/usr/bin/sed -n -e 's/^.*Successfully built //p' <<< "${build_output}")
    # Verify the wheel was produced
    if [[ ! -f ${~wheel_path} ]]; then
        echo "ERROR: wheel not found after uv build" >&2
        exit 1
    fi
    echo "Wheel built: ${wheel_path}"
}

##############################################
# Writes the postinstall script that:
#  - ensures uv is available
#  - installs irupkg via uv tool install
#  - copies config templates to data dir
#  - adds ~/.local/bin to PATH if absent
##############################################
function write_postinstall() {
    mkdir -p "${scripts_dir}"
    /bin/cat > "${scripts_dir}/postinstall" <<"EOF"
#!/bin/zsh

user=$(stat -f%Su /dev/console)
user_id=$(stat -f%Du /dev/console)
user_dir=$(dscl /Local/Default -read "/Users/${user}" NFSHomeDirectory | /usr/bin/cut -d ":" -f2 | /usr/bin/xargs)
data_dir="${user_dir}/Library/IruPackages"
function run_as_user() { sudo launchctl asuser "${user_id}" sudo -u "${user}" -H "${@}" }

# Ensure uv is installed for the target user
if ! uv_bin=$(run_as_user command -v uv); then
    echo "Installing uv for ${user}..."
    run_as_user curl -LsSf https://astral.sh/uv/install.sh | run_as_user /bin/sh
    source ${user_dir}/.local/bin/env
    uv_bin=$(run_as_user command -v uv)
fi

# Install irupkg wheel via uv tool install
wheel_path=$(find /private/tmp -iname "irupkg-*.whl" 2>/dev/null | head -1)
if [[ -z ${wheel_path} ]]; then
    echo "ERROR: irupkg wheel not found in /tmp"
    exit 1
fi
run_as_user "${uv_bin}" tool install --force --reinstall "${wheel_path}"
run_as_user "${uv_bin}" tool update-shell
rm -f "${wheel_path}"

# Migrate from legacy KandjiPackages install before creating the new data dir
if [[ -d "${user_dir}/Library/KandjiPackages" && ! -d "${user_dir}/Library/IruPackages" ]]; then
    run_as_user mv "${user_dir}/Library/KandjiPackages" "${user_dir}/Library/IruPackages"
elif [[ -d "${user_dir}/Library/KandjiPackages" && -d "${user_dir}/Library/IruPackages" ]]; then
    if run_as_user rsync -a --ignore-existing "${user_dir}/Library/KandjiPackages/" "${user_dir}/Library/IruPackages/"; then
        rm -rf "${user_dir}/Library/KandjiPackages"
    fi
fi

# Copy config templates to data directory (skip if already present)
mkdir -p "${data_dir}"
for f in package_map.json config.json audit_app_and_version.zsh brew_cron.json; do
    [[ -f "/tmp/${f}" ]] && mv -n "/tmp/${f}" "${data_dir}/"
done
mv -f "/tmp/setup.zsh" "${data_dir}/"
for f in package_map.json config.json audit_app_and_version.zsh brew_cron.json; do
    rm -f "/tmp/${f}"
done
chown -R "${user}" "${data_dir}"

# Symlink irupkg-setup into ~/.local/bin; keep kpkg-setup as deprecated alias
run_as_user mkdir -p "${user_dir}/.local/bin"
run_as_user ln -sf "${data_dir}/setup.zsh" "${user_dir}/.local/bin/irupkg-setup"
run_as_user ln -sf "${data_dir}/setup.zsh" "${user_dir}/.local/bin/kpkg-setup"
legacy_plist="${user_dir}/Library/LaunchAgents/io.kandji.kpkg.brewcron.plist"
if [[ -f "${legacy_plist}" ]]; then
    run_as_user launchctl bootout "gui/${user_id}/io.kandji.kpkg.brewcron" 2>/dev/null
    rm -f "${legacy_plist}"
fi

if [[ -f "${data_dir}/kpkg" ]]; then
    echo "Removing legacy kpkg binary from ${data_dir}..."
    rm -f -R "${data_dir}/kpkg" "${data_dir}/.kpkg_py_framework"
fi
# Remove legacy kpkg/kpkg-setup symlinks from /usr/local/bin (now installed to ~/.local/bin)
rm -f "/usr/local/bin/kpkg" "/usr/local/bin/kpkg-setup"

exit 0
EOF
    chmod a+x "${scripts_dir}/postinstall"
}

##############################################
# Stages the wheel and config templates into
# the Payload and builds the .pkg
##############################################
function build_pkg() {
    mkdir -p "${payload_dir}"
    write_postinstall

    # Stage wheel
    cp ${~wheel_path} "${payload_dir}/"

    # Stage config templates
    for f in setup.zsh package_map.json config.json audit_app_and_version.zsh brew_cron.json; do
        [[ -f "${dir}/${f}" ]] && cp "${dir}/${f}" "${payload_dir}/"
    done

    echo "Creating irupkg-${version}.pkg"
    /usr/bin/pkgbuild \
        --quiet \
        --root "${tmp_dir}/Payload" \
        --scripts "${scripts_dir}" \
        --identifier "${identifier}" \
        --version "${version}" \
        "${dir}/irupkg-${version}.pkg"
    echo "Successfully built ${dir}/irupkg-${version}.pkg"
}

##############################################
# Removes the temp directory used during build
##############################################
function cleanup() {
    rm -rf "${tmp_dir}"
}

##############################################
# Main: build wheel, package, clean up
##############################################
function main() {
    if ! [[ $(uname) == "Darwin" ]]; then
        echo "ERROR: This build script is only supported on macOS."
        exit 1
    fi
    pushd "${dir}" || exit
    build_wheel
    build_pkg
    cleanup
    popd || exit
}

###############
##### MAIN ####
###############
main
