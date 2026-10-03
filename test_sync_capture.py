import tempfile
import unittest
from pathlib import Path

from sync_capture import QtmEvent, SyncRecorder, parse_qtm_xml, safe_name
from calibration import CalibrationProfile, robust_stats


START = b'''<?xml version="1.0" encoding="UTF-8"?><CaptureStart>
<Name VALUE="CAST 01"/><PacketID VALUE="42"/><HostName VALUE="QTM-PC"/>
<ProcessID VALUE="123"/></CaptureStart>'''
STOP = b'''<?xml version="1.0" encoding="UTF-8"?><CaptureStop RESULT="SUCCESS">
<Name VALUE="CAST 01"/><PacketID VALUE="43"/></CaptureStop>'''


class SyncCaptureTests(unittest.TestCase):
    def test_calibration_profile(self):
        profile = CalibrationProfile(stability_stdev_threshold=2)
        profile.set_baseline("S1", [100, 101, 100, 99, 100], 3)
        profile.add_point("S1", 0, [100, 101, 100], "unknown", 3)
        profile.add_point("S1", 20, [120, 121, 120], "dorsiflexion", 3)
        profile.add_point("S1", -20, [80, 81, 80], "plantarflexion", 3)
        self.assertAlmostEqual(profile.delta("S1", 110), 10, places=3)
        self.assertAlmostEqual(profile.angle("S1", 110), 10, places=3)
        self.assertEqual(profile.metrics("S1")["point_count"], 3)

    def test_calibration_rejects_unstable_window(self):
        profile = CalibrationProfile(stability_stdev_threshold=1)
        with self.assertRaises(ValueError):
            profile.set_baseline("S1", [0, 100], 3)
    def test_parse_start_and_stop(self):
        start = parse_qtm_xml(START, 100)
        stop = parse_qtm_xml(STOP, 200)
        self.assertEqual((start.kind, start.name, start.packet_id), ("start", "CAST 01", "42"))
        self.assertEqual((stop.kind, stop.result), ("stop", "SUCCESS"))

    def test_ignores_bad_or_unrelated_xml(self):
        self.assertIsNone(parse_qtm_xml(b"not xml"))
        self.assertIsNone(parse_qtm_xml(b"<Other/>"))

    def test_safe_filename(self):
        self.assertEqual(safe_name('CAST:01/left'), "CAST_01_left")

    def test_recording_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {
                "channels": [
                    {"name": "S1", "receiver_id": "0051", "serial_port": "COM8"},
                    {"name": "S2", "receiver_id": "0053", "serial_port": "COM5"},
                ],
                "baudrate": 115200,
                "udp_bind": "127.0.0.1",
                "udp_port": 8989,
                "pretrigger_seconds": 1.0,
                "output_directory": temp,
                "flush_interval_seconds": 0,
                "sampling_interval_ms": 20,
            }
            recorder = SyncRecorder(config, log=lambda _: None)
            recorder.add_sample("650", "S1")
            recorder.handle_event(parse_qtm_xml(START))
            recorder.add_sample("651", "S1")
            recorder.add_sample("702", "S2")
            recorder.add_sample("bad", "S2")
            recorder.handle_event(parse_qtm_xml(STOP))
            folders = [p for p in Path(temp).iterdir() if p.is_dir()]
            self.assertEqual(len(folders), 1)
            csv_text = (folders[0] / "elastreme_raw.csv").read_text(encoding="utf-8-sig")
            self.assertIn("650", csv_text)
            self.assertIn("651", csv_text)
            self.assertIn("S2", csv_text)
            self.assertIn("702", csv_text)
            session_text = (folders[0] / "session.json").read_text(encoding="utf-8")
            self.assertIn('"receiver_id": "0051"', session_text)
            self.assertIn('"receiver_id": "0053"', session_text)
            self.assertIn('"measured_host_receive_rate_hz_by_channel"', session_text)

    def test_recording_includes_calibrated_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = CalibrationProfile(stability_stdev_threshold=2)
            profile.set_baseline("S1", [100, 100, 101], 3)
            profile.add_point("S1", 0, [100, 100, 101], "unknown", 3)
            profile.add_point("S1", 20, [120, 120, 121], "unknown", 3)
            config = {
                "channels": [{"name": "S1", "serial_port": "COM3"}],
                "baudrate": 115200, "udp_bind": "127.0.0.1", "udp_port": 8989,
                "pretrigger_seconds": 1.0, "output_directory": temp,
                "flush_interval_seconds": 0, "sampling_interval_ms": 20,
            }
            recorder = SyncRecorder(config, log=lambda _: None)
            recorder.calibration = profile
            recorder.handle_event(parse_qtm_xml(START))
            recorder.add_sample("110", "S1")
            recorder.handle_event(parse_qtm_xml(STOP))
            folder = next(Path(temp).iterdir())
            rows = (folder / "elastreme_raw.csv").read_text(encoding="utf-8-sig").splitlines()
            self.assertIn("baseline_delta", rows[0])
            self.assertIn("calibrated_angle_deg", rows[0])
            self.assertIn(",10.000000,calibrated", rows[-1])


if __name__ == "__main__":
    unittest.main()
