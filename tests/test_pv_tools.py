"""Behavior checks with synthetic images and a tiny lossless CFR video."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "pv-character-replacement" / "scripts" / "pv_tools.py"
SPEC = importlib.util.spec_from_file_location("pv_tools", SCRIPT)
pv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pv)


class PVToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pv_tools_tests_")
        self.directory = Path(self.temporary.name) / "Unicode_参考"
        self.directory.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def image(self, filename, array):
        path = self.directory / filename
        pv.write_image(path, array)
        return path

    def video(self, frames):
        path = self.directory / "原片.avi"
        h, w = frames[0].shape[:2]
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (w, h))
        self.assertTrue(writer.isOpened(), "FFV1 encoder required for the deterministic CFR fixture")
        for frame in frames:
            writer.write(frame)
        writer.release()
        return path

    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), *(str(a) for a in args)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        return result

    def test_detects_small_blink_and_repeat_a_b_a(self):
        a = np.full((64, 96, 3), 170, np.uint8)
        a[20:24, 30:40] = 20
        b = a.copy()
        b[20:23, 30:40] = 170
        clip = self.video([a, a, b, b, a, a])
        output = self.directory / "候选"
        self.run_cli("analyze", clip, "--output", output, "--start", 0, "--end", 6, "--fps", "24/1", "--cfr")
        data = json.loads((output / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual([(r["start"], r["end"], r["cel"]) for r in data["source_exposures"]], [(0, 2, "cel_000000"), (2, 4, "cel_000002"), (4, 6, "cel_000000")])
        self.assertTrue(data["requires_visual_review"])
        self.assertEqual(data["status"], "candidate_only")
        self.assertEqual(data["comparison_resolution"], [96, 64])
        self.assertEqual(len(data["assets"]), 2)
        np.testing.assert_array_equal(pv.read_image(output / data["assets"]["cel_000002"]), b)

    def test_local_and_component_gates_prevent_false_duplicate(self):
        a = np.full((400, 400, 3), 150, np.uint8)
        b = a.copy()
        b[100:103, 100:103] = 0
        metrics = pv.spatial_metrics(a, b, pv.pixel_selection(a.shape), 12, 8)
        self.assertLess(metrics["changed_fraction"], 0.00025)
        self.assertFalse(pv.is_duplicate(metrics, 0.00025, 8, 1))
        self.assertFalse(pv.is_duplicate(metrics, 1, 100, 0.001))
        self.assertEqual(metrics["largest_changed_regions"][0], {"x": 100, "y": 100, "width": 3, "height": 3, "pixels": 9})

    def test_mask_excludes_subtitle_but_not_character(self):
        a = np.full((64, 96, 3), 150, np.uint8)
        b = a.copy()
        b[10:40, 80:85] = 255
        c = b.copy()
        c[20:23, 30:40] = 0
        clip = self.video([a, b, c])
        mask = np.zeros((64, 96), np.uint8)
        mask[:, 76:] = 255
        mask_path = self.image("字幕遮罩.png", mask)
        output = self.directory / "masked"
        self.run_cli("analyze", clip, "--output", output, "--start", 0, "--end", 3, "--fps", "24/1", "--cfr", "--ignore-mask", mask_path)
        data = json.loads((output / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual([(r["start"], r["end"]) for r in data["source_exposures"]], [(0, 2), (2, 3)])

    def test_requires_cfr_and_matching_fps(self):
        clip = self.video([np.zeros((32, 32, 3), np.uint8)])
        output = self.directory / "out"
        self.run_cli("analyze", clip, "--output", output, "--start", 0, "--end", 1, "--fps", "24/1", expected=2)
        self.run_cli("analyze", clip, "--output", output, "--start", 0, "--end", 1, "--fps", "30/1", "--cfr", expected=2)
        self.assertFalse(output.exists())

    def test_rejects_empty_mask_and_bad_roi(self):
        with self.assertRaises(pv.InputError):
            pv.pixel_selection((20, 30, 3), ignore_mask=np.ones((20, 30), np.uint8))
        with self.assertRaises(pv.InputError):
            pv.pixel_selection((20, 30, 3), (29, 0, 5, 4))
        with self.assertRaises(pv.InputError):
            pv.pixel_selection((20, 30, 3), ignore_mask=np.zeros((10, 15), np.uint8))

    def test_frame_range_is_absolute_and_end_exclusive(self):
        a = np.full((32, 32, 3), 140, np.uint8)
        b = np.full((32, 32, 3), 30, np.uint8)
        clip = self.video([a, b, b, a])
        output = self.directory / "interval"
        self.run_cli("analyze", clip, "--output", output, "--start", 1, "--end", 3, "--fps", "24/1", "--cfr")
        data = json.loads((output / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(data["source_exposures"], [{"start": 1, "end": 3, "cel": "cel_000001"}])

    def test_diff_is_spatial_and_does_not_align(self):
        a = np.full((40, 60, 3), 180, np.uint8)
        b = a.copy()
        b[15:20, 22:27] = [20, 100, 220]
        before, after = self.image("原A.png", a), self.image("原B.png", b)
        output = self.directory / "diff"
        self.run_cli("diff", before, after, "--output", output, "--roi", "18,12,15,15")
        actual = pv.read_image(output / "rgb_absdiff.png")
        expected = np.clip(np.abs(a.astype(np.int16) - b.astype(np.int16)) * 4, 0, 255).astype(np.uint8)
        np.testing.assert_array_equal(actual, expected)
        self.assertTrue((output / "comparison.png").is_file())
        data = json.loads((output / "difference.json").read_text(encoding="utf-8"))
        self.assertFalse(data["alignment_applied"])
        self.assertEqual(data["metrics"]["changed_pixels"], 25)

    def test_diff_rejects_size_mismatch_and_nonempty_output(self):
        before = self.image("a.png", np.zeros((20, 30, 3), np.uint8))
        after = self.image("b.png", np.zeros((20, 31, 3), np.uint8))
        output = self.directory / "diff"
        self.run_cli("diff", before, after, "--output", output, expected=2)
        self.assertFalse(output.exists())
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        self.run_cli("diff", before, before, "--output", output, expected=2)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def timeline(self):
        for name in ("a.png", "b.png", "c.png"):
            self.image(name, np.zeros((4, 4, 3), np.uint8))
        return {"version": 1, "fps": "24/1", "start": 100, "end": 112, "mode": "strict_source", "source_exposures": [{"start": 100, "end": 104, "cel": "source_a"}, {"start": 104, "end": 108, "cel": "source_b"}, {"start": 108, "end": 112, "cel": "source_a"}], "output_exposures": [{"start": 100, "end": 104, "cel": "replacement_a"}, {"start": 104, "end": 108, "cel": "replacement_b"}, {"start": 108, "end": 112, "cel": "replacement_a"}], "assets": {"replacement_a": "a.png", "replacement_b": "b.png", "replacement_c": "c.png"}}

    def test_strict_timeline_accepts_ids_different_from_source(self):
        data = self.timeline()
        report = pv.validate_timeline_document(data, self.directory)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["status"], "structurally_valid")
        self.assertTrue(report["requires_visual_review"])

    def test_timeline_gap_overlap_and_missing_asset_fail(self):
        original = self.timeline()
        for bad_start in (103, 105):
            data = copy.deepcopy(original)
            data["output_exposures"][1]["start"] = bad_start
            self.assertFalse(pv.validate_timeline_document(data, self.directory)["valid"])
        original["assets"]["replacement_a"] = "missing.png"
        self.assertFalse(pv.validate_timeline_document(original, self.directory)["valid"])

    def test_strict_timeline_rejects_split_hold_and_reassigned_repeat(self):
        data = self.timeline()
        data["output_exposures"][:1] = [{"start": 100, "end": 102, "cel": "replacement_a"}, {"start": 102, "end": 104, "cel": "replacement_c"}]
        self.assertFalse(pv.validate_timeline_document(data, self.directory)["valid"])
        data = self.timeline()
        data["output_exposures"][-1]["cel"] = "replacement_c"
        self.assertFalse(pv.validate_timeline_document(data, self.directory)["valid"])

    def test_strict_timeline_rejects_merging_different_source_holds(self):
        data = self.timeline()
        data["output_exposures"] = [{"start": 100, "end": 112, "cel": "replacement_a"}]
        self.assertFalse(pv.validate_timeline_document(data, self.directory)["valid"])

    def test_adapted_motion_allows_inserted_drawings_but_reports_shifted_anchors(self):
        data = self.timeline()
        data["mode"] = "adapted_motion"
        data["output_exposures"][:1] = [{"start": 100, "end": 102, "cel": "replacement_a"}, {"start": 102, "end": 104, "cel": "replacement_c"}]
        report = pv.validate_timeline_document(data, self.directory)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["anchor_offsets"], [])
        data["output_exposures"][1]["end"] = 105
        data["output_exposures"][2]["start"] = 105
        report = pv.validate_timeline_document(data, self.directory)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["status"], "valid_with_warnings")
        self.assertEqual(report["anchor_offsets"], [{"source_boundary": 104, "nearest_output_boundary": 105, "delta_frames": 1}])
        data["output_exposures"][-1]["end"] = 111
        self.assertFalse(pv.validate_timeline_document(data, self.directory)["valid"])

    def test_timeline_rejects_absolute_escape_and_bool_frames(self):
        for value in ("../outside.png", "C:/secret.png"):
            data = self.timeline()
            data["assets"]["replacement_a"] = value
            self.assertFalse(pv.validate_timeline_document(data, self.directory)["valid"])
        data = self.timeline()
        data["output_exposures"][0]["start"] = True
        self.assertFalse(pv.validate_timeline_document(data, self.directory)["valid"])

    def test_validate_cli_report_and_no_overwrite(self):
        path = self.directory / "timeline.json"
        path.write_text(json.dumps(self.timeline()), encoding="utf-8")
        report_path = self.directory / "report.json"
        self.run_cli("validate-timeline", path, "--report", report_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertTrue(report["valid"])
        self.run_cli("validate-timeline", path, "--report", report_path, expected=2)
        broken = self.timeline()
        broken["assets"] = {}
        path.write_text(json.dumps(broken), encoding="utf-8")
        self.run_cli("validate-timeline", path, expected=1)

    def test_rational_fps_rejects_ambiguous_strings(self):
        self.assertAlmostEqual(float(pv.rational_fps("24000/1001")), 23.976023976)
        for value in (24, "24", "0/1", "24/0", "-24/1", None):
            with self.assertRaises(pv.InputError):
                pv.rational_fps(value)


if __name__ == "__main__":
    unittest.main()
