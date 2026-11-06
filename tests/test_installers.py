"""Installer checks without root, package downloads or host service changes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / 'scripts/install-common.sh'
PLATFORMS = ROOT / 'scripts/install-platforms.sh'
FAMILIES = ['fedora', 'bazzite', 'arch', 'ubuntu', 'suse']


class InstallerTests(unittest.TestCase):
    def run_bash(self, code, *arguments, env=None):
        return subprocess.run(['bash', '-euc', code, 'test', *map(str, arguments)],
                              cwd=ROOT, capture_output=True, text=True, check=True, env=env)

    def test_all_installers_have_valid_shell_syntax(self):
        for filename in ['install.sh', 'scripts/install-common.sh',
                         'scripts/install-platforms.sh']:
            with self.subTest(filename=filename):
                subprocess.run(['bash', '-n', str(ROOT / filename)], check=True)

    def test_dry_run_selects_package_manager_without_executing_it(self):
        for family, expected in [('fedora', 'dnf install'),
                                 ('arch', 'pacman -S --needed'),
                                 ('ubuntu', 'apt-get install'),
                                 ('suse', 'zypper --non-interactive install')]:
            with self.subTest(family=family):
                result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--dry-run', f'--family={family}'],
                                        capture_output=True, text=True, check=True)
                self.assertIn(expected, result.stdout)
                self.assertIn('Would copy application', result.stdout)
                self.assertIn('Would run: systemctl restart', result.stdout)
                if family in ('ubuntu', 'suse'):
                    self.assertIn('/usr/local/share/ryuo-control/venv/bin/python', result.stdout)
                    self.assertIn('--only-binary=:all:', result.stdout)

    def test_unified_installer_supports_each_family(self):
        for family, manager in [('fedora', 'dnf install'), ('arch', 'pacman -S'),
                                ('ubuntu', 'apt-get install'), ('suse', 'zypper'),
                                ('bazzite', 'rpm-ostree install')]:
            with self.subTest(family=family):
                result = subprocess.run(['bash', 'install.sh', '--dry-run', f'--family={family}'],
                                        cwd=ROOT, capture_output=True, text=True, check=True)
                self.assertIn(f'Distribution family: {family}', result.stdout)
                self.assertIn(manager, result.stdout)

    def test_gtk4_installs_its_dependencies_without_qt_for_every_family(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                result = subprocess.run(['bash', 'install.sh', '--dry-run', '--gtk4', f'--family={family}'],
                                        cwd=ROOT, capture_output=True, text=True, check=True)
                self.assertIn('Desktop frontend: gtk4', result.stdout)
                self.assertIn('gtk4' if family in ('fedora', 'arch', 'bazzite') else 'Gtk-4_0' if family == 'suse' else 'gtk-4.0', result.stdout)
                self.assertNotIn('pyside6', result.stdout.lower())
                self.assertNotIn('qt6-', result.stdout)
                self.assertIn('adb' if family == 'ubuntu' else 'android-tools', result.stdout)
                if family in ('ubuntu', 'suse'):
                    self.assertIn('--system-site-packages', result.stdout)
                    self.assertIn('hidapi', result.stdout)
                if family == 'bazzite':
                    self.assertNotIn('Would copy application', result.stdout)

    def test_frontend_flags_are_mutually_exclusive(self):
        for args in [('--qt6', '--gtk4'), ('--gtk4', '--qt6')]:
            result = subprocess.run(['bash', 'install.sh', '--dry-run', *args], cwd=ROOT,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Choose only one frontend', result.stderr)
            self.assertNotIn('Would run:', result.stdout)

    def test_staged_switch_to_gtk_preserves_settings_and_changes_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            settings = stage / 'etc/ryuo-lcd.json'
            settings.parent.mkdir(parents=True)
            settings.write_text('existing settings')
            self.run_bash('source "$1"; ryuo_install_files "$2"; RYUO_UI=gtk4; ryuo_install_files "$2"', COMMON, stage)
            self.assertIn('/usr/local/bin/ryuo-gtk.py', (stage / 'usr/local/bin/ryuo-control').read_text())
            self.assertTrue((stage / 'usr/local/bin/ryuo-gtk.py').exists())
            self.assertTrue((stage / 'usr/local/bin/ryuo_desktop.py').exists())
            self.assertEqual(settings.read_text(), 'existing settings')

    def test_detects_distribution_and_ordered_ancestry(self):
        for distro, ancestry, expected in [
                ('fedora', '', 'fedora'), ('nobara', 'fedora', 'fedora'),
                ('bazzite', 'fedora', 'bazzite'), ('manjaro', 'arch', 'arch'),
                ('linuxmint', 'ubuntu debian', 'ubuntu'),
                ('opensuse-tumbleweed', 'suse', 'suse'),
                ('custom', 'unknown arch fedora', 'arch'),
                ('custom', '* ubuntu', 'ubuntu')]:
            with self.subTest(distro=distro, ancestry=ancestry):
                result = self.run_bash('source "$1"\nsource "$2"\nryuo_detect_family "$3" "$4"',
                                       COMMON, PLATFORMS, distro, ancestry)
                self.assertEqual(result.stdout.strip(), expected)

    def test_unsupported_distribution_fails_without_package_operations(self):
        result = subprocess.run(['bash', '-euc',
                                 'source "$1"; source "$2"; ryuo_detect_family unknown unknown',
                                 'test', str(COMMON), str(PLATFORMS)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported distribution', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_invalid_family_or_atomic_combination_fails_before_operations(self):
        for args in [('--family=unknown',), ('--family=arch', '--atomic')]:
            with self.subTest(args=args):
                result = subprocess.run(['bash', 'install.sh', '--dry-run', *args],
                                        cwd=ROOT, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Would run:', result.stdout)

    def test_automatic_dry_run_selects_host_family(self):
        expected = self.run_bash('source "$1"; source "$2"; source /etc/os-release; '
                                 'ryuo_detect_family "$ID" "${ID_LIKE:-}"', COMMON, PLATFORMS)
        result = subprocess.run(['bash', 'install.sh', '--dry-run'], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn(f'Distribution family: {expected.stdout.strip()}', result.stdout)

    def test_bazzite_dry_run_stops_before_service_and_file_installation(self):
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--dry-run', '--family=bazzite'],
                                capture_output=True, text=True, check=True)
        self.assertIn('rpm-ostree install --idempotent', result.stdout)
        self.assertNotIn('dnf install', result.stdout)
        self.assertNotIn('systemctl', result.stdout)
        self.assertNotIn('Would copy application', result.stdout)

    def test_every_family_installs_adb_and_authentication(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--dry-run', f'--family={family}'],
                                        capture_output=True, text=True, check=True)
                self.assertIn('adb' if family == 'ubuntu' else 'android-tools', result.stdout)
                self.assertIn('pkexec' if family == 'ubuntu' else 'polkit', result.stdout)
                if family != 'bazzite':
                    self.assertIn('Would verify ADB', result.stdout)

    def test_runtime_tools_are_checked_and_missing_adb_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('adb', 'pkexec', 'systemctl', 'journalctl', 'udevadm'):
                path = root / name
                path.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
                path.chmod(0o755)
            result = self.run_bash('source "$1"\nryuo_check_commands "$2"', COMMON, root)
            self.assertIn('version', result.stdout)
            (root / 'adb').unlink()
            result = subprocess.run(['bash', '-euc', 'source "$1"\nryuo_check_commands "$2"',
                                     'test', str(COMMON), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Required executable missing', result.stderr)
            self.assertIn('/adb', result.stderr)

    def test_unknown_options_fail_before_package_operations(self):
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--invalid-option'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unknown option', result.stderr)

    def test_staged_install_preserves_config_and_uses_matching_python(self):
        for python in ('/usr/bin/python3', '/usr/local/share/ryuo-control/venv/bin/python'):
            with self.subTest(python=python), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory)
                settings = stage / 'etc/ryuo-lcd.json'
                drop_in = stage / 'etc/systemd/system/ryuo-lcd.service.d/override.conf'
                drop_in.parent.mkdir(parents=True)
                settings.write_text('existing settings')
                drop_in.write_text('existing override')
                self.run_bash('source "$1"\nRYUO_PYTHON="$2"\nryuo_install_files "$3"', COMMON, python, stage)
                service = (stage / 'etc/systemd/system/ryuo-lcd.service').read_text()
                launcher = stage / 'usr/local/bin/ryuo-control'
                self.assertIn(f'ExecStart={python} -u /usr/local/bin/ryuo-lcd.py', service)
                self.assertIn(f'exec "{python}" /usr/local/bin/ryuo-gui.py "$@"', launcher.read_text())
                self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
                self.assertIn(f'exec "{python}"', (stage / 'usr/local/bin/ryuo-lcd').read_text())
                self.assertEqual(settings.read_text(), 'existing settings')
                self.assertEqual(drop_in.read_text(), 'existing override')
                self.assertTrue((stage / 'usr/local/bin/ryuo_i18n.py').is_file())
                self.assertTrue((stage / 'usr/local/bin/ryuo_hid.py').is_file())
                self.assertEqual((stage / 'usr/local/bin/ryuo-uninstall').read_text(),
                                 (ROOT / 'scripts/uninstall.py').read_text())
                self.assertEqual((stage / 'etc/udev/rules.d/70-ryuo-lcd.rules').read_text(),
                                 (ROOT / '70-ryuo-lcd.rules').read_text())
                self.assertTrue((stage / 'usr/local/share/ryuo-control/ryuo-screen.png').is_file())
                self.assertIn('Exec=/usr/local/bin/ryuo-control', (stage / 'usr/local/share/applications/ryuo-control.desktop').read_text())

    def atomic_environment(self, directory, missing):
        directory = Path(directory)
        rpm = directory / 'rpm'
        rpm.write_text('#!/bin/sh\n' + ('[ "$2" != python3-hidapi ]\n' if missing else 'exit 0\n'))
        ostree = directory / 'rpm-ostree'
        ostree.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RYUO_TEST_LOG"\n')
        rpm.chmod(0o755)
        ostree.chmod(0o755)
        return {**os.environ, 'PATH': f'{directory}:{os.environ["PATH"]}',
                'RYUO_TEST_LOG': str(directory / 'commands.log')}

    def test_atomic_installs_only_missing_packages_and_requires_reboot(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_bash('source "$1"\nryuo_atomic_packages python3-hidapi polkit\nprintf "REBOOT=%s\\n" "$RYUO_REBOOT_REQUIRED"',
                                   COMMON, env=self.atomic_environment(directory, missing=True))
            self.assertIn('REBOOT=1', result.stdout)
            self.assertIn('Reboot', result.stdout)
            self.assertEqual((Path(directory) / 'commands.log').read_text().splitlines(),
                             ['install', '--idempotent', 'python3-hidapi'])

    def test_atomic_continues_without_new_deployment_when_dependencies_are_present(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_bash('source "$1"\nryuo_atomic_packages python3-hidapi polkit\nprintf "REBOOT=%s\\n" "$RYUO_REBOOT_REQUIRED"',
                                   COMMON, env=self.atomic_environment(directory, missing=False))
            self.assertIn('REBOOT=0', result.stdout)
            self.assertFalse((Path(directory) / 'commands.log').exists())


if __name__ == '__main__':
    unittest.main()
