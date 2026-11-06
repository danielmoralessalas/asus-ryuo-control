import tempfile
from pathlib import Path
import unittest

from ryuo_config import PRESETS, validate_config
from ryuo_desktop import preview_cache_path, quad_matrix, upload_plan, validated_serial


class DesktopTests(unittest.TestCase):
    def test_projection_maps_all_video_corners_without_crossing_faces(self):
        for corners in [((190, 280), (740, 485), (710, 930), (165, 705)),
                        ((740, 485), (980, 440), (935, 825), (710, 930))]:
            m = quad_matrix(corners)
            for (x, y), expected in zip(((0, 0), (1, 0), (1, 1), (0, 1)), corners):
                denominator = m[3]*x+m[7]*y+m[15]
                actual = ((m[0]*x+m[4]*y+m[12])/denominator,
                          (m[1]*x+m[5]*y+m[13])/denominator)
                for result, wanted in zip(actual, expected):
                    self.assertAlmostEqual(result, wanted)

    def test_upload_replaces_playlist_with_safe_unique_name(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'hello ; $world.mp4'
            source.write_bytes(b'mp4 fixture')
            config = validate_config({'media': list(PRESETS)})
            _, name, planned = upload_plan(source, config)
            self.assertRegex(name, r'^clip_[A-Za-z0-9_]+_[a-f0-9]{12}\.mp4$')
            self.assertEqual(planned['media'], [name])
            self.assertEqual(config['media'], list(PRESETS))

    def test_invalid_media_and_serial_never_become_adb_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                preview_cache_path('TEST', '../video.mp4', directory)
        with self.assertRaises(ValueError):
            validated_serial('device; rm -rf /')
