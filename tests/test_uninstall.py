import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('uninstall', ROOT / 'scripts/uninstall.py')
uninstall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uninstall)


class UninstallTests(unittest.TestCase):
    def fixture(self, root):
        for filename in uninstall.FILES:
            path = root / filename.lstrip('/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('installed file')
        for filename in ('etc/ryuo-lcd.json', 'etc/systemd/system/ryuo-lcd.service.d/override.conf',
                         'usr/local/share/ryuo-control/venv/bin/python',
                         'usr/local/bin/__pycache__/ryuo_hid.cpython-315.pyc',
                         'usr/local/bin/__pycache__/other_app.cpython-315.pyc',
                         'home/user/.cache/ryuo-control/videos/saved.mp4',
                         'usr/local/bin/other-app'):
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('keep or remove as specified')

    def runner(self, load='loaded', active='active'):
        return Mock(return_value=SimpleNamespace(returncode=0, stderr='',
                    stdout=f'LoadState={load}\nActiveState={active}\n'))

    def execute(self, root, **kwargs):
        runner = kwargs.pop('runner', self.runner())
        with patch.object(uninstall.shutil, 'which', return_value='/usr/bin/udevadm'), \
                contextlib.redirect_stdout(io.StringIO()):
            uninstall.Uninstaller(root=root, runner=runner, **kwargs).execute()
        return runner

    def test_default_removes_only_application_and_preserves_settings_and_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            runner = self.execute(root)
            for filename in uninstall.FILES:
                self.assertFalse((root / filename.lstrip('/')).exists())
            self.assertFalse((root / 'usr/local/share/ryuo-control').exists())
            self.assertFalse((root / 'usr/local/bin/__pycache__/ryuo_hid.cpython-315.pyc').exists())
            for filename in ('etc/ryuo-lcd.json', 'etc/systemd/system/ryuo-lcd.service.d/override.conf',
                             'home/user/.cache/ryuo-control/videos/saved.mp4',
                             'usr/local/bin/other-app', 'usr/local/bin/__pycache__/other_app.cpython-315.pyc'):
                self.assertTrue((root / filename).exists())
            commands = [call.args[0] for call in runner.call_args_list]
            self.assertEqual(commands[1], ['/usr/bin/systemctl', 'disable', '--now', 'ryuo-lcd.service'])
            self.assertFalse(any(command[0] in ('dnf', 'apt-get', 'pacman', 'zypper', 'rpm-ostree', 'adb') for command in commands))

    def test_purge_removes_system_settings_but_keeps_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.execute(root, purge=True)
            self.assertFalse((root / 'etc/ryuo-lcd.json').exists())
            self.assertFalse((root / 'etc/systemd/system/ryuo-lcd.service.d').exists())
            self.assertTrue((root / 'home/user/.cache/ryuo-control/videos/saved.mp4').exists())

    def test_dry_run_never_executes_commands_or_deletes_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            before = {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            runner = self.execute(root, dry_run=True, purge=True)
            runner.assert_not_called()
            self.assertEqual(before, {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()})

    def test_stop_failure_aborts_before_removing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            runner = self.runner()
            runner.side_effect = [runner.return_value, subprocess.CalledProcessError(1, 'stop')]
            with self.assertRaises(subprocess.CalledProcessError):
                self.execute(root, runner=runner)
            self.assertTrue((root / 'usr/local/bin/ryuo-lcd.py').exists())

    def test_repeated_uninstall_when_service_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.execute(root)
            runner = self.execute(root, runner=self.runner('not-found', 'inactive'))
            self.assertFalse(any('disable' in call.args[0] for call in runner.call_args_list))

    def test_does_not_follow_private_runtime_symlink(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            target = Path(outside) / 'keep.txt'
            target.write_text('do not remove')
            shared = root / 'usr/local/share'
            shared.mkdir(parents=True)
            (shared / 'ryuo-control').symlink_to(outside, target_is_directory=True)
            self.execute(root)
            self.assertTrue(target.exists())
            self.assertFalse((shared / 'ryuo-control').is_symlink())

    def test_installed_uninstaller_is_self_contained(self):
        subprocess.run(['bash', '-n', str(ROOT / 'uninstall.sh')], check=True)
        result = subprocess.run(['bash', str(ROOT / 'uninstall.sh'), '--help'],
                                capture_output=True, text=True, check=True)
        self.assertIn('--purge', result.stdout)
        self.assertIn('--dry-run', result.stdout)


if __name__ == '__main__':
    unittest.main()
