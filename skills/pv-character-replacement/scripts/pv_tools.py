#!/usr/bin/env python3
"""Conservative PV cel candidates, spatial difference boards, and timeline checks.

No image generation, image registration, optical flow, or character deformation.
An analysis result is a candidate list, never a visual approval.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from fractions import Fraction
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw


class InputError(ValueError):
    """Actionable input or media error."""


def rational_fps(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9]\d*/[1-9]\d*", value):
        raise InputError("fps must be a positive rational string, for example 24/1 or 24000/1001")
    return Fraction(value)


def parse_roi(value):
    try:
        parts = tuple(int(v) for v in value.split(","))
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError("ROI must be x,y,width,height") from exc
    if len(parts) != 4 or min(parts[:2]) < 0 or min(parts[2:]) <= 0:
        raise argparse.ArgumentTypeError("ROI must be nonnegative x,y and positive width,height")
    return parts


def read_image(path, grayscale=False):
    """Use Python file I/O + imdecode, including non-ASCII Windows paths."""
    path = Path(path)
    try:
        data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        flag = cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR
        image = cv2.imdecode(data, flag)
    except (OSError, cv2.error) as exc:
        raise InputError(f"Cannot read image: {path}: {exc}") from exc
    if image is None:
        raise InputError(f"Cannot decode image: {path}")
    return image


def write_image(path, image):
    path = Path(path)
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise InputError(f"Cannot encode image: {path}")
    path.write_bytes(encoded.tobytes())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def new_output_directory(path):
    path = Path(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise InputError(f"Output must be new or empty; refusing to overwrite: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_report_path(path):
    if path is None:
        return None
    path = Path(path)
    if path.exists():
        raise InputError(f"Report already exists; refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def pixel_selection(shape, roi=None, ignore_mask=None):
    h, w = shape[:2]
    selected = np.ones((h, w), dtype=bool)
    if roi is not None:
        x, y, rw, rh = roi
        if x < 0 or y < 0 or rw <= 0 or rh <= 0 or x + rw > w or y + rh > h:
            raise InputError(f"ROI {roi} lies outside the {w}x{h} image")
        selected[:] = False
        selected[y:y + rh, x:x + rw] = True
    if ignore_mask is not None:
        if ignore_mask.shape != (h, w):
            raise InputError("Ignore mask must have exactly the full frame width and height")
        selected &= ignore_mask == 0
    if not selected.any():
        raise InputError("ROI and ignore mask exclude every pixel")
    return selected


def spatial_metrics(before, after, selected, pixel_threshold=12, grid=8):
    """All decisions use native-resolution pixels; no registration or downsampling."""
    if before.shape != after.shape:
        raise InputError("Image dimensions differ; alignment/resizing is deliberately not automatic")
    if grid < 1 or pixel_threshold < 0 or pixel_threshold > 255:
        raise InputError("grid must be positive and pixel_threshold must be in 0..255")
    delta = np.abs(before.astype(np.int16) - after.astype(np.int16))
    changed = (np.max(delta, axis=2) > pixel_threshold) & selected
    changed_count = int(changed.sum())
    component_count, _, stats, _ = cv2.connectedComponentsWithStats(changed.astype(np.uint8), 8)
    components = []
    for label in range(1, component_count):
        x, y, w, h, area = (int(v) for v in stats[label])
        components.append({"x": x, "y": y, "width": w, "height": h, "pixels": area})
    components.sort(key=lambda c: (-c["pixels"], c["y"], c["x"]))
    yy, xx = np.nonzero(selected)
    xs = np.unique(np.linspace(int(xx.min()), int(xx.max()) + 1, grid + 1, dtype=int))
    ys = np.unique(np.linspace(int(yy.min()), int(yy.max()) + 1, grid + 1, dtype=int))
    max_local = 0.0
    max_local_cell = None
    for y0, y1 in zip(ys[:-1], ys[1:]):
        for x0, x1 in zip(xs[:-1], xs[1:]):
            denominator = int(selected[y0:y1, x0:x1].sum())
            if denominator == 0:
                continue
            rate = float(changed[y0:y1, x0:x1].sum()) / denominator
            if rate > max_local:
                max_local = rate
                max_local_cell = [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]
    return {
        "compared_pixels": int(selected.sum()),
        "changed_pixels": changed_count,
        "changed_fraction": changed_count / int(selected.sum()),
        "largest_component_pixels": components[0]["pixels"] if components else 0,
        "max_local_changed_fraction": max_local,
        "max_local_cell_xywh": max_local_cell,
        "largest_changed_regions": components[:12],
    }


def is_duplicate(metrics, max_fraction, max_component, max_local_fraction):
    return (
        metrics["changed_fraction"] <= max_fraction
        and metrics["largest_component_pixels"] <= max_component
        and metrics["max_local_changed_fraction"] <= max_local_fraction
    )


@contextmanager
def open_video(path):
    """Open Unicode paths directly; use a Python-managed copy if a backend cannot."""
    path = Path(path)
    if not path.is_file():
        raise InputError(f"Video does not exist: {path}")
    cap = cv2.VideoCapture(str(path))
    temporary = None
    if not cap.isOpened():
        cap.release()
        temporary = tempfile.TemporaryDirectory(prefix="pv_cel_input_")
        copied = Path(temporary.name) / ("input" + path.suffix)
        shutil.copyfile(path, copied)
        cap = cv2.VideoCapture(str(copied))
    try:
        if not cap.isOpened():
            raise InputError(f"Cannot open video with the installed OpenCV backend: {path}")
        yield cap
    finally:
        cap.release()
        if temporary is not None:
            temporary.cleanup()


def analyze(args):
    fps = rational_fps(args.fps)
    if not args.cfr:
        raise InputError("--cfr is required: first confirm that the source is constant-frame-rate")
    if args.start < 0 or args.end <= args.start:
        raise InputError("Require 0 <= start < end; end is exclusive")
    if not 0 <= args.pixel_threshold <= 255 or args.grid < 1:
        raise InputError("pixel-threshold must be 0..255 and grid must be positive")
    if not 0 <= args.max_fraction <= 1 or not 0 <= args.max_local_fraction <= 1 or args.max_component < 0:
        raise InputError("Fraction thresholds must be 0..1 and max-component must be nonnegative")
    ignore = read_image(args.ignore_mask, grayscale=True) if args.ignore_mask else None
    representatives, exposures, decisions = [], [], []
    with open_video(args.input) as cap:
        metadata_fps = float(cap.get(cv2.CAP_PROP_FPS))
        if metadata_fps <= 0 or abs(metadata_fps - float(fps)) > 0.002:
            raise InputError(f"Video reports {metadata_fps:g} fps, unlike requested {args.fps}; use an explicit CFR source")
        selected = None
        output = None
        for frame_index in range(args.end):
            ok, frame = cap.read()
            if not ok:
                raise InputError(f"Video ended or decoding failed at frame {frame_index}, before requested end {args.end}")
            if frame_index < args.start:
                continue
            if selected is None:
                selected = pixel_selection(frame.shape, args.roi, ignore)
                output = new_output_directory(args.output)
                (output / "representatives").mkdir(exist_ok=True)
            elif frame.shape[:2] != selected.shape:
                raise InputError("Decoded frame dimensions changed inside the selected shot")
            candidates = []
            comparisons = []
            for rep in representatives:
                metrics = spatial_metrics(rep["image"], frame, selected, args.pixel_threshold, args.grid)
                item = {"cel": rep["cel"], "metrics": metrics}
                comparisons.append(item)
                if is_duplicate(metrics, args.max_fraction, args.max_component, args.max_local_fraction):
                    candidates.append(item)
            if candidates:
                candidates.sort(key=lambda c: (c["metrics"]["changed_pixels"], c["metrics"]["largest_component_pixels"], c["cel"]))
                chosen = candidates[0]
                cel = chosen["cel"]
                decision = {"frame": frame_index, "cel": cel, "decision": "candidate_repeat", "metrics": chosen["metrics"]}
            else:
                cel = f"cel_{frame_index:06d}"
                asset = f"representatives/{cel}.png"
                write_image(output / asset, frame)
                representatives.append({"cel": cel, "frame": frame_index, "path": asset, "image": frame.copy()})
                nearest = min(comparisons, key=lambda c: (c["metrics"]["changed_pixels"], c["cel"])) if comparisons else None
                decision = {"frame": frame_index, "cel": cel, "decision": "candidate_new", "nearest_previous": nearest}
            decisions.append(decision)
            if exposures and exposures[-1]["cel"] == cel:
                exposures[-1]["end"] = frame_index + 1
            else:
                exposures.append({"start": frame_index, "end": frame_index + 1, "cel": cel})
    manifest = {
        "version": 1,
        "status": "candidate_only",
        "requires_visual_review": True,
        "input": str(Path(args.input).resolve()),
        "fps": args.fps,
        "cfr_asserted_by_user": True,
        "metadata_fps": metadata_fps,
        "start": args.start,
        "end": args.end,
        "roi_xywh": list(args.roi) if args.roi else None,
        "ignore_mask": str(Path(args.ignore_mask).resolve()) if args.ignore_mask else None,
        "comparison_resolution": [int(selected.shape[1]), int(selected.shape[0])],
        "thresholds": {
            "pixel_threshold": args.pixel_threshold,
            "max_changed_fraction": args.max_fraction,
            "max_component_pixels": args.max_component,
            "max_local_changed_fraction": args.max_local_fraction,
            "grid": args.grid,
            "duplicate_rule": "all three limits must pass against a prior representative",
        },
        "source_exposures": exposures,
        "assets": {r["cel"]: r["path"] for r in representatives},
        "representative_frames": {r["cel"]: r["frame"] for r in representatives},
        "decisions": decisions,
        "limitations": [
            "Selected frame interval must already be one shot. This command does not detect cuts.",
            "CFR is the user's assertion plus an FPS metadata check, not a timestamp audit.",
            "Masks/ROIs may hide real changes. Review their coverage and every candidate grouping.",
            "Thresholds need checking against source compression and the smallest meaningful detail.",
            "This is a source-cel candidate manifest, not a finished replacement timeline.",
        ],
    }
    write_json(output / "candidates.json", manifest)
    return {"status": "candidate_only", "requires_visual_review": True, "representatives": len(representatives), "exposures": len(exposures), "manifest": str(output / "candidates.json")}


def difference_board(args):
    before, after = read_image(args.before), read_image(args.after)
    if before.shape != after.shape:
        raise InputError("Image dimensions differ; do not resize or align away actual motion")
    if not np.isfinite(args.gain) or args.gain <= 0:
        raise InputError("Difference gain must be finite and positive")
    selected = pixel_selection(before.shape, args.roi)
    h, w = before.shape[:2]
    rgb_a, rgb_b = cv2.cvtColor(before, cv2.COLOR_BGR2RGB), cv2.cvtColor(after, cv2.COLOR_BGR2RGB)
    difference = np.clip(np.abs(rgb_a.astype(np.int16) - rgb_b.astype(np.int16)) * args.gain, 0, 255).astype(np.uint8)
    edge_a = cv2.Canny(cv2.cvtColor(before, cv2.COLOR_BGR2GRAY), 42, 110) > 0
    edge_b = cv2.Canny(cv2.cvtColor(after, cv2.COLOR_BGR2GRAY), 42, 110) > 0
    overlay = np.full_like(rgb_a, 244)
    overlay[edge_a] = (40, 120, 230)
    overlay[edge_b] = (235, 125, 30)
    overlay[edge_a & edge_b] = (65, 65, 65)
    panels = [rgb_a, rgb_b, difference, overlay]
    labels = ["SOURCE A / BEFORE", "SOURCE B / AFTER", f"ABS(RGB A - RGB B) x {args.gain:g}", "CONTOURS: old BLUE / new ORANGE / shared GRAY"]
    label_height = 30
    zoom_height = min(max(h, 200), 500) if args.roi else 0
    board = Image.new("RGB", (2 * w, 2 * (h + label_height) + (zoom_height + label_height if args.roi else 0)), "white")
    draw = ImageDraw.Draw(board)
    for index, (panel, label) in enumerate(zip(panels, labels)):
        x, y = (index % 2) * w, (index // 2) * (h + label_height)
        draw.text((x + 8, y + 8), label, fill="black")
        board.paste(Image.fromarray(panel), (x, y + label_height))
    if args.roi:
        x, y, rw, rh = args.roi
        row_y = 2 * (h + label_height)
        slot_width = (2 * w) // 4
        for index, panel in enumerate(panels):
            left = index * slot_width
            draw.text((left + 5, row_y + 8), ["ROI A", "ROI B", "ROI RGB DIFF", "ROI CONTOURS"][index], fill="black")
            zoom = Image.fromarray(panel[y:y + rh, x:x + rw])
            scale = min(slot_width / rw, zoom_height / rh)
            zoom = zoom.resize((max(1, round(rw * scale)), max(1, round(rh * scale))), Image.Resampling.NEAREST)
            board.paste(zoom, (left + (slot_width - zoom.width) // 2, row_y + label_height))
    output = new_output_directory(args.output)
    board.save(output / "comparison.png")
    Image.fromarray(difference).save(output / "rgb_absdiff.png")
    Image.fromarray(overlay).save(output / "contour_overlay.png")
    metrics = spatial_metrics(before, after, selected, args.pixel_threshold, args.grid)
    report = {"version": 1, "status": "spatial_reference_only", "before": str(Path(args.before).resolve()), "after": str(Path(args.after).resolve()), "width": w, "height": h, "roi_xywh": list(args.roi) if args.roi else None, "gain": args.gain, "pixel_threshold": args.pixel_threshold, "grid": args.grid, "metrics": metrics, "alignment_applied": False, "requires_visual_review": True}
    write_json(output / "difference.json", report)
    return {"status": "spatial_reference_only", "comparison": str(output / "comparison.png"), "rgb_absdiff": str(output / "rgb_absdiff.png"), "contour_overlay": str(output / "contour_overlay.png")}


def is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_timeline_document(document, base_directory):
    errors, warnings, offsets = [], [], []
    if not isinstance(document, dict):
        return {"valid": False, "status": "invalid", "errors": ["Top-level JSON must be an object"], "warnings": [], "requires_visual_review": True}
    if not is_integer(document.get("version")) or document.get("version") != 1:
        errors.append("version must be integer 1")
    try:
        rational_fps(document.get("fps"))
    except InputError as exc:
        errors.append(str(exc))
    start, end = document.get("start"), document.get("end")
    valid_range = is_integer(start) and is_integer(end) and 0 <= start < end
    if not valid_range:
        errors.append("Require integer 0 <= start < end (end exclusive)")
    mode = document.get("mode")
    if mode not in ("strict_source", "adapted_motion"):
        errors.append("mode must be strict_source or adapted_motion")

    def check_runs(field):
        runs = document.get(field)
        if not isinstance(runs, list) or not runs:
            errors.append(f"{field} must be a nonempty list")
            return None
        usable = True
        expected = start if valid_range else None
        previous_cel = None
        for index, run in enumerate(runs):
            label = f"{field}[{index}]"
            if not isinstance(run, dict):
                errors.append(f"{label} must be an object")
                usable = False
                continue
            a, b, cel = run.get("start"), run.get("end"), run.get("cel")
            if not is_integer(a) or not is_integer(b) or a >= b:
                errors.append(f"{label} requires integer start < end")
                usable = False
                continue
            if not isinstance(cel, str) or not cel.strip():
                errors.append(f"{label}.cel must be a nonempty string")
                usable = False
                continue
            if valid_range and (a < start or b > end):
                errors.append(f"{label} is outside [{start}, {end})")
            if expected is not None and a != expected:
                kind = "gap" if a > expected else "overlap or out-of-order run"
                errors.append(f"{label} has {kind}: expected start {expected}, got {a}")
            if previous_cel == cel:
                errors.append(f"{label} repeats adjacent cel {cel}; merge this redundant run")
            expected, previous_cel = b, cel
        if valid_range and expected != end:
            errors.append(f"{field} ends at {expected}, expected {end}")
        return runs if usable else None

    source = check_runs("source_exposures")
    output = check_runs("output_exposures")
    assets = document.get("assets")
    if not isinstance(assets, dict):
        errors.append("assets must map output cel IDs to relative file paths")
        assets = {}
    base = Path(base_directory).resolve()
    for cel, relative in assets.items():
        if not isinstance(cel, str) or not cel or not isinstance(relative, str) or not relative:
            errors.append("assets must map nonempty strings to nonempty path strings")
            continue
        win = PureWindowsPath(relative)
        path = Path(relative.replace("\\", "/"))
        if path.is_absolute() or win.is_absolute() or win.drive:
            errors.append(f"assets[{cel}] must be a relative path")
            continue
        resolved = (base / path).resolve()
        if not resolved.is_relative_to(base):
            errors.append(f"assets[{cel}] escapes the timeline directory")
        elif not resolved.is_file():
            errors.append(f"Missing asset for {cel}: {relative}")
    if output is not None:
        for cel in sorted({r["cel"] for r in output}):
            if cel not in assets:
                errors.append(f"No asset mapping for output cel {cel}")
    if source is not None and output is not None and mode == "strict_source":
        source_bounds = [(r["start"], r["end"]) for r in source]
        output_bounds = [(r["start"], r["end"]) for r in output]
        if source_bounds != output_bounds:
            errors.append("strict_source requires identical source/output run boundaries; no split holds or merged source cels")
        else:
            forward, reverse = {}, {}
            for s, o in zip(source, output):
                a, b = s["cel"], o["cel"]
                if a in forward and forward[a] != b:
                    errors.append(f"strict_source remaps repeated source cel {a} to different output cels")
                if b in reverse and reverse[b] != a:
                    errors.append(f"strict_source reuses output cel {b} for distinct source cels")
                forward[a], reverse[b] = b, a
    if source is not None and output is not None and mode == "adapted_motion":
        boundaries = sorted({r["start"] for r in output} | {output[-1]["end"]})
        for run in source[1:]:
            anchor = run["start"]
            if anchor not in boundaries:
                nearest = min(boundaries, key=lambda b: (abs(b - anchor), b))
                offsets.append({"source_boundary": anchor, "nearest_output_boundary": nearest, "delta_frames": nearest - anchor})
        if offsets:
            warnings.append("Adapted motion omits source boundary anchors; inspect anchor_offsets and explicitly review the timing change")
    status = "invalid" if errors else "valid_with_warnings" if warnings else "structurally_valid"
    return {"valid": not errors, "status": status, "mode": mode, "errors": errors, "warnings": warnings, "anchor_offsets": offsets, "requires_visual_review": True, "scope": "Timeline structure and asset existence only; not visual, audio, CFR, or content approval"}


def validate_timeline(args):
    report_path = new_report_path(args.report)
    try:
        document = json.loads(Path(args.timeline).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"Cannot read timeline JSON: {exc}") from exc
    report = validate_timeline_document(document, Path(args.timeline).parent)
    if report_path:
        write_json(report_path, report)
    return report


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    analyze_parser = sub.add_parser("analyze", help="Candidate cels in one explicitly selected CFR shot")
    analyze_parser.add_argument("input", type=Path)
    analyze_parser.add_argument("--output", type=Path, required=True, help="New or empty directory")
    analyze_parser.add_argument("--start", type=int, required=True, help="0-based inclusive frame")
    analyze_parser.add_argument("--end", type=int, required=True, help="0-based exclusive frame")
    analyze_parser.add_argument("--fps", required=True, help="Rational, for example 24/1")
    analyze_parser.add_argument("--cfr", action="store_true", required=True, help="Assert that source is constant-frame-rate")
    analyze_parser.add_argument("--roi", type=parse_roi, help="x,y,width,height at native resolution")
    analyze_parser.add_argument("--ignore-mask", type=Path, help="Full-frame mask; nonzero pixels are ignored")
    analyze_parser.add_argument("--pixel-threshold", type=int, default=12, help="Changed if any RGB channel exceeds this absolute delta (default: 12)")
    analyze_parser.add_argument("--max-fraction", type=float, default=0.00025, help="Duplicate limit on all selected pixels (default: 0.00025)")
    analyze_parser.add_argument("--max-component", type=int, default=8, help="Duplicate limit on largest 8-connected changed region (default: 8 pixels)")
    analyze_parser.add_argument("--max-local-fraction", type=float, default=0.005, help="Duplicate limit for every grid cell (default: 0.005)")
    analyze_parser.add_argument("--grid", type=int, default=8, help="Native-resolution grid divisions per axis (default: 8)")
    analyze_parser.set_defaults(handler=analyze)
    diff_parser = sub.add_parser("diff", help="Actual 2D RGB differences and blue/orange contour overlays")
    diff_parser.add_argument("before", type=Path)
    diff_parser.add_argument("after", type=Path)
    diff_parser.add_argument("--output", type=Path, required=True, help="New or empty directory")
    diff_parser.add_argument("--roi", type=parse_roi, help="Optional enlarged detail: x,y,width,height")
    diff_parser.add_argument("--gain", type=float, default=4.0)
    diff_parser.add_argument("--pixel-threshold", type=int, default=12)
    diff_parser.add_argument("--grid", type=int, default=8)
    diff_parser.set_defaults(handler=difference_board)
    timeline_parser = sub.add_parser("validate-timeline", help="Check timeline structure; never visual approval")
    timeline_parser.add_argument("timeline", type=Path)
    timeline_parser.add_argument("--report", type=Path, help="New JSON report path; existing files are not overwritten")
    timeline_parser.set_defaults(handler=validate_timeline)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except (InputError, OSError, cv2.error) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("valid") is False else 0


if __name__ == "__main__":
    sys.exit(main())
