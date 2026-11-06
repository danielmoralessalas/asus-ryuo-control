import importlib.util
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ryuo_config import PRESETS, load_config, screen_config, validate_config, valid_media_name, custom_video_path

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


driver = module('driver', 'ryuo-lcd.py')
helper = module('helper', 'ryuo-control.py')


class ConfigTests(unittest.TestCase):
    def test_deletion_paths_exclude_all_presets_and_unsafe_names(self):
        self.assertEqual(custom_video_path('clip_user.mp4'), '/sdcard/pcMedia/clip_user.mp4')
        for name in PRESETS + ['RYUO_other.mp4', '../clip.mp4', '/sdcard/pcMediaPreset/a.mp4',
                               'clip*.mp4', 'clip;reboot.mp4', '-rf.mp4', 'clip.mp4\n']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                custom_video_path(name)

    def test_defaults_are_independent(self):
        first = validate_config({})
        first['media'].clear()
        self.assertEqual(validate_config({})['media'], PRESETS)

    def test_invalid_settings_rejected(self):
        for data in ({'media': []}, {'media': ['../../video']},
                     {'media': [PRESETS[0], PRESETS[0]]}, {'sensors': ['CPU Voltage']},
                     {'sensors': []}, {'sensors': [''] * 7}, {'sensors': [None]},
                     {'brightness': True}, {'brightness': 101},
                     {'adb_serial': 'serial; reboot'}, {'title_color': 'red'},
                     {'play_mode': 'loop'}, {'cpu_temp_mode': 'unknown'}, {'command': 'sh'}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_config(data)

    def test_sensor_slots_preserve_holes_and_migrate_old_settings(self):
        sensors = ['', 'GPU Temperature', '', 'CPU Temperature', '', 'CPU Load']
        config = validate_config({'sensors': sensors})
        self.assertEqual(config['sensors'], sensors)
        self.assertEqual(screen_config(config)['sysinfoDisplay'], sensors)
        self.assertEqual(validate_config({'sensors': ['CPU Load']})['sensors'],
                         ['CPU Load', '', '', '', '', ''])
        self.assertEqual(validate_config({'sensors': [''] * 6})['sensors'], [''] * 6)
        self.assertEqual(validate_config({'sensors': ['CPU Load', 'CPU Load']})['sensors'][:2],
                         ['CPU Load', 'CPU Load'])

    def test_custom_media_names_and_size_limit(self):
        self.assertTrue(valid_media_name('clip_mi_video_abc123.mp4'))
        for name in ('../video.mp4', '/tmp/video.mp4', 'RYUO_custom.mp4',
                     'video;reboot.mp4', 'video.webm', '-video.mp4', 'a' * 90 + '.mp4'):
            with self.subTest(name=name):
                self.assertFalse(valid_media_name(name))
                with self.assertRaises(ValueError):
                    validate_config({'media': [name]})
        config = validate_config({'media': ['clip_custom.mp4']})
        self.assertEqual(screen_config(config)['media'], ['clip_custom.mp4'])
        with self.assertRaises(ValueError):
            validate_config({'media': [f'{i}' + 'a' * 74 + '.mp4' for i in range(5)],
                             'sensors': ['CPU Speed Average'] * 6})

    def test_atomic_round_trip_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            self.assertEqual(load_config(path), validate_config({}))
            config = validate_config({'media': [PRESETS[2]], 'brightness': 76})
            helper.write_config(config, path)
            self.assertEqual(load_config(path), config)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            with self.assertRaises(ValueError):
                helper.write_config({'brightness': -1}, path)
            self.assertEqual(load_config(path), config)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_invalid_file_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                load_config(path)


class ProtocolTests(unittest.TestCase):
    def test_screen_settings_fit_report_and_checksum(self):
        config = validate_config({'adb_serial': 'TC123', 'brightness': 75})
        screen = screen_config(config)
        self.assertNotIn('adb_serial', screen)
        content = json.dumps(screen, separators=(',', ':'))
        packet = driver.build_packet(content, 7, 'waterBlockScreenId')
        self.assertLessEqual(len(packet), 1024)
        raw = packet[1:-1].replace(b'\x5b\x01', b'\x5a').replace(b'\x5b\x02', b'\x5b')
        self.assertEqual(struct.unpack('>H', raw[:2])[0], len(raw) + 2)
        self.assertEqual(raw[-1], sum(raw[:-1]) & 255)
        self.assertIn(b'POST waterBlockScreenId 1\r\n', raw)
        self.assertIn(content.encode(), raw)

    def test_ccd_average_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'temp1_input').write_text('55000')
            self.assertEqual(driver.cpu_temperature(path), 55)
            for number, temperature in [(3, '36000'), (4, '41000')]:
                (path / f'temp{number}_label').write_text(f'Tccd{number - 2}')
                (path / f'temp{number}_input').write_text(temperature)
            self.assertEqual(driver.cpu_temperature(path), 38.5)
            (path / 'temp4_input').write_text('unavailable')
            self.assertEqual(driver.cpu_temperature(path), 36)

    def test_hid_short_write_and_oversize(self):
        class Device:
            def write(self, data):
                return 0
        with self.assertRaises(OSError):
            driver.send_packet(Device(), b'test')
        with self.assertRaises(ValueError):
            driver.send_packet(Device(), b'x' * 1025)


class HelperTests(unittest.TestCase):
    def test_apply_works_without_selinux_utilities(self):
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.sys, 'argv', ['helper', 'apply']), \
                patch.object(helper.sys, 'stdin', io.StringIO('{}')), \
                patch.object(helper.shutil, 'which', return_value=None), \
                patch.object(helper.subprocess, 'run') as run, \
                patch.object(helper, 'write_config'):
            helper.main()
            run.assert_called_once_with(['/usr/bin/systemctl', 'restart', 'ryuo-lcd.service'], check=True, timeout=45)

    def test_invalid_input_cannot_restart_service(self):
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.sys, 'argv', ['helper', 'apply']), \
                patch.object(helper.sys, 'stdin', io.StringIO('{"brightness": -1}')), \
                patch.object(helper.subprocess, 'run') as run, \
                patch.object(helper, 'write_config') as write:
            with self.assertRaises(ValueError):
                helper.main()
            run.assert_not_called()
            write.assert_not_called()

    def test_apply_validates_before_restart(self):
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.sys, 'argv', ['helper', 'apply']), \
                patch.object(helper.sys, 'stdin', io.StringIO('{"brightness": 75}')), \
                patch.object(helper.subprocess, 'run') as run, \
                patch.object(helper, 'write_config') as write:
            helper.main()
            self.assertEqual(write.call_args.args[0]['brightness'], 75)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'restart', 'ryuo-lcd.service'])

    def test_unknown_action_rejected(self):
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.sys, 'argv', ['helper', 'anything']), \
                patch.object(helper.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                helper.main()
            run.assert_not_called()


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.gui = module('gui', 'ryuo-gui.py')

    def test_edit_reload_and_sensor_display(self):
        with patch.object(self.gui, 'load_config', return_value=validate_config({})):
            window = self.gui.Window(language='es', monitor=False)
            self.assertEqual(window.collect_settings(), validate_config({}))
            window.brightness.setValue(62)
            self.assertTrue(window.dirty)
            self.assertEqual(window.collect_settings()['brightness'], 62)
            window.reload_settings()
            self.assertFalse(window.dirty)
            window.update_status({'sensors': driver.get_sensors(), 'device': ('RYUO', '123'),
                                  'service': {'ActiveState': 'active', 'SubState': 'running', 'UnitFileState': 'enabled'}})
            self.assertIn('°C', window.metrics['cpu'][0].text())
            self.assertIn('active', window.service_label.text())
            window.close()

    def test_language_defaults_to_english_and_does_not_follow_system_locale(self):
        from PySide6.QtCore import QLocale, QSettings
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})):
            QLocale.setDefault(QLocale('es'))
            preferences = QSettings(str(Path(directory) / 'ui.ini'), QSettings.Format.IniFormat)
            window = self.gui.Window(monitor=False, preferences=preferences)
            self.assertEqual(window.language, 'en')
            self.assertEqual(window.nav.item(0).text(), 'Overview')
            self.assertEqual(window.editor_sensor.itemText(1), 'CPU temperature')
            self.assertIn('CPU temperature', window.slot_previews[0].text())
            self.assertEqual(window.editor_sensor.itemData(1), 'CPU Temperature')
            self.assertFalse(window.dirty)
            window.close()

    def test_language_switch_is_live_persistent_and_preserves_pending_settings(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QMessageBox
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})):
            preferences = QSettings(str(Path(directory) / 'ui.ini'), QSettings.Format.IniFormat)
            window = self.gui.Window(monitor=False, preferences=preferences)
            window.nav.setCurrentRow(1)
            window.brightness.setValue(62)
            before = window.collect_settings()
            window.device_preview.player = Mock()
            player = window.device_preview.player
            window.preview_play.setText(self.gui.tr('Pausar vista previa'))
            window.language_choice.setCurrentIndex(window.language_choice.findData('es'))
            self.assertEqual(window.nav.item(1).text(), 'Pantalla')
            self.assertEqual(window.preview_play.text(), 'Pausar vista previa')
            self.assertEqual(window.editor_sensor.itemText(1), 'Temperatura CPU')
            dialog = QMessageBox(window)
            dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            self.assertEqual(dialog.button(QMessageBox.StandardButton.Yes).text().replace('&', ''), 'Sí')
            self.assertEqual(window.collect_settings(), before)
            self.assertEqual(window.nav.currentRow(), 1)
            self.assertTrue(window.dirty)
            player.stop.assert_not_called()
            player.setSource.assert_not_called()
            self.assertEqual(preferences.value('language'), 'es')
            window.language_choice.setCurrentIndex(window.language_choice.findData('en'))
            self.assertEqual(window.preview_play.text(), 'Pause preview')
            self.assertIn('CPU temperature', window.slot_previews[0].text())
            self.assertEqual(window.collect_settings(), before)
            window.language_choice.setCurrentIndex(window.language_choice.findData('es'))
            window.dirty = False
            window.close()
            reopened = self.gui.Window(monitor=False, preferences=preferences)
            self.assertEqual(reopened.language, 'es')
            self.assertEqual(reopened.nav.item(0).text(), 'Resumen')
            reopened.close()

    def test_translation_changes_validation_messages_but_not_protocol(self):
        from ryuo_i18n import set_language
        config = validate_config({})
        try:
            set_language('en')
            with self.assertRaisesRegex(ValueError, 'Brightness must'):
                validate_config({'brightness': -1})
            english = screen_config(config)
            set_language('es')
            with self.assertRaisesRegex(ValueError, 'El brillo debe'):
                validate_config({'brightness': -1})
            self.assertEqual(screen_config(config), english)
        finally:
            set_language('en')

    def test_device_editor_replaces_old_panel_and_edits_selected_sensor(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QGroupBox
        config = validate_config({})
        with patch.object(self.gui, 'load_config', return_value=config):
            window = self.gui.Window(language='es', monitor=False)
            self.assertFalse(any(box.title().startswith('Disposición de sensores')
                                 for box in window.findChildren(QGroupBox)))
            self.assertFalse(window.device_preview.picture.isNull())
            window.slot_previews[3].selected.emit(3)
            self.assertEqual(window.editor_slot.currentIndex(), 3)
            self.assertEqual(window.editor_sensor.currentData(), config['sensors'][3])
            self.assertFalse(window.dirty)
            window.editor_sensor.setCurrentIndex(window.editor_sensor.findData('GPU Temperature'))
            self.assertEqual(window.collect_settings()['sensors'][3], 'GPU Temperature')
            self.assertIn('Temperatura GPU', window.slot_previews[3].text())
            QTest.keyClick(window.slot_previews[1], Qt.Key.Key_Return)
            self.assertEqual(window.editor_slot.currentIndex(), 1)
            window.reload_settings()
            self.assertEqual(window.collect_settings(), config)
            self.assertFalse(window.dirty)
            window.close()

    def test_device_overlay_resizes_and_displays_live_values(self):
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QResizeEvent
        with patch.object(self.gui, 'load_config', return_value=validate_config({})):
            window = self.gui.Window(language='es', monitor=False)
            canvas = window.device_preview
            for width in (540, 850):
                canvas.resize(width, 510)
                # Send the resize explicitly because the offscreen widget is hidden.
                canvas.resizeEvent(QResizeEvent(canvas.size(), canvas.size()))
                for card in canvas.cards:
                    self.assertTrue(QRect(0, 0, width, 510).contains(card.geometry()))
                for i, card in enumerate(canvas.cards):
                    self.assertFalse(any(card.geometry().intersects(other.geometry())
                                         for other in canvas.cards[i + 1:]))
            sensors = driver.get_sensors()
            sensors['cpu']['temperature'] = 42.5
            window.update_status({'sensors': sensors})
            self.assertIn('42.5 °C', window.slot_previews[0].text())
            self.assertFalse(window.dirty)
            window.close()

    def test_nvidia_unavailable_values_display_dash_in_overview_and_preview(self):
        with patch.object(self.gui, 'load_config', return_value=validate_config({})):
            window = self.gui.Window(language='es', monitor=False)
            sensors = driver.get_sensors()
            sensors['gpu'].update(temperature='61.0', usage=0, power=0, speed=1455)
            window.update_status({'sensors': sensors, 'sensor_sources': {
                'gpu': 'nvidia-smi', 'gpu_unavailable': ['usage', 'power'], 'cpu': 'coretemp'}})
            self.assertEqual(window.preview_value('GPU Load'), '—')
            self.assertEqual(window.preview_value('GPU Power'), '—')
            self.assertEqual(window.preview_value('GPU Speed'), '1455 MHz')
            self.assertEqual(window.metrics['gpu'][1].text(), '— · —')
            self.assertGreaterEqual(window.editor_sensor.findData('GPU Speed'), 0)
            self.assertGreaterEqual(window.editor_sensor.findData('GPU Power'), 0)
            self.assertFalse(window.dirty)
            window.close()

    def test_preview_cache_is_per_device_and_uses_only_safe_names(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.gui.preview_cache_path('TC1', PRESETS[0], directory)
            second = self.gui.preview_cache_path('TC2', PRESETS[0], directory)
            self.assertNotEqual(first, second)
            self.assertEqual(first.name, PRESETS[0])
            self.assertEqual(first.parent.parent, Path(directory))
            for name in ('../escape.mp4', '/tmp/escape.mp4'):
                with self.assertRaises(ValueError):
                    self.gui.preview_cache_path('TC1', name, directory)

    def test_preview_downloads_missing_file_and_reuses_cache(self):
        from PySide6.QtCore import QProcess
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False, cache_root=directory)
            name = window.preview_choice.currentData()
            before = window.collect_settings()
            with patch.object(QProcess, 'start') as start, \
                    patch.object(window.device_preview, 'play_video') as play:
                window.start_video_preview()
                start.assert_called_with('/usr/bin/adb', ['-s', 'TC123', 'pull', f'/sdcard/pcMediaPreset/{name}', str(window.preview_fetch_temp)])
                self.assertFalse(window.preview_fetch_target.exists())
                window.preview_fetch_temp.write_bytes(b'complete-test-video')
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                play.assert_called_with(window.preview_fetch_target)
                self.assertEqual(window.preview_fetch_target.read_bytes(), b'complete-test-video')
                self.assertFalse(window.preview_fetch_temp.exists())
                window.start_video_preview()
                self.assertEqual(start.call_count, 1)
                self.assertEqual(play.call_count, 2)
            self.assertEqual(window.collect_settings(), before)
            self.assertFalse(window.dirty)
            window.close()

    def test_preview_failed_refresh_preserves_old_cache(self):
        from PySide6.QtCore import QProcess
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False, cache_root=directory)
            target = self.gui.preview_cache_path('TC123', window.preview_choice.currentData(), directory)
            target.parent.mkdir(parents=True)
            target.write_bytes(b'old-good-cache')
            with patch.object(QProcess, 'start'), patch.object(window.device_preview, 'play_video') as play:
                window.start_video_preview(force=True)
                window.preview_fetch_temp.write_bytes(b'partial')
                window.action_finished(1, QProcess.ExitStatus.NormalExit)
                play.assert_not_called()
            self.assertEqual(target.read_bytes(), b'old-good-cache')
            self.assertFalse(window.preview_fetch_temp.exists())
            self.assertIn('no disponible', window.preview_status.text())
            self.assertFalse(window.dirty)
            window.close()

    def test_preview_does_not_play_old_request_after_selection_changes(self):
        from PySide6.QtCore import QProcess
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False, cache_root=directory)
            with patch.object(QProcess, 'start'), patch.object(window.device_preview, 'play_video') as play:
                window.start_video_preview()
                window.preview_choice.setCurrentIndex(1)
                window.preview_fetch_temp.write_bytes(b'complete')
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                play.assert_not_called()
            self.assertTrue(window.preview_fetch_target.exists())
            self.assertFalse(window.dirty)
            window.close()

    def test_preview_new_upload_uses_local_source_without_adb(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({'media': ['clip_new.mp4']})):
            source = Path(directory) / 'local.mp4'
            source.write_bytes(b'local-video')
            window = self.gui.Window(language='es', monitor=False, cache_root=directory)
            window.local_video_sources['clip_new.mp4'] = source
            with patch.object(window, 'start_process') as start, \
                    patch.object(window.device_preview, 'play_video') as play:
                window.start_video_preview()
                play.assert_called_once_with(source)
                start.assert_not_called()
            window.close()

    def test_empty_video_selection_rejected(self):
        with patch.object(self.gui, 'load_config', return_value=validate_config({})):
            window = self.gui.Window(language='es', monitor=False)
            for check in window.video_checks.values():
                check.setChecked(False)
            with self.assertRaises(ValueError):
                window.collect_settings()
            window.dirty = False
            window.close()

    def test_slots_edit_swap_clear_and_reload(self):
        sensors = ['', 'GPU Temperature', '', 'CPU Temperature', '', 'CPU Load']
        config = validate_config({'sensors': sensors})
        with patch.object(self.gui, 'load_config', return_value=config):
            window = self.gui.Window(language='es', monitor=False)
            self.assertEqual(window.collect_settings()['sensors'], sensors)
            window.swap_slots(3, 2)
            expected = ['', 'GPU Temperature', 'CPU Temperature', '', '', 'CPU Load']
            self.assertEqual(window.collect_settings()['sensors'], expected)
            self.assertIn('Temperatura CPU', window.slot_previews[2].text())
            self.assertIn('Vacía', window.slot_previews[3].text())
            self.assertTrue(window.dirty)
            window.reload_settings()
            self.assertEqual(window.collect_settings()['sensors'], sensors)
            self.assertFalse(window.dirty)
            window.clear_slots()
            self.assertEqual(window.collect_settings()['sensors'], [''] * 6)
            window.dirty = False
            window.close()

    def test_drag_drop_moves_sensor_into_empty_slot(self):
        from PySide6.QtCore import QMimeData
        config = validate_config({'sensors': ['CPU Temperature', '', 'GPU Temperature']})
        with patch.object(self.gui, 'load_config', return_value=config):
            window = self.gui.Window(language='es', monitor=False)
            source, target = window.slot_previews[:2]
            mime = QMimeData()
            mime.setData(source.MIME, b'0')
            event = Mock()
            event.source.return_value = source
            event.mimeData.return_value = mime
            with patch.object(window, 'run_action') as action:
                target.dragEnterEvent(event)
                event.accept.assert_called_once()
                target.dropEvent(event)
                action.assert_not_called()
            self.assertEqual(window.collect_settings()['sensors'],
                             ['', 'CPU Temperature', 'GPU Temperature', '', '', ''])
            self.assertIn('Vacía', source.text())
            self.assertIn('Temperatura CPU', target.text())
            self.assertTrue(window.dirty)
            window.reload_settings()
            self.assertEqual(window.collect_settings(), config)
            window.close()

    def test_drag_drop_rejects_foreign_or_forged_sources(self):
        from PySide6.QtCore import QMimeData
        config = validate_config({})
        with patch.object(self.gui, 'load_config', return_value=config):
            window = self.gui.Window(language='es', monitor=False)
            source, target = window.slot_previews[:2]
            mime = QMimeData()
            mime.setData(source.MIME, b'5')
            event = Mock()
            event.source.return_value = source
            event.mimeData.return_value = mime
            target.dropEvent(event)
            event.ignore.assert_called_once()
            event.accept.assert_not_called()
            mime.setData(source.MIME, b'0')
            event.source.return_value = None
            target.dropEvent(event)
            self.assertEqual(window.collect_settings(), config)
            self.assertFalse(window.dirty)
            window.close()

    def test_custom_video_selection_survives_reload(self):
        config = validate_config({'media': ['clip_saved.mp4']})
        with patch.object(self.gui, 'load_config', return_value=config):
            window = self.gui.Window(language='es', monitor=False)
            self.assertTrue(window.video_checks['clip_saved.mp4'].isChecked())
            self.assertEqual(window.collect_settings()['media'], ['clip_saved.mp4'])
            self.assertFalse(window.dirty)
            window.close()

    def test_upload_selects_immediately_and_applies_after_copy(self):
        from PySide6.QtCore import QProcess
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            source = Path(directory) / 'my video.mp4'
            source.write_bytes(b'test-video')
            window = self.gui.Window(language='es', monitor=False)
            with patch.object(self.gui.Path, 'exists', return_value=True), \
                    patch.object(QProcess, 'start') as start:
                window.begin_upload(source)
                start.assert_called_with('/usr/bin/adb', ['-s', 'TC123', 'shell', 'mkdir', '-p', '/sdcard/pcMedia'])
                name = window.upload_name
                self.assertTrue(valid_media_name(name))
                self.assertEqual(window.collect_settings()['media'], [name])
                self.assertTrue(window.video_checks[name].isChecked())
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                start.assert_called_with('/usr/bin/adb', ['-s', 'TC123', 'push', str(source), f'/sdcard/pcMedia/{name}'])
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                start.assert_called_with('/usr/bin/pkexec', ['/usr/local/bin/ryuo-control.py', 'apply'])
                self.assertEqual(window.pending_settings['media'], [name])
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                self.assertIsNone(window.process)
                self.assertFalse(window.dirty)
                self.assertIn('aplicado', window.notice.text())
            window.close()

    def test_upload_accepts_empty_old_playlist_and_restores_on_copy_failure(self):
        from PySide6.QtCore import QProcess
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            source = Path(directory) / 'video.mp4'
            source.write_bytes(b'test-video')
            window = self.gui.Window(language='es', monitor=False)
            for check in window.video_checks.values():
                check.setChecked(False)
            with patch.object(self.gui.Path, 'exists', return_value=True), \
                    patch.object(QProcess, 'start') as start:
                window.begin_upload(source)
                self.assertEqual(window.collect_settings()['media'], [window.upload_name])
                window.action_finished(1, QProcess.ExitStatus.NormalExit)
                self.assertEqual(start.call_count, 1)
            self.assertNotIn(window.upload_name, window.video_checks)
            self.assertFalse(any(check.isChecked() for check in window.video_checks.values()))
            self.assertTrue(window.dirty)
            window.dirty = False
            window.close()

    def test_failed_upload_restores_previous_valid_playlist(self):
        from PySide6.QtCore import QProcess
        config = validate_config({'media': [PRESETS[2]]})
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=config), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            source = Path(directory) / 'video.mp4'
            source.write_bytes(b'test-video')
            window = self.gui.Window(language='es', monitor=False)
            with patch.object(self.gui.Path, 'exists', return_value=True), \
                    patch.object(QProcess, 'start') as start:
                window.begin_upload(source)
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                window.action_finished(1, QProcess.ExitStatus.NormalExit)
                self.assertEqual(start.call_count, 2)
            self.assertEqual(window.collect_settings(), config)
            self.assertFalse(window.dirty)
            window.close()

    def test_cancelled_apply_after_upload_keeps_video_selected_for_retry(self):
        from PySide6.QtCore import QProcess
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            source = Path(directory) / 'video.mp4'
            source.write_bytes(b'test-video')
            window = self.gui.Window(language='es', monitor=False)
            with patch.object(self.gui.Path, 'exists', return_value=True), \
                    patch.object(QProcess, 'start'):
                window.begin_upload(source)
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                window.action_finished(126, QProcess.ExitStatus.NormalExit)
            self.assertEqual(window.collect_settings()['media'], [window.upload_name])
            self.assertTrue(window.dirty)
            self.assertIn('sin volver a subirlo', window.notice.text())
            with patch.object(window, 'run_action') as run:
                window.apply_settings()
                self.assertEqual(run.call_args.args[0], 'apply')
                self.assertEqual(run.call_args.args[1]['media'], [window.upload_name])
            window.dirty = False
            window.close()

    def test_failed_upload_not_added_to_list(self):
        from PySide6.QtCore import QProcess
        with patch.object(self.gui, 'load_config', return_value=validate_config({})):
            window = self.gui.Window(language='es', monitor=False)
            window.upload_name = 'clip_failed.mp4'
            with patch.object(QProcess, 'start'):
                window.start_process('upload', '/usr/bin/adb', [])
                window.action_finished(1, QProcess.ExitStatus.NormalExit)
            self.assertNotIn(window.upload_name, window.video_checks)
            self.assertIsNone(window.process)
            window.close()

    def test_delete_custom_video_requires_confirmation_and_preserves_presets(self):
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QMessageBox
        with patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False)
            name = 'clip_remove.mp4'
            window.add_video(name)
            self.assertTrue(all(preset not in window.delete_buttons for preset in PRESETS))
            with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.No), \
                    patch.object(QProcess, 'start') as start:
                window.delete_video(name)
                start.assert_not_called()
            self.assertIn(name, window.video_checks)
            with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Yes), \
                    patch.object(QProcess, 'start') as start:
                window.delete_video(name)
                start.assert_called_once_with('/usr/bin/adb', ['-s', 'TC123', 'shell', 'rm', '--', '/sdcard/pcMedia/clip_remove.mp4'])
                self.assertIn(name, window.video_checks)
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                start.assert_called_with('/usr/bin/adb', ['-s', 'TC123', 'shell', 'ls', '-1', '/sdcard/pcMedia/'])
                self.assertEqual(window.pending_action, 'refresh_after_delete')
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
            self.assertNotIn(name, window.video_checks)
            self.assertNotIn(name, window.video_rows)
            self.assertNotIn(name, window.delete_buttons)
            self.assertTrue(all(preset in window.video_checks for preset in PRESETS))
            self.assertFalse(window.dirty)
            window.close()

    def test_delete_blocks_presets_and_saved_playlist(self):
        from PySide6.QtWidgets import QMessageBox
        config = validate_config({'media': ['clip_in_use.mp4']})
        with patch.object(self.gui, 'load_config', return_value=config):
            window = self.gui.Window(language='es', monitor=False)
            with patch.object(QMessageBox, 'warning') as warning, \
                    patch.object(QMessageBox, 'exec') as confirm, \
                    patch.object(window, 'start_process') as start:
                for name in PRESETS + ['clip_in_use.mp4', '../bad.mp4']:
                    window.delete_video(name)
                self.assertEqual(warning.call_count, 7)
                confirm.assert_not_called()
                start.assert_not_called()
            window.close()

    def test_failed_delete_keeps_video_and_selection(self):
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QMessageBox
        with patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False)
            name = 'clip_failed.mp4'
            window.add_video(name)
            with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Yes), \
                    patch.object(QProcess, 'start'):
                window.delete_video(name)
                window.action_finished(1, QProcess.ExitStatus.NormalExit)
            self.assertIn(name, window.video_checks)
            self.assertTrue(window.delete_buttons[name].isEnabled())
            self.assertFalse(window.dirty)
            window.close()

    def test_delete_unsaved_only_selection_restores_preset(self):
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QMessageBox
        with patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False)
            name = 'clip_unsaved.mp4'
            window.add_video(name)
            for check in window.video_checks.values():
                check.setChecked(False)
            window.video_checks[name].setChecked(True)
            with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Yes), \
                    patch.object(QProcess, 'start'):
                window.delete_video(name)
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual(window.collect_settings()['media'], [PRESETS[0]])
            self.assertTrue(window.dirty)
            window.dirty = False
            window.close()

    def test_download_both_categories_uses_unique_local_directories(self):
        from PySide6.QtCore import QProcess
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False)
            before = window.collect_settings()
            destinations = []
            with patch.object(QProcess, 'start') as start:
                for category, remote in [('presets', '/sdcard/pcMediaPreset'), ('custom', '/sdcard/pcMedia')]:
                    window.begin_download(category, Path(directory))
                    destination = window.download_destination
                    self.assertTrue(destination.is_dir())
                    self.assertEqual(destination.parent, Path(directory))
                    start.assert_called_with('/usr/bin/adb', ['-s', 'TC123', 'pull', remote, str(destination)])
                    self.assertTrue(all(not button.isEnabled() for button in window.action_buttons))
                    window.action_finished(0, QProcess.ExitStatus.NormalExit)
                    self.assertIn(str(destination), window.notice.text())
                    self.assertIsNone(window.process)
                    destinations.append(destination)
                window.begin_download('presets', Path(directory))
                self.assertNotIn(window.download_destination, destinations)
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
            self.assertEqual(window.collect_settings(), before)
            self.assertFalse(window.dirty)
            window.close()

    def test_failed_download_reports_partial_files(self):
        from PySide6.QtCore import QProcess
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False)
            with patch.object(QProcess, 'start'):
                window.begin_download('presets', Path(directory))
                destination = window.download_destination
                (destination / 'partial.mp4').write_bytes(b'partial')
                window.action_finished(1, QProcess.ExitStatus.NormalExit)
            self.assertIn('fallida', window.notice.text())
            self.assertIn(str(destination), window.notice.text())
            self.assertTrue((destination / 'partial.mp4').exists())
            self.assertTrue(all(button.isEnabled() for button in window.action_buttons))
            window.close()

    def test_cancelled_download_does_nothing(self):
        with patch.object(self.gui, 'load_config', return_value=validate_config({})):
            window = self.gui.Window(language='es', monitor=False)
            with patch.object(self.gui.QFileDialog, 'getExistingDirectory', return_value=''), \
                    patch.object(window, 'start_process') as start:
                window.download_videos('presets')
                start.assert_not_called()
            self.assertIsNone(window.process)
            self.assertFalse(window.dirty)
            window.close()

    def test_list_filters_non_video_and_unsafe_names(self):
        from PySide6.QtCore import QProcess
        with patch.object(self.gui, 'load_config', return_value=validate_config({})):
            window = self.gui.Window(language='es', monitor=False)
            with patch.object(QProcess, 'start'):
                window.start_process('list_videos', '/usr/bin/adb', [])
                window.process_output = 'clip_ok.mp4\n../bad.mp4\nimage.png\n'
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
            self.assertIn('clip_ok.mp4', window.video_checks)
            self.assertNotIn('../bad.mp4', window.video_checks)
            self.assertNotIn('image.png', window.video_checks)
            self.assertFalse(window.dirty)
            window.close()

    def test_video_refresh_removes_stale_entries_and_keeps_selection(self):
        from PySide6.QtCore import QProcess
        config = validate_config({'media': ['clip_keep.mp4']})
        with patch.object(self.gui, 'load_config', return_value=config):
            window = self.gui.Window(language='es', monitor=False)
            window.add_video('clip_stale.mp4')
            with patch.object(QProcess, 'start'):
                window.start_process('list_videos', '/usr/bin/adb', [])
                window.process_output = 'clip_keep.mp4\nclip_new.mp4\n'
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
            self.assertNotIn('clip_stale.mp4', window.video_checks)
            self.assertTrue(window.video_checks['clip_keep.mp4'].isChecked())
            self.assertIn('clip_new.mp4', window.video_checks)
            self.assertFalse(window.video_checks['clip_new.mp4'].isChecked())
            self.assertFalse(window.dirty)
            self.assertTrue(all(preset in window.video_checks for preset in PRESETS))
            window.close()

    def test_refresh_failure_after_delete_keeps_other_entries(self):
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QMessageBox
        with patch.object(self.gui, 'load_config', return_value=validate_config({})), \
                patch.object(self.gui, 'detected_device', return_value=('RYUO', 'TC123')):
            window = self.gui.Window(language='es', monitor=False)
            window.add_video('clip_delete.mp4')
            window.add_video('clip_keep.mp4')
            with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Yes), \
                    patch.object(QProcess, 'start'):
                window.delete_video('clip_delete.mp4')
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                window.action_finished(1, QProcess.ExitStatus.NormalExit)
            self.assertNotIn('clip_delete.mp4', window.video_checks)
            self.assertIn('clip_keep.mp4', window.video_checks)
            self.assertIn('se borró', window.notice.text())
            self.assertIn('no se pudo recargar', window.notice.text())
            self.assertIsNone(window.process)
            window.close()

    def test_edits_during_apply_are_not_marked_saved(self):
        from PySide6.QtCore import QProcess
        with patch.object(self.gui, 'load_config', return_value=validate_config({})):
            window = self.gui.Window(language='es', monitor=False)
            settings = window.collect_settings()
            with patch.object(self.gui.Path, 'exists', return_value=True), \
                    patch.object(QProcess, 'start') as start:
                window.run_action('apply', settings)
                start.assert_called_once_with('/usr/bin/pkexec', ['/usr/local/bin/ryuo-control.py', 'apply'])
                self.assertTrue(all(not button.isEnabled() for button in window.action_buttons))
                window.brightness.setValue(55)
                window.action_finished(0, QProcess.ExitStatus.NormalExit)
                self.assertTrue(window.dirty)
                self.assertIsNone(window.process)
                self.assertTrue(all(button.isEnabled() for button in window.action_buttons))
            window.dirty = False
            window.close()


if __name__ == '__main__':
    unittest.main()
