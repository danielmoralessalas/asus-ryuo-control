#!/usr/bin/env bash
# Platform-specific dependency plans, called only by install.sh.

ryuo_detect_family() {
    local candidate
    local -a inherited=()
    read -r -a inherited <<< "${2:-}"
    # Match ID first, then each ID_LIKE token in the distro's declared order.
    for candidate in "$1" "${inherited[@]}"; do
        case "$candidate" in
            bazzite) echo bazzite; return 0 ;;
            fedora|nobara|ublue|bluefin|aurora) echo fedora; return 0 ;;
            arch|manjaro|endeavouros|garuda|cachyos) echo arch; return 0 ;;
            ubuntu|debian|linuxmint|pop|neon|zorin|elementary) echo ubuntu; return 0 ;;
            opensuse|opensuse-*|suse|sles) echo suse; return 0 ;;
        esac
    done
    ryuo_error "Unsupported distribution: $1 (ID_LIKE=${2:-unset}). No packages were installed."
}

ryuo_select_family() {
    if [[ $RYUO_FAMILY == auto ]]; then
        local ID='' ID_LIKE=''
        source /etc/os-release
        RYUO_FAMILY="$(ryuo_detect_family "$ID" "$ID_LIKE")" || return 1
    fi
    case "$RYUO_FAMILY" in
        fedora|bazzite|arch|ubuntu|suse) ;;
        *) ryuo_error "Unknown family: $RYUO_FAMILY"; return 1 ;;
    esac
    if (( RYUO_FORCE_ATOMIC )) && [[ $RYUO_FAMILY != fedora && $RYUO_FAMILY != bazzite ]]; then
        ryuo_error '--atomic is only valid for Fedora-based distributions.'
        return 1
    fi
    if [[ $RYUO_FAMILY == bazzite ]]; then RYUO_FORCE_ATOMIC=1; fi
    echo "Distribution family: $RYUO_FAMILY"
    echo "Desktop frontend: $RYUO_UI"
}

ryuo_platform_fedora() {
    ryuo_require_family ' (fedora|nobara|bazzite|ublue|bluefin|aurora) ' 'Fedora, Nobara and Fedora Atomic derivatives'
    local packages=(python3-hidapi android-tools polkit)
    if [[ $RYUO_UI == gtk4 ]]; then
        packages+=(python3-gobject python3-cairo gtk4 gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugin-libav)
    else
        packages+=(python3-pyside6 qt6-qtmultimedia qt6-qtwayland qt6-qttranslations)
    fi
    if [[ -e /run/ostree-booted ]] || (( RYUO_FORCE_ATOMIC )); then
        if (( ! RYUO_DRY_RUN )); then
            [[ -e /run/ostree-booted ]] || { ryuo_error '--atomic requires an rpm-ostree host.'; exit 1; }
            command -v rpm-ostree >/dev/null || { ryuo_error 'rpm-ostree is required for this image.'; exit 1; }
        fi
        ryuo_atomic_packages "${packages[@]}"
    else
        ryuo_run dnf install -y "${packages[@]}"
    fi
}

ryuo_platform_arch() {
    ryuo_require_family ' (arch|manjaro|endeavouros|garuda|cachyos) ' 'Arch Linux and systemd-based derivatives'
    # No -Sy: do not refresh the database without upgrading the system.
    local packages=(python python-hidapi android-tools polkit)
    if [[ $RYUO_UI == gtk4 ]]; then
        packages+=(python-gobject python-cairo gtk4 gst-plugins-base gst-plugins-good gst-libav)
    else
        packages+=(pyside6 qt6-multimedia qt6-wayland qt6-translations)
    fi
    ryuo_run pacman -S --needed "${packages[@]}"
}

ryuo_platform_ubuntu() {
    ryuo_require_family ' (ubuntu|debian|linuxmint|pop|neon|zorin|elementary) ' 'Ubuntu, Debian and systemd-based derivatives'
    ryuo_run apt-get update
    if [[ $RYUO_UI == gtk4 ]]; then
        ryuo_run apt-get install -y python3 python3-venv python3-pip adb pkexec \
            libhidapi-hidraw0 libhidapi-libusb0 python3-gi python3-gi-cairo python3-cairo \
            gir1.2-gtk-4.0 gir1.2-graphene-1.0 libgtk-4-media-gstreamer \
            gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav
        ryuo_create_venv
        return
    fi
    ryuo_run apt-get install -y python3 python3-venv python3-pip adb pkexec \
        libhidapi-hidraw0 libhidapi-libusb0 libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 \
        libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
        libxcb-xinerama0 libxcb-xkb1 libx11-xcb1 libdbus-1-3 \
        libwayland-client0 libwayland-cursor0 libwayland-egl1 libxkbcommon0
    # Private wheels also support releases where PySide6 is absent from apt.
    ryuo_create_venv
}

ryuo_platform_suse() {
    ryuo_require_family ' (opensuse|opensuse-tumbleweed|opensuse-leap|opensuse-slowroll|suse|sles) ' 'openSUSE/SUSE and systemd-based derivatives'
    if [[ -e /run/transactional-update ]] || { command -v transactional-update >/dev/null 2>&1 && (( ! RYUO_DRY_RUN )); }; then
        ryuo_error 'Transactional SUSE images (MicroOS/Aeon/Kalpa) require an image-specific deployment; this installer targets mutable hosts.'
        return 1
    fi
    ryuo_run zypper --non-interactive refresh
    if [[ $RYUO_UI == gtk4 ]]; then
        ryuo_run zypper --non-interactive install python3 android-tools polkit \
            libhidapi-hidraw0 libhidapi-libusb0 python3-gobject python3-gobject-Gdk python3-cairo \
            typelib-1_0-Gtk-4_0 typelib-1_0-Graphene-1_0 libgtk-4-1 \
            gstreamer-plugins-base gstreamer-plugins-good
    else
    ryuo_run zypper --non-interactive install python3 android-tools polkit \
        libhidapi-hidraw0 libhidapi-libusb0 libxcb-cursor0 libxkbcommon-x11-0 libEGL1 libGL1 \
        libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
        libxcb-xinerama0 libxcb-xkb1 libX11-xcb1 libdbus-1-3 \
        libwayland-client0 libwayland-cursor0 libwayland-egl1 libxkbcommon0
    fi
    ryuo_check_python
    if (( RYUO_DRY_RUN )); then
        echo 'Would detect the system Python ABI and install its matching pip/virtualenv packages.'
    else
        local python_abi="$(/usr/bin/python3 -c 'import sys; print(f"python{sys.version_info.major}{sys.version_info.minor}")')"
        ryuo_run zypper --non-interactive install "${python_abi}-pip" "${python_abi}-virtualenv"
    fi
    ryuo_create_venv virtualenv
}
