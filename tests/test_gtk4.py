"""GTK4 behavior checks; run with a GTK display (Broadway is sufficient)."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, Gio, GLib, Gtk
    Gtk.init_check()
    AVAILABLE = Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'GTK4 display unavailable')
class GtkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('gtk_frontend', ROOT / 'ryuo-gtk.py')
        cls.gui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gui)
        cls.app = Gtk.Application(application_id='io.github.ryuocontrol.Tests', flags=Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)
        cls.provider = Gtk.CssProvider()
        cls.provider.load_from_data(cls.gui.STYLE.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), cls.provider,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = self.gui.Window(self.app, monitor=False,
            preferences=self.gui.Preferences(Path(self.temp.name)/'preferences.ini'),
            cache_root=Path(self.temp.name)/'cache', language='en')

    def tearDown(self):
        self.window.dirty = False
        self.window.shutdown()
        self.temp.cleanup()
        self.gui.set_language('en')

    def test_language_switch_preserves_unsaved_settings_and_slots(self):
        window = self.window
        window.swap_slots(0, 5)
        window.brightness.set_value(42)
        before = window.collect_settings()
        window.language_choice.set_active_id('es')
        self.assertEqual(window.collect_settings(), before)
        self.assertTrue(window.dirty)
        self.assertEqual(window.nav[1].get_label(), 'Pantalla')
        self.assertEqual(window.preferences.language(), 'es')
        window.language_choice.set_active_id('en')
        self.assertEqual(window.nav[1].get_label(), 'Display')
        self.assertEqual(window.collect_settings(), before)

    def test_drag_rejects_external_data_and_swaps_only_own_slots(self):
        preview = self.window.device_preview
        before = list(self.window.sensor_assignments)
        self.assertFalse(preview.drop_slot(None, 'other-window:0', 0, 0, 1))
        self.assertFalse(preview.drop_slot(None, f'{preview.token}:9', 0, 0, 1))
        self.assertEqual(self.window.sensor_assignments, before)
        self.assertTrue(preview.drop_slot(None, f'{preview.token}:0', 0, 0, 1))
        self.assertEqual(self.window.sensor_assignments[1], before[0])
        self.assertEqual(self.window.sensor_assignments[0], before[1])

    def test_hidden_slots_are_preserved_in_saved_configuration(self):
        self.window.clear_slots()
        self.assertEqual(self.window.collect_settings()['sensors'], ['']*6)
        self.window.select_slot(3)
        self.window.editor_sensor.set_active_id('GPU Power')
        self.assertEqual(self.window.collect_settings()['sensors'], ['', '', '', 'GPU Power', '', ''])

    def test_upload_selects_before_transfer_then_applies_in_single_chain(self):
        window = self.window
        source = Path(self.temp.name)/'new video.mp4'
        source.write_bytes(b'video fixture')
        window.serial.set_text('TEST123')
        window.helper_available = Mock(return_value=True)
        window.start_process = Mock()
        window.begin_upload(source)
        name = window.upload['name']
        self.assertEqual(window.selected_media(), [name])
        self.assertEqual(window.start_process.call_args.args[0], 'upload_mkdir')
        window.complete_action('upload_mkdir', True, '')
        self.assertEqual(window.start_process.call_args.args[0], 'upload')
        window.complete_action('upload', True, '')
        args = window.start_process.call_args.args
        self.assertEqual(args[0], 'apply_upload')
        self.assertEqual(args[2]['media'], [name])
        window.complete_action('apply_upload', True, '', args[2])
        self.assertFalse(window.dirty)
        self.assertEqual(window.local_video_sources[name], source)

    def test_upload_failure_restores_playlist(self):
        window = self.window
        before = window.selected_media()
        source = Path(self.temp.name)/'video.mp4'
        source.write_bytes(b'video')
        window.serial.set_text('TEST123')
        window.helper_available = Mock(return_value=True)
        window.start_process = Mock()
        window.begin_upload(source)
        uploaded = window.upload['name']
        window.complete_action('upload', False, 'failure')
        self.assertEqual(window.selected_media(), before)
        self.assertNotIn(uploaded, window.video_checks)

    def test_presets_and_saved_playlist_cannot_be_deleted(self):
        window = self.window
        window.message = Mock()
        window.start_process = Mock()
        window.delete_video(self.gui.PRESETS[0])
        window.start_process.assert_not_called()
        self.assertIsNone(window.message.call_args.kwargs.get('confirm'))
        window.add_video('clip_saved.mp4')
        with patch.object(self.gui, 'load_config', return_value={'media': ['clip_saved.mp4']}):
            window.delete_video('clip_saved.mp4')
        self.assertEqual(len(window.message.call_args.args), 2)
        window.start_process.assert_not_called()

    def test_delete_refresh_reconciles_entries_and_preserves_selection(self):
        window = self.window
        window.add_video('clip_old.mp4')
        window.add_video('clip_keep.mp4')
        window.video_checks['clip_keep.mp4'].set_active(True)
        window.delete_name, window.delete_serial = 'clip_old.mp4', 'TEST123'
        window.start_process = Mock()
        window.complete_action('delete_video', True, '')
        self.assertNotIn('clip_old.mp4', window.video_checks)
        self.assertEqual(window.start_process.call_args.args[0], 'refresh_after_delete')
        window.complete_action('refresh_after_delete', True, 'clip_keep.mp4\nclip_new.mp4\n../bad.mp4\n')
        self.assertTrue(window.video_checks['clip_keep.mp4'].get_active())
        self.assertIn('clip_new.mp4', window.video_checks)
        self.assertNotIn('../bad.mp4', window.video_checks)

    def test_failed_cache_refresh_keeps_previous_copy(self):
        target = Path(self.temp.name)/'cached.mp4'
        target.write_bytes(b'valid original')
        partial = Path(self.temp.name)/'.partial'
        partial.write_bytes(b'partial')
        self.window.preview_fetch = self.gui.PRESETS[0], target, partial
        self.window.finish_preview_fetch(False)
        self.assertEqual(target.read_bytes(), b'valid original')
        self.assertFalse(partial.exists())

    def test_async_process_reports_success_and_releases_busy_state(self):
        self.window.start_process('logs', ['/usr/bin/printf', 'hello'])
        context = GLib.MainContext.default()
        import time
        deadline = time.monotonic()+5
        while self.window.process and time.monotonic() < deadline:
            context.iteration(True)
        self.assertIsNone(self.window.process)
        buffer = self.window.logs.get_buffer()
        self.assertIn('hello', buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True))
        self.assertTrue(self.window.language_choice.get_sensitive())

    def test_preferences_are_compatible_with_qsettings_ini(self):
        path = Path(self.temp.name)/'shared.ini'
        path.write_text('[General]\nlanguage=es\nother=value\n')
        preferences = self.gui.Preferences(path)
        self.assertEqual(preferences.language(), 'es')
        preferences.save('en')
        self.assertEqual(preferences.language(), 'en')
        self.assertIn('other = value', path.read_text())

    @unittest.skipUnless(os.environ.get('RYUO_TEST_VIDEO'), 'Set RYUO_TEST_VIDEO to a local test video')
    def test_video_decodes_projects_and_survives_language_change(self):
        import time
        window = self.window
        window.nav[1].set_active(True)
        window.present()
        window.play_file(Path(os.environ['RYUO_TEST_VIDEO']))
        media = window.device_preview.media
        context = GLib.MainContext.default()
        deadline = time.monotonic()+8
        while time.monotonic() < deadline and (not media.is_prepared() or media.get_timestamp() == 0):
            while context.pending():
                context.iteration(False)
            time.sleep(.01)
        self.assertIsNone(media.get_error())
        self.assertTrue(media.is_prepared())
        self.assertGreater(media.get_timestamp(), 0)
        window.language_choice.set_active_id('es')
        self.assertIs(window.device_preview.media, media)
        self.assertTrue(media.get_playing())
        deadline = time.monotonic()+.3
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(.01)
        # Broadway without a browser client has no compositor size negotiation.
        window.get_child().allocate(1100, 1000, -1, None)
        window.device_preview.layout_cards(window.device_preview, None)
        window.get_child().allocate(1100, 1000, -1, None)
        window.device_preview.surface.receive_frame(media)
        self.assertIsNotNone(window.device_preview.surface.frame)
        snapshot = Gtk.Snapshot()
        window.snapshot_child(window.get_child(), snapshot)
        node = snapshot.to_node()
        self.assertIsNotNone(node)
        texture = window.get_renderer().render_texture(node, None)
        self.assertTrue(texture.save_to_png('/tmp/ryuo-control-gtk-video-preview.png'))
        window.start_preview()
        self.assertFalse(media.get_playing())
