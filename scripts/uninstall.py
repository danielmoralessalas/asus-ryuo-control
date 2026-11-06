#!/usr/bin/python3
"""Remove RYUO Control files without uninstalling shared system packages."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

SERVICE = 'ryuo-lcd.service'
FILES = (
    '/usr/local/bin/ryuo-lcd.py', '/usr/local/bin/ryuo-gui.py',
    '/usr/local/bin/ryuo-control.py', '/usr/local/bin/ryuo_config.py',
    '/usr/local/bin/ryuo_i18n.py', '/usr/local/bin/ryuo_hid.py',
    '/usr/local/bin/ryuo-lcd', '/usr/local/bin/ryuo-control',
    '/etc/systemd/system/ryuo-lcd.service',
    '/etc/systemd/system/multi-user.target.wants/ryuo-lcd.service',
    '/etc/udev/rules.d/70-ryuo-lcd.rules',
    '/usr/local/share/applications/ryuo-control.desktop',
    '/usr/local/share/icons/hicolor/scalable/apps/ryuo-control.svg',
    '/usr/local/bin/ryuo-uninstall',
)
CACHE_STEMS = ('ryuo-lcd', 'ryuo-gui', 'ryuo-control', 'ryuo_config', 'ryuo_i18n', 'ryuo_hid')


class Uninstaller:
    def __init__(self, *, root=Path('/'), dry_run=False, purge=False, runner=subprocess.run):
        self.root = Path(root)
        self.dry_run = dry_run
        self.purge = purge
        self.runner = runner

    def path(self, absolute):
        return self.root / absolute.lstrip('/')

    def run(self, command, **kwargs):
        if self.dry_run:
            print('Would run:', ' '.join(command))
            return None
        return self.runner(command, check=True, text=True, **kwargs)

    def stop_service(self):
        if self.dry_run:
            self.run(['/usr/bin/systemctl', 'disable', '--now', SERVICE])
            return
        probe = self.runner(['/usr/bin/systemctl', 'show', '--property=LoadState,ActiveState', SERVICE],
                            check=False, text=True, capture_output=True)
        states = dict(line.split('=', 1) for line in probe.stdout.splitlines() if '=' in line)
        if states.get('LoadState') == 'not-found' and states.get('ActiveState') in ('inactive', 'failed'):
            return
        if probe.returncode:
            raise RuntimeError(probe.stderr.strip() or 'Cannot query the service; no files were removed.')
        # A cached running unit can outlive a manually removed unit file.
        if states.get('LoadState') == 'not-found':
            self.run(['/usr/bin/systemctl', 'stop', SERVICE])
            return
        # Stop/disable failure is fatal: never remove files from a running service.
        self.run(['/usr/bin/systemctl', 'disable', '--now', SERVICE])

    def remove(self, path, *, directory=False):
        if not path.exists() and not path.is_symlink():
            return
        print(('Would remove: ' if self.dry_run else 'Removing: ') + str(path))
        if self.dry_run:
            return
        if path.is_symlink() or not directory:
            path.unlink()
        else:
            shutil.rmtree(path)

    def execute(self):
        self.stop_service()
        for filename in FILES:
            self.remove(self.path(filename))
        self.remove(self.path('/usr/local/share/ryuo-control'), directory=True)
        cache = self.path('/usr/local/bin/__pycache__')
        if not cache.is_symlink():
            for stem in CACHE_STEMS:
                for path in cache.glob(stem + '.*.pyc'):
                    self.remove(path)
        if self.purge:
            self.remove(self.path('/etc/ryuo-lcd.json'))
            self.remove(self.path('/etc/systemd/system/ryuo-lcd.service.d'), directory=True)
        self.run(['/usr/bin/systemctl', 'daemon-reload'])
        udevadm = shutil.which('udevadm')
        if udevadm:
            self.run([udevadm, 'control', '--reload-rules'])
            for vendor in ('0b05', '1c75'):
                self.run([udevadm, 'trigger', '--action=change', '--subsystem-match=usb',
                          f'--attr-match=idVendor={vendor}', '--attr-match=idProduct=1c76'])
        else:
            print('udevadm not found. Reconnect the AIO or reboot to refresh USB access rules.')
        print('RYUO Control removal ' + ('planned.' if self.dry_run else 'completed.'))
        print('Shared dependencies, per-user preferences/cache, downloaded videos and AIO files are preserved.')
        if not self.purge:
            print('System settings and service overrides are preserved. Use --purge to remove them too.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Show the plan without changing anything')
    parser.add_argument('--purge', action='store_true', help='Also remove system settings and service overrides')
    args = parser.parse_args()
    if not args.dry_run and os.geteuid() != 0:
        parser.error('Run with sudo, or use --dry-run to inspect the plan.')
    try:
        Uninstaller(dry_run=args.dry_run, purge=args.purge).execute()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Uninstall failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
