#!/bin/zsh -f
#
# Audit script used to enforce Iru custom app; programmatically populated during irupkg runtime
# Searches for install via populated bundle identifier/app name or from receipt DB given package ID
# If not found, immediately triggers custom app installation (regardless of any configured delays)
# If found, determines if version enforcement due; if so, triggers install if installed < required
# If app is open in foreground, prompts user to close app with one-time option to defer one hour

##############################
########## VARIABLES #########
##############################

# Set to 0 for immediate enforcement
DAYS_UNTIL_ENFORCEMENT=3

# Programmatically populated during runtime
APP_NAME=""
BUNDLE_ID=""
PKG_ID=""
MINIMUM_ENFORCED_VERSION=""
CREATION_TIMESTAMP=""

###############################################################
##################### DO NOT MODIFY BELOW #####################
###############################################################

# Math
NOW_TIMESTAMP=$(date +%s)
# Determine TS when enforcement is due
ENFORCEMENT_TIMESTAMP=$((${CREATION_TIMESTAMP}+(${DAYS_UNTIL_ENFORCEMENT}*24*60*60)))
# Enforcement due datetime
ENFORCEMENT_DATETIME=$(date -r ${ENFORCEMENT_TIMESTAMP} +'%Y-%m-%d %H:%M:%S %Z')
# Enforcement delay plist
ENFORCEMENT_DELAY_PLIST="/Library/Preferences/com.iru.irupkg.enforcement.delay.plist"

##############################
########## FUNCTIONS #########
##############################

##############################################
# Checks for defined BUNDLE_ID and attempts to
# locate using mdfind; failing that, searches
# using find in common dirs for Info.plist
# with matching CFBundleIdentifier
# Globals:
#   BUNDLE_ID
# Assigns:
#   installed_path
##############################################
function find_app_by_bid() {
    # Attempt location of app bundle by BID if defined
    if [[ -n "${BUNDLE_ID}" ]]; then
        # Sort to bring shortest path (if multiple) to top and select
        installed_path=$(mdfind "kMDItemCFBundleIdentifier == '${BUNDLE_ID}'" | sort | head -1)
        if [[ -z ${installed_path} ]]; then
            # Search typical paths for app bundle dir structures, match on BID from Info.plists and print matching app (if any)
            info_plist_path=$(find /Applications /System/Applications /Library/ -maxdepth 7 -path "*\.app/Contents/Info.plist" -print0 -exec /usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "{}" \; 2>/dev/null | grep -a "${BUNDLE_ID}" | sed -n "s/${BUNDLE_ID}$//p")
            # Shell built-in to lop off two sub dirs
            installed_path=${info_plist_path%/*/*}
        fi
    fi
}

##############################################
# If installed_path is undefined and APP_NAME
# is valid (ends in .app), attempt to locate
# app using mdfind; failing that, searches
# using find in common dirs for matching app
# Globals:
#   APP_NAME
#   installed_path
# Assigns:
#   installed_path
##############################################
function find_app_by_name() {
    # If we couldn't find an install path from BID, validate and check against APP_NAME
    if ! grep -q '\.app$' <<< ${installed_path} && grep -q '\.app$' <<< "${APP_NAME}"; then
        installed_path=$(mdfind "kMDItemFSName == '${APP_NAME}'" | sort | head -1)
        if [[ -z ${installed_path} ]]; then
            installed_path="$(find /Applications /System/Applications /Library/ -maxdepth 5 -name ${APP_NAME} 2>/dev/null)"
        fi
    fi
}

##############################################
# Checks for installed app by installed_path
# If missing, triggers install with exit 1
# If present, checks/assigns info_plist_path
# and assigns CFBundleShortVersionString value
# from Info.plist as installed_version
# Globals:
#   IDENTIFIER
#   info_plist_path
#   installed_path
# Assigns:
#   info_plist_path
#   installed_version
# Returns:
#   Exit 1 if app install missing
##############################################
function validate_install() {
    echo "Checking for '${IDENTIFIER}' install..."
    # Confirm installed_path assignment
    if [[ ! -d ${installed_path} ]]; then
        echo "'${IDENTIFIER}' not found. Triggering install..."
        exit 1
    else
        # Assign if not already set
        info_plist_path="${info_plist_path:-${installed_path}/Contents/Info.plist}"
        echo "'${IDENTIFIER}' installed at '${installed_path}'"
        # Get/assign installed version
        installed_version=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "${info_plist_path}" 2>/dev/null)
    fi
}

##############################################
# Checks for installed app by PKG_ID receipt
# If missing, triggers install with exit 1
# If present, checks pkg_info_plist and
# and assigns pkg-version value from receipt
# as installed_pkg_version
# Globals:
#   PKG_ID
# Assigns:
#   pkg_info_plist
#   installed_pkg_version
# Returns:
#   Exit 1 if app install missing
##############################################
function validate_pkginfo() {
    echo "Checking for '${PKG_ID}' receipt..."
    # Confirm installed_path assignment
    pkg_info_plist=$(pkgutil --pkg-info-plist ${PKG_ID} 2>/dev/null)
    if [[ -z ${pkg_info_plist} ]]; then
        echo "No PKG receipt found for '${PKG_ID}'. Triggering install..."
        exit 1
    else
        echo "PKG receipt found for '${PKG_ID}'"
        # Get/assign installed version
        installed_pkg_version=$(plutil -extract pkg-version raw -o - - <<< ${pkg_info_plist})
    fi
}

##############################################
# Checks if version enforcement is due and if
# so, continues to validate installed version
# Globals:
#   ENFORCEMENT_TIMESTAMP
#   IDENTIFIER
#   MINIMUM_ENFORCED_VERSION
#   NOW_TIMESTAMP
# Returns:
#   Exit 0 if enforcement not yet due
##############################################
function enforcement_check() {
    echo "Checking version enforcement..."
    if [[ ${NOW_TIMESTAMP} -lt ${ENFORCEMENT_TIMESTAMP} ]]; then
        time_remaining=$(awk '{printf "%d days, %d hours, %02d minutes, %02d seconds", $1/(3600*24), ($1/3600)%24, ($1/60)%60, $1%60}' <<< $(expr ${ENFORCEMENT_TIMESTAMP} - ${NOW_TIMESTAMP}))
        echo "Update is due at ${ENFORCEMENT_DATETIME}"
        echo "Will verify '${IDENTIFIER}' running at least version '${MINIMUM_ENFORCED_VERSION}' in ${time_remaining}"
        exit 0
    else
        echo "Enforcement was due at ${ENFORCEMENT_DATETIME}"
        echo "Confirming '${IDENTIFIER}' version..."
    fi
}

##############################################
# Checks for running app by installed_path or
# BUNDLE_ID and assigns lsappinfo values
# Checks if app is running in foreground and
# prompts user to close if so
# Globals:
#  BUNDLE_ID
#  ENFORCEMENT_DELAY_PLIST
#  IDENTIFIER
#  MINIMUM_ENFORCED_VERSION
#  installed_path
# Assigns:
#  as_formatted_path
#  ls_display_name
# Returns:
#   0 if no running app found or app is closed
##############################################
function check_blocking_proc() {

    # Locate running app by BID or APP_NAME
    if [[ -n ${installed_path} ]]; then
        app_open_check=$(lsappinfo info $(lsappinfo find bundlepath="${installed_path}") -only CFBundleName -only LSDisplayName -only BundlePath -only ApplicationType -only CFBundleIconFile)
    elif [[ -n ${BUNDLE_ID} ]]; then
        app_open_check=$(lsappinfo info $(lsappinfo find bundleid="${BUNDLE_ID}") -only CFBundleName -only LSDisplayName -only BundlePath -only ApplicationType -only CFBundleIconFile)
    else
        # No app or bundle ID defined likely means bg proc
        return 0
    fi

    if [[ -z ${app_open_check} ]]; then
        echo "No running process found for '${IDENTIFIER}'"
        if [[ -f "${ENFORCEMENT_DELAY_PLIST}" ]]; then
            # Remove enforcement delay for version if previously set
            /usr/libexec/PlistBuddy -c "Delete :'${IDENTIFIER}':'${MINIMUM_ENFORCED_VERSION}'" "${ENFORCEMENT_DELAY_PLIST}" 2>/dev/null
        fi
        return 0
    fi

    # If open, assign values from lsappinfo
    ls_bundle_name=$(grep "CFBundleName" <<< "${app_open_check}" | cut -d '"' -f4)
    ls_display_name=$(grep "LSDisplayName" <<< "${app_open_check}" | cut -d '"' -f4)
    ls_bundle_path=$(grep "LSBundlePath" <<< "${app_open_check}" | cut -d '"' -f4)
    ls_app_type=$(grep "ApplicationType" <<< "${app_open_check}" | cut -d '"' -f4)
    ls_icon_file=$(grep "CFBundleIconFile" <<< "${app_open_check}" | cut -d '"' -f4)
    # Append .icns if not present
    [[ ${ls_icon_file} == *.icns ]] || ls_icon_file+=".icns"

    # Locate app icon and assign backups if not found
    icon_path=$(find "${ls_bundle_path}" -depth 3 -name "${ls_icon_file}" | head -1)
    if [[ -z ${icon_path} ]]; then
        # Use Iru Self Service icon as first fallback
        icon_path=$(find "/Applications/Iru Self Service.app" -depth 3 -name "AppIcon.icns" | head -1)
        # If not found, use Installer icon as final fallback
        icon_path=${icon_path:-"/System/Library/CoreServices/Installer.app/Contents/Resources/AppIcon.icns"}
    fi
    # Format for osascript
    as_formatted_path=$(sed 's/\//:/g; s/://' <<< "${icon_path}")

    # If app is running in fg, prompt user to close
    if [[ ${ls_app_type} == "Foreground" ]]; then
        echo "Detected blocking process: '${ls_display_name}'"
        prompt_close_app
    fi
    return 0
}

##############################################
# Prompts user to close app blocking update
# Allows for single one hour deferral if opted
# If user quits, closes app and returns 0 to
# continue installation; if deferral, writes
# one hour delay to plist and exits 0 to skip
# custom app enforcement during runtime
# Globals:
#   as_formatted_path
#   ls_display_name
# Returns:
#   0 if user quits app
#   Exit 0 if prompt times out or is deferred
#   Exit 1 if unexpected return code received
##############################################
function prompt_close_app() {
    # Capitalize display name if not already
    dialog_title="Close '${(C)ls_display_name}' to Update"
    dialog_prompt="Click 'Quit' to exit '${ls_display_name}' and finish updating."
    # Check if delay already set or elapsed
    check_delay
    case $? in
        0)
            exit 0
            ;;
        1)
            buttons='{"Quit"} default button 1'
            ;;
        2)
            buttons='{"Delay 1 Hour", "Quit"} cancel button 1 default button 2'
            dialog_prompt+="\n\nYou may delay for one hour."
            ;;
        *)
            echo "Unexpected return code received"
            exit 1
            ;;
    esac

    applescript_out=$(osascript 2>&1 <<EOF
    tell application "System Events"
        display dialog "${dialog_prompt}" \
        with title "${dialog_title}" \
        with text buttons ${buttons} \
        with icon file "${as_formatted_path}" \
        giving up after 300
    end tell
EOF
    )
    exitc=$?

    if grep -q "got an error: Application" <<< ${applescript_out}; then
        sleep 1
        echo "AppleScript prompt error; retrying..."
        prompt_close_app
        return
    fi
    if grep -q "gave up:true" <<< ${applescript_out}; then
        echo "App quit prompt timed out..."
        echo "Will prompt again during next check-in"
        exit 0
    fi
    if [[ ${exitc} -eq 0 ]]; then
        echo "User clicked Quit"
        osascript -e 'quit app "'"${ls_bundle_name}"'"'
        # Sleep in case something needs saving
        sleep 5
        return 0
    else
        echo "User clicked Delay"
        add_delay
        exit 0
    fi
}

##############################################
# Checks for prior user enforcement delay
# If found, validates expiry and returns code
# based on status of user delay
# Globals:
#   ENFORCEMENT_DELAY_PLIST
#   IDENTIFIER
#   MINIMUM_ENFORCED_VERSION
#   NOW_TIMESTAMP
# Returns:
#   0 if user delay still pending
#   1 if enforcement delay has expired
#   2 if no enforcement delay found
##############################################
function check_delay() {

    if /usr/libexec/PlistBuddy -c "Print :'${IDENTIFIER}'" "${ENFORCEMENT_DELAY_PLIST}" >/dev/null 2>&1; then
        echo "Enforcement delay present for ${IDENTIFIER}"
        delay_ts_check=$(/usr/libexec/PlistBuddy -c "Print :'${IDENTIFIER}':'${MINIMUM_ENFORCED_VERSION}'" "${ENFORCEMENT_DELAY_PLIST}" 2>/dev/null)
        if [[ -z ${delay_ts_check} ]]; then
            echo "No enforcement delay found for ${IDENTIFIER} ${MINIMUM_ENFORCED_VERSION}"
            return 2
        elif [[ ${NOW_TIMESTAMP} -ge ${delay_ts_check} ]]; then
            echo "Enforcement delay has expired for ${IDENTIFIER} ${MINIMUM_ENFORCED_VERSION}"
            return 1
        else
            time_remaining=$(awk '{printf "%d hours, %02d minutes, %02d seconds", ($1/3600)%24, ($1/60)%60, $1%60}' <<< $(expr ${delay_ts_check} - ${NOW_TIMESTAMP}))
            echo "User delay still pending; enforcing version ${MINIMUM_ENFORCED_VERSION} for ${IDENTIFIER} in ${time_remaining}"
            return 0
        fi
    else
        echo "No enforcement delay found for ${IDENTIFIER}"
        return 2
    fi
}

##############################################
# Adds enforcement delay for app and version
# to plist at ENFORCEMENT_DELAY_PLIST
# Globals:
#   ENFORCEMENT_DELAY_PLIST
#   IDENTIFIER
#   MINIMUM_ENFORCED_VERSION
#   NOW_TIMESTAMP
# Outputs:
#   Writes delay to ENFORCEMENT_DELAY_PLIST
##############################################
function add_delay() {

    if /usr/libexec/PlistBuddy -c "Print :'${IDENTIFIER}'" "${ENFORCEMENT_DELAY_PLIST}" >/dev/null 2>&1; then
        echo "Enforcement delay present for ${IDENTIFIER}"
    else
        echo "Writing enforcement delay for ${IDENTIFIER} to ${ENFORCEMENT_DELAY_PLIST}"
        /usr/libexec/PlistBuddy -c "Add :'${IDENTIFIER}' dict" "${ENFORCEMENT_DELAY_PLIST}"
    fi

    hour_delay_ts=$(( ${NOW_TIMESTAMP} + 3600 ))

    /usr/libexec/PlistBuddy -c "Add :'${IDENTIFIER}':'${MINIMUM_ENFORCED_VERSION}' integer '${hour_delay_ts}'" "${ENFORCEMENT_DELAY_PLIST}"
}

##############################################
# Checks for defined MINIMUM_ENFORCED_VERSION
# and compares to installed version or pkgvers
# If version is less than enforced, triggers
# check for blocking proc + user delay
# Once blocker closed, exits 1 to trigger
# If version greater than/equal to enforced,
# removes delay from plist and exits 0
# Globals:
#   ENFORCEMENT_DELAY_PLIST
#   MINIMUM_ENFORCED_VERSION
#   IDENTIFIER
#   PKG_ID
#   PKG_ID
#   info_plist_path
#   installed_version
#   installed_pkg_version
#   pkg_info_plist
# Outputs:
#   Removes delay from ENFORCEMENT_DELAY_PLIST
# Returns:
#   Exit 0 if version compliant
#   Exit 1 if version less than enforced
##############################################
function validate_version() {
    # Confirm minimum enforced version is set
    if [[ -z ${MINIMUM_ENFORCED_VERSION} ]]; then
        echo "No minimum version defined — exiting"
        exit 0
    elif [[ -n ${PKG_ID} ]]; then
        if [[ -z ${installed_pkg_version} ]]; then
            echo "WARNING: Current PKG install could not be determined! Tried parsing '${pkg_info_plist}'"
            echo "Attempting reinstall of '${PKG_ID}'..."
            exit 1
        fi
        installed_version=${installed_pkg_version}
    elif [[ -z ${installed_version} ]]; then
        echo "WARNING: Current app version could not be determined! Tried parsing '${info_plist_path}'"
        echo "Attempting reinstall of '${installed_path}'..."
        exit 1
    fi

    # Compare minimum enforced version to installed version via zsh builtin is-at-least
    autoload is-at-least
    version_check=$(is-at-least "${MINIMUM_ENFORCED_VERSION}" "${installed_version}" && echo "greater than or equal to" || echo "less than")

    if [[ ${version_check} == *"less"* ]]; then
        echo "Installed version '${installed_version}' ${version_check} enforced version '${MINIMUM_ENFORCED_VERSION}'"
        # Check if app is running in fg
        check_blocking_proc
        echo "Upgrading '${IDENTIFIER}' to version '${MINIMUM_ENFORCED_VERSION}'..."
        exit 1
    else
        echo "Installed version '${installed_version}' ${version_check} enforced version '${MINIMUM_ENFORCED_VERSION}'"
        if [[ -f "${ENFORCEMENT_DELAY_PLIST}" ]]; then
            # Remove enforcement delay for version if previously set
            /usr/libexec/PlistBuddy -c "Delete :'${IDENTIFIER}':'${MINIMUM_ENFORCED_VERSION}'" "${ENFORCEMENT_DELAY_PLIST}" 2>/dev/null
        fi
        exit 0
    fi
}

##############################################
# Main runtime
# Assigns a common identifier for logging
# Validate one of BUNDLE_ID or APP_NAME exists
# If not, attempts to validate defined PKG_ID
# Locates install by installed app or PKG_ID
# Checks if enforcement due, validates version
# Globals:
#   APP_NAME
#   BUNDLE_ID
#   PKG_ID
# Assigns:
#   IDENTIFIER
##############################################
function main() {

    # Set ID for logging to APP_NAME, PKG_ID, or BUNDLE_ID
    # shellcheck disable=SC2299
    IDENTIFIER="${${APP_NAME:-$PKG_ID}:-$BUNDLE_ID}"

    if [[ -z ${BUNDLE_ID} && -z ${APP_NAME} ]]; then
        echo "Neither BUNDLE_ID nor APP_NAME defined"
        if [[ -n ${PKG_ID} ]]; then
            echo "PKG_ID defined; attempting PKG install validation from receipts..."
            validate_pkginfo
        else
            echo "No PKG_ID defined — exiting"
            exit 1
        fi
    else
        find_app_by_bid

        find_app_by_name

        validate_install
    fi

    enforcement_check

    validate_version
}

###############
##### MAIN ####
###############

main
