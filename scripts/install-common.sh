#!/usr/bin/env bash
# Shared installer functions. Sourcing this file does not change the system.
RYUO_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RYUO_PYTHON=/usr/bin/python3
RYUO_VENV=/usr/local/share/ryuo-control/venv
RYUO_DRY_RUN=0
RYUO_FORCE_ATOMIC=0
RYUO_REBOOT_REQUIRED=0
RYUO_FAMILY=auto

ryuo_error() { echo "Error: $*" >&2; return 1; }

ryuo_init() {
    local option
    for option in "$@"; do
        case "$option" in
            --dry-run) RYUO_DRY_RUN=1 ;;
            --atomic) RYUO_FORCE_ATOMIC=1 ;;
            --family=*) RYUO_FAMILY=${option#--family=} ;;
            --help|-h)
                echo 'Usage: sudo bash ./install.sh [--dry-run] [--family=auto|fedora|bazzite|arch|ubuntu|suse]'
                echo '--dry-run: print the plan without changing packages, files or services.'
                echo '--family: inspect/choose a family; real installs still verify host compatibility.'
                echo 'Fedora only: --atomic selects the rpm-ostree workflow (Bazzite).'
                exit 0 ;;
            *) ryuo_error "Unknown option: $option"; return 1 ;;
        esac
    done
    if (( RYUO_DRY_RUN )); then return 0; fi
    [[ $EUID -eq 0 ]] || { ryuo_error "Run: sudo bash ${0##*/}"; return 1; }
    [[ -d /run/systemd/system ]] || { ryuo_error 'Run on the systemd host, outside containers/chroots.'; return 1; }
    [[ ! -e /run/.containerenv && ! -e /.dockerenv ]] || { ryuo_error 'Run on the host, not inside a container.'; return 1; }
    [[ -w /usr/local ]] || { ryuo_error '/usr/local must be writable. This image needs a dedicated host package.'; return 1; }
    umask 022
}

ryuo_require_family() {
    (( RYUO_DRY_RUN )) && return 0
    local ID='' ID_LIKE=''
    source /etc/os-release
    [[ " $ID $ID_LIKE " =~ $1 ]] || { ryuo_error "This installer is for $2 (detected: $ID)."; return 1; }
}

ryuo_run() {
    if (( RYUO_DRY_RUN )); then
        printf 'Would run:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

ryuo_atomic_packages() {
    local package
    local missing=()
    RYUO_REBOOT_REQUIRED=0
    for package in "$@"; do
        if (( RYUO_DRY_RUN )) || ! rpm -q "$package" >/dev/null 2>&1; then missing+=("$package"); fi
    done
    if (( ${#missing[@]} )); then
        ryuo_run rpm-ostree install --idempotent "${missing[@]}"
        RYUO_REBOOT_REQUIRED=1
        if (( RYUO_DRY_RUN )); then
            echo 'Would stage missing dependencies and stop before installing files/services.'
        else
            echo 'Dependencies staged. Reboot, then rerun: sudo bash ./install.sh'
            echo 'Application files and services have not been changed in this phase.'
        fi
    fi
}

ryuo_check_python() {
    (( RYUO_DRY_RUN )) && return 0
    "$RYUO_PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'
}

ryuo_create_venv() {
    local method=${1:-venv}
    ryuo_check_python
    ryuo_run "$RYUO_PYTHON" -m "$method" "$RYUO_VENV"
    RYUO_PYTHON="$RYUO_VENV/bin/python"
    ryuo_run "$RYUO_PYTHON" -m pip install --only-binary=:all: 'PySide6>=6.6,<7' 'hidapi>=0.14,<1'
}

ryuo_check_runtime() {
    if (( RYUO_DRY_RUN )); then
        echo 'Would verify ADB, pkexec, systemd, udev, HID, Qt Multimedia and Spanish translations.'
        return 0
    fi
    ryuo_check_commands
    ryuo_check_python
    "$RYUO_PYTHON" -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from ryuo_hid import load_backend; from PySide6.QtCore import qVersion, QLibraryInfo; from PySide6.QtWidgets import QApplication; from PySide6.QtMultimedia import QMediaPlayer, QVideoSink; load_backend(); assert tuple(map(int, qVersion().split(".")[:2])) >= (6, 6), "Qt 6.6 or newer is required"; assert (Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_es.qm").is_file(), "Qt Spanish translations are missing"' "$RYUO_PROJECT_DIR"
    "$RYUO_PYTHON" "$RYUO_PROJECT_DIR/ryuo-lcd.py" --sensors
}

ryuo_check_commands() {
    local binaries=${1:-/usr/bin} name
    for name in adb pkexec systemctl journalctl udevadm; do
        [[ -x "$binaries/$name" ]] || {
            ryuo_error "Required executable missing: $binaries/$name. Dependency installation is incomplete."
            return 1
        }
    done
    # 'adb version' does not start a server or require an attached/authorized AIO.
    "$binaries/adb" version
    "$binaries/pkexec" --version
    "$binaries/udevadm" --version
}

ryuo_install_files() {
    # Optional staging root is for packaging/tests; no package/service actions.
    local stage=${1:-} name
    for name in ryuo-lcd.py ryuo-gui.py ryuo-control.py; do
        install -D -m 0755 "$RYUO_PROJECT_DIR/$name" "$stage/usr/local/bin/$name"
    done
    for name in ryuo_config.py ryuo_i18n.py ryuo_hid.py; do
        install -D -m 0644 "$RYUO_PROJECT_DIR/$name" "$stage/usr/local/bin/$name"
    done
    printf '#!/bin/sh\nexec "%s" /usr/local/bin/ryuo-gui.py "$@"\n' "$RYUO_PYTHON" > "$stage/usr/local/bin/ryuo-control"
    printf '#!/bin/sh\nexec "%s" /usr/local/bin/ryuo-lcd.py "$@"\n' "$RYUO_PYTHON" > "$stage/usr/local/bin/ryuo-lcd"
    chmod 0755 "$stage/usr/local/bin/ryuo-control" "$stage/usr/local/bin/ryuo-lcd"
    install -m 0755 "$RYUO_PROJECT_DIR/scripts/uninstall.py" "$stage/usr/local/bin/ryuo-uninstall"
    install -D -m 0644 "$RYUO_PROJECT_DIR/ryuo-lcd.service" "$stage/etc/systemd/system/ryuo-lcd.service"
    install -D -m 0644 "$RYUO_PROJECT_DIR/70-ryuo-lcd.rules" "$stage/etc/udev/rules.d/70-ryuo-lcd.rules"
    sed -i "s|^ExecStart=/usr/bin/python3 |ExecStart=$RYUO_PYTHON |" "$stage/etc/systemd/system/ryuo-lcd.service"
    install -D -m 0644 "$RYUO_PROJECT_DIR/ryuo-control.desktop" "$stage/usr/local/share/applications/ryuo-control.desktop"
    install -D -m 0644 "$RYUO_PROJECT_DIR/ryuo-control.svg" "$stage/usr/local/share/icons/hicolor/scalable/apps/ryuo-control.svg"
    install -D -m 0644 "$RYUO_PROJECT_DIR/assets/ryuo-screen.png" "$stage/usr/local/share/ryuo-control/ryuo-screen.png"
    install -m 0644 "$RYUO_PROJECT_DIR/assets/README.md" "$stage/usr/local/share/ryuo-control/ASSET-SOURCE.md"
    # Preserve /etc/ryuo-lcd.json and all service drop-ins.
}

ryuo_finish() {
    ryuo_check_runtime
    if (( RYUO_DRY_RUN )); then
        echo "Would copy application, launcher, assets and service using $RYUO_PYTHON."
        echo 'Would preserve settings/drop-ins and restore SELinux labels if available.'
    else
        ryuo_install_files
        if command -v restorecon >/dev/null 2>&1; then
            restorecon -R /usr/local/share/ryuo-control
            restorecon /usr/local/bin/ryuo-lcd.py /usr/local/bin/ryuo-gui.py \
                /usr/local/bin/ryuo-control.py /usr/local/bin/ryuo-control \
                /usr/local/bin/ryuo-lcd \
                /usr/local/bin/ryuo-uninstall \
                /usr/local/bin/ryuo_config.py /usr/local/bin/ryuo_i18n.py /usr/local/bin/ryuo_hid.py \
                /etc/systemd/system/ryuo-lcd.service \
                /etc/udev/rules.d/70-ryuo-lcd.rules \
                /usr/local/share/applications/ryuo-control.desktop \
                /usr/local/share/icons/hicolor/scalable/apps/ryuo-control.svg
        fi
    fi
    ryuo_run udevadm control --reload-rules
    ryuo_run udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=0b05 --attr-match=idProduct=1c76
    ryuo_run udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=1c75 --attr-match=idProduct=1c76
    ryuo_run systemctl daemon-reload
    ryuo_run systemctl enable ryuo-lcd.service
    ryuo_run systemctl restart ryuo-lcd.service
    ryuo_run sleep 3
    ryuo_run systemctl --no-pager --full status ryuo-lcd.service
    ryuo_run journalctl -u ryuo-lcd.service -n 15 --no-pager
    echo 'Launch RYUO Control from the application menu or run: /usr/local/bin/ryuo-control'
}
