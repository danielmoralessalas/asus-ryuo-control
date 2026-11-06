#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$project_dir/scripts/install-common.sh"
source "$project_dir/scripts/install-platforms.sh"
# Argument parsing selects --qt6 (default) or --gtk4 before planning packages.
ryuo_init "$@"
ryuo_select_family
case "$RYUO_FAMILY" in
    fedora|bazzite) ryuo_platform_fedora ;;
    arch) ryuo_platform_arch ;;
    ubuntu) ryuo_platform_ubuntu ;;
    suse) ryuo_platform_suse ;;
esac
if (( RYUO_REBOOT_REQUIRED )); then exit 0; fi
ryuo_finish
