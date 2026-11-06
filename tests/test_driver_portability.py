"""Linux interface fixtures and HID contracts; no real USB writes."""
import ast
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import ryuo_hid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('portable_driver', ROOT / 'ryuo-lcd.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class HidPortabilityTests(unittest.TestCase):
    def test_three_binding_apis_send_same_report(self):
        report = b'\x00' + bytes(range(256)) * 4
        for api in ('cython', 'ctypes', 'legacy'):
            with self.subTest(api=api):
                handle = Mock()
                handle.manufacturer = 'ASUS'
                handle.product = 'RYUO'
                handle.get_manufacturer_string.return_value = 'ASUS'
                handle.get_product_string.return_value = 'RYUO'
                handle.write.return_value = len(report)
                module = SimpleNamespace(device=Mock(return_value=handle), Device=Mock(return_value=handle),
                    hid_open=Mock(return_value=handle), hid_close=Mock(),
                    hid_write=Mock(return_value=len(report)), hid_set_nonblocking=Mock(),
                    hid_get_manufacturer_string=Mock(return_value='ASUS'),
                    hid_get_product_string=Mock(return_value='RYUO'))
                device = ryuo_hid.HidDevice(module, api)
                device.open(0x0b05, 0x1c76)
                device.set_nonblocking(1)
                self.assertEqual(device.write(report), 1025)
                self.assertEqual(device.get_manufacturer_string(), 'ASUS')
                self.assertEqual(device.get_product_string(), 'RYUO')
                if api == 'ctypes':
                    module.Device.assert_called_once_with(vid=0x0b05, pid=0x1c76)
                    self.assertTrue(handle.nonblocking)
                if api == 'legacy':
                    self.assertEqual(module.hid_write.call_args.args, (handle, bytearray(report)))
                else:
                    handle.write.assert_called_once_with(report)
                device.close()
                device.close()
                if api == 'legacy':
                    module.hid_close.assert_called_once_with(handle)
                else:
                    handle.close.assert_called_once()

    def test_loader_detects_apis_and_initializes_legacy(self):
        for api, module in (
            ('cython', SimpleNamespace(device=lambda: None)),
            ('ctypes', SimpleNamespace(Device=lambda **kwargs: None)),
            ('legacy', SimpleNamespace(**{name: Mock() for name in (
                'hid_init', 'hid_open', 'hid_write', 'hid_close', 'hid_set_nonblocking',
                'hid_get_manufacturer_string', 'hid_get_product_string')}))):
            with self.subTest(api=api), patch.object(ryuo_hid.importlib, 'import_module', return_value=module):
                self.assertEqual(ryuo_hid.load_backend(), (module, api))
                if api == 'legacy':
                    module.hid_init.assert_called_once()

    def test_loader_reports_missing_native_library_without_distro_specific_command(self):
        with patch.object(ryuo_hid.importlib, 'import_module', side_effect=ImportError('native library missing')):
            with self.assertRaisesRegex(RuntimeError, 'installer for your distribution'):
                ryuo_hid.load_backend()

    def test_sysfs_detection_excludes_other_asus_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (vendor, product) in enumerate((('0b05', '1c76'), ('1c75', '1c76'),
                                                     ('0b05', '1aa6'), ('ffff', '1c76'))):
                path = root / str(index)
                path.mkdir()
                (path / 'idVendor').write_text(vendor)
                (path / 'idProduct').write_text(product)
            devices = ryuo_hid.connected_devices(root)
            self.assertEqual([device['vendor_id'] for device in devices], [0x0b05, 0x1c75])
            self.assertTrue(all(device['product_id'] == 0x1c76 for device in devices))

    def test_open_tries_supported_vendor_ids_and_closes_failed_handle(self):
        first, second = Mock(), Mock()
        first.open.side_effect = OSError('absent')
        module = SimpleNamespace(device=Mock(side_effect=[first, second]))
        with patch.object(ryuo_hid, 'load_backend', return_value=(module, 'cython')), \
                patch.object(ryuo_hid, 'connected_devices', return_value=[]):
            device = ryuo_hid.open_ryuo_device()
            first.open.assert_called_once_with(0x0b05, 0x1c76)
            first.close.assert_called_once()
            second.open.assert_called_once_with(0x1c75, 0x1c76)
            device.close()
            second.close.assert_called_once()

    def test_legacy_null_handle_is_not_closed(self):
        module = SimpleNamespace(hid_open=Mock(return_value=None), hid_close=Mock())
        with self.assertRaises(OSError):
            ryuo_hid.HidDevice(module, 'legacy').open(0x0b05, 0x1c76)
        module.hid_close.assert_not_called()


class SensorPortabilityTests(unittest.TestCase):
    def write(self, root, name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value))

    def fixture(self, root):
        self.write(root, 'proc/stat', 'cpu 100 0 0 100 0 0 0 0 0 0\n')
        self.write(root, 'proc/meminfo', 'MemTotal: 100000 kB\nMemAvailable: 40000 kB\n')
        return root / 'sys', root / 'proc'

    def test_missing_optional_sysfs_is_safe_and_does_not_report_full_cpu_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            sysfs, proc = self.fixture(Path(directory))
            sensors = driver.get_sensors(sys_root=sysfs, proc_root=proc, sample_interval=0)
            self.assertEqual(sensors['cpu']['temperature'], 0)
            self.assertEqual(sensors['cpu']['usage'], 0)
            self.assertEqual(sensors['memory']['load'], 60)

    def test_ccds_gpu_without_fan_and_multiple_gpu_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysfs, proc = self.fixture(root)
            for filename, value in [('name', 'k10temp'), ('temp1_input', 55000),
                                    ('temp3_label', 'Tccd1'), ('temp3_input', 36000),
                                    ('temp4_label', 'Tccd2'), ('temp4_input', 41000)]:
                self.write(sysfs, f'class/hwmon/hwmon2/{filename}', value)
            for path, vram, temp, usage in [('hwmon0', 8_000_000_000, 60000, 37),
                                           ('hwmon9', 256_000_000, 42000, 7)]:
                for filename, value in [('name', 'amdgpu'), ('temp1_input', temp),
                                        ('device/mem_info_vram_total', vram),
                                        ('device/gpu_busy_percent', usage), ('power1_input', 75_000_000)]:
                    self.write(sysfs, f'class/hwmon/{path}/{filename}', value)
            for cpu, freq in [(0, 2000000), (1, 3000000)]:
                self.write(sysfs, f'devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq', freq)
            sensors = driver.get_sensors(sys_root=sysfs, proc_root=proc, sample_interval=0)
            self.assertEqual(sensors['cpu']['temperature'], 38.5)
            self.assertEqual(sensors['cpu']['speedAverage'], 2500)
            self.assertEqual(sensors['gpu']['temperature'], '60.0')
            self.assertEqual(sensors['gpu']['usage'], 37)
            self.assertEqual(sensors['gpu']['fan'], 0)
            self.assertEqual(sensors['gpu']['power'], 75)
            self.assertEqual(driver.get_sensors('tctl', sys_root=sysfs, proc_root=proc, sample_interval=0)['cpu']['temperature'], 55)

    def test_frequency_and_memory_fallbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysfs, proc = self.fixture(root)
            self.write(root, 'proc/cpuinfo', 'cpu MHz : 2100.0\ncpu MHz : 2300.0\n')
            self.write(root, 'proc/meminfo', 'MemTotal: 100000 kB\nMemFree: 10000 kB\nCached: 20000 kB\nBuffers: 5000 kB\n')
            sensors = driver.get_sensors(sys_root=sysfs, proc_root=proc, sample_interval=0)
            self.assertEqual(sensors['cpu']['speedAverage'], 2200)
            self.assertEqual(sensors['memory']['load'], 65)

    def test_intel_prefers_package_temperature_and_handles_multiple_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysfs, proc = self.fixture(root)
            for index, label, temp in [(0, 'Package id 0', 55000), (1, 'Physical id 1', 59000)]:
                path = f'class/hwmon/hwmon{index}'
                for filename, value in [('name', 'coretemp'), ('temp1_label', label),
                                        ('temp1_input', temp), ('temp2_label', 'Core 0'), ('temp2_input', 80000)]:
                    self.write(sysfs, f'{path}/{filename}', value)
            for mode in ('ccd_average', 'tctl'):
                report = {}
                sensors = driver.get_sensors(mode, sys_root=sysfs, proc_root=proc, sample_interval=0, report=report)
                self.assertEqual(sensors['cpu']['temperature'], 59)
                self.assertEqual(report['cpu'], 'coretemp')

    def test_intel_without_package_uses_hottest_readable_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, 'temp2_label', 'Core 0')
            self.write(root, 'temp2_input', '43000')
            self.write(root, 'temp3_label', 'Core 1')
            self.write(root, 'temp3_input', '48000')
            self.write(root, 'temp4_input', 'N/A')
            self.assertEqual(driver.intel_temperature(root), 48)

    def test_nvidia_query_converts_csv_and_selects_largest_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'module/nvidia').mkdir(parents=True)
            result = SimpleNamespace(stdout='0, 55, 10, 40.0, 1000, 4096\n1, 62, 87, 75.4, 1920, 8192\n')
            report = {}
            with patch.object(driver.shutil, 'which', return_value='/usr/bin/nvidia-smi'), \
                    patch.object(driver.subprocess, 'run', return_value=result) as run:
                gpu = driver.read_nvidia_gpu(root, report)
            self.assertEqual(gpu, {'temperature': 62, 'usage': 87, 'power': 75.4, 'speed': 1920})
            self.assertEqual(report['nvidia_gpu_index'], 1)
            self.assertEqual(run.call_args.kwargs['timeout'], 1)
            self.assertEqual(run.call_args.kwargs['env']['LC_ALL'], 'C')
            self.assertTrue(run.call_args.kwargs['check'])
            self.assertIn('--format=csv,noheader,nounits', run.call_args.args[0])

    def test_nvidia_overrides_amd_igpu_and_preserves_unavailable_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysfs, proc = self.fixture(root)
            (sysfs / 'module/nvidia').mkdir(parents=True)
            self.write(sysfs, 'class/hwmon/hwmon0/name', 'amdgpu')
            self.write(sysfs, 'class/hwmon/hwmon0/temp1_input', 42000)
            result = SimpleNamespace(stdout='0, 61, N/A, [Not Supported], 1455, 8192\n')
            report = {}
            with patch.object(driver.shutil, 'which', return_value='/usr/bin/nvidia-smi'), \
                    patch.object(driver.subprocess, 'run', return_value=result):
                sensors = driver.get_sensors(sys_root=sysfs, proc_root=proc, sample_interval=0, report=report)
            self.assertEqual(report['gpu'], 'nvidia-smi')
            self.assertEqual(report['gpu_unavailable'], ['usage', 'power'])
            self.assertEqual(sensors['gpu']['temperature'], '61.0')
            self.assertEqual(sensors['gpu']['speed'], 1455)
            self.assertEqual(sensors['gpu']['fan'], 0)
            self.assertEqual(sensors['gpu']['power'], 0)

    def test_missing_nvidia_tools_and_failed_queries_do_not_block_amd(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysfs, proc = self.fixture(root)
            (sysfs / 'module/nvidia').mkdir(parents=True)
            self.write(sysfs, 'class/hwmon/hwmon0/name', 'amdgpu')
            self.write(sysfs, 'class/hwmon/hwmon0/temp1_input', 42000)
            for error in (subprocess.TimeoutExpired('nvidia-smi', 1),
                          subprocess.CalledProcessError(1, 'nvidia-smi', stderr='driver mismatch')):
                report = {}
                with patch.object(driver.shutil, 'which', return_value='/usr/bin/nvidia-smi'), \
                        patch.object(driver.subprocess, 'run', side_effect=error):
                    sensors = driver.get_sensors(sys_root=sysfs, proc_root=proc, sample_interval=0, report=report)
                self.assertEqual(report['gpu'], 'amdgpu')
                self.assertEqual(sensors['gpu']['temperature'], '42.0')
                self.assertIn('failed', report['nvidia_status'])
            with patch.object(driver.shutil, 'which', return_value=None), \
                    patch.object(driver.subprocess, 'run') as run:
                self.assertIsNone(driver.read_nvidia_gpu(sysfs))
                run.assert_not_called()

    def test_nvidia_invalid_csv_is_ignored_and_absent_driver_is_not_polled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(driver.subprocess, 'run') as run:
                self.assertIsNone(driver.read_nvidia_gpu(root))
                run.assert_not_called()
            (root / 'module/nvidia').mkdir(parents=True)
            for output in ('bad,row\n', 'not-an-index,60,20,40,1500,4096\n',
                           '0,N/A,N/A,N/A,N/A,4096\n'):
                with patch.object(driver.shutil, 'which', return_value='/usr/bin/nvidia-smi'), \
                        patch.object(driver.subprocess, 'run', return_value=SimpleNamespace(stdout=output)):
                    self.assertIsNone(driver.read_nvidia_gpu(root))

    def test_cpu_counters_ignore_guest_and_account_for_iowait(self):
        with patch.object(driver, 'cpu_times', side_effect=[(200, 100), (300, 150)]), \
                patch.object(driver, 'cpu_frequency', return_value=1000):
            with tempfile.TemporaryDirectory() as directory:
                sysfs, proc = self.fixture(Path(directory))
                self.assertEqual(driver.get_sensors(sys_root=sysfs, proc_root=proc, sample_interval=0)['cpu']['usage'], 50)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, 'stat', 'cpu 100 0 0 80 20 0 0 0 9999 8888\n')
            self.assertEqual(driver.cpu_times(root), (200, 100))

    def test_timezone_from_copied_localtime_and_tz_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(driver.os.environ, {}, clear=True):
            root = Path(directory)
            self.write(root, 'localtime', 'binary copied zonefile')
            self.write(root, 'timezone', 'America/Santiago\n')
            self.assertEqual(driver.local_timezone(root / 'localtime', root / 'timezone'), 'America/Santiago')
            with patch.dict(driver.os.environ, {'TZ': ':Europe/Prague'}):
                self.assertEqual(driver.local_timezone(root / 'localtime', root / 'timezone'), 'Europe/Prague')

    def test_open_handle_is_closed_on_initial_configuration_failure(self):
        device = Mock()
        with patch.object(driver, 'open_ryuo_device', return_value=device), \
                patch.object(driver, 'load_config', side_effect=ValueError('bad settings')), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                driver.main(once=True)
        device.close.assert_called_once()

    def test_diagnosis_never_opens_usb(self):
        with patch.object(driver, 'load_backend', side_effect=RuntimeError('binding missing')), \
                patch.object(driver, 'open_ryuo_device') as open_device:
            result = driver.diagnostics()
        open_device.assert_not_called()
        self.assertFalse(result['usb_access_tested'])
        self.assertEqual(result['hid_error'], 'binding missing')

    def test_python_310_syntax_compatibility(self):
        for path in ROOT.glob('*.py'):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(), feature_version=(3, 10))


if __name__ == '__main__':
    unittest.main()
