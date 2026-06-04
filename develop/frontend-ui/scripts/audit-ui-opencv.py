import json
import sys
from pathlib import Path

import numpy as np

try:
    import cv2
except ModuleNotFoundError as exc:
    print(
        "OpenCV is required for UI visual auditing. Install it with `python -m pip install opencv-python`.",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


def resolve_artifact_path(path_value: str, report_path: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path

    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path

    return report_path.parent / path


def read_image(path: str, report_path: Path):
    resolved_path = resolve_artifact_path(path, report_path)
    image = cv2.imread(str(resolved_path), cv2.IMREAD_COLOR)
    if image is None:
        return None
    return image


def image_stats(path: str, report_path: Path):
    image = read_image(path, report_path)
    if image is None:
        return {
            "path": path,
            "ok": False,
            "error": "image-unreadable",
        }

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    stddev = float(np.std(gray))
    mean = float(np.mean(gray))
    white_ratio = float(np.mean(gray > 248))
    black_ratio = float(np.mean(gray < 7))
    edge_ratio = float(np.mean(cv2.Canny(gray, 70, 160) > 0))
    return {
        "path": path,
        "ok": True,
        "width": width,
        "height": height,
        "mean": round(mean, 3),
        "stddev": round(stddev, 3),
        "white_ratio": round(white_ratio, 5),
        "black_ratio": round(black_ratio, 5),
        "edge_ratio": round(edge_ratio, 5),
        "blank_like": stddev < 3.0 or white_ratio > 0.985 or black_ratio > 0.985,
    }


def diff_stats(before_path: str, after_path: str, report_path: Path):
    before = read_image(before_path, report_path)
    after = read_image(after_path, report_path)
    if before is None or after is None:
        return {
            "before": before_path,
            "after": after_path,
            "ok": False,
            "error": "pair-unreadable",
        }

    height = min(before.shape[0], after.shape[0])
    width = min(before.shape[1], after.shape[1])
    before = before[:height, :width]
    after = after[:height, :width]
    diff = cv2.absdiff(before, after)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    changed_ratio = float(np.mean(gray > 8))
    mean_delta = float(np.mean(gray))
    return {
        "before": before_path,
        "after": after_path,
        "ok": True,
        "width": width,
        "height": height,
        "changed_ratio": round(changed_ratio, 6),
        "mean_delta": round(mean_delta, 3),
        "changed": changed_ratio >= 0.001 or mean_delta >= 0.45,
    }


def iter_screenshots(report):
    for route in report.get("routes", []):
        if route.get("screenshot"):
            yield route["screenshot"]

    for key in ("buttonActions", "fieldActions", "scenarios"):
        for action in report.get(key, []):
            for field in ("before", "after"):
                if action.get(field):
                    yield action[field]


def iter_pairs(report):
    for key in ("buttonActions", "fieldActions", "scenarios"):
        for action in report.get(key, []):
            before = action.get("before")
            after = action.get("after")
            if before and after:
                yield key, action, before, after


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: audit-ui-opencv.py <playwright-ui-audit.json>", file=sys.stderr)
        return 2

    report_path = Path(sys.argv[1]).resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output_root = Path(report.get("outputRoot") or report_path.parent)
    issues = []
    images = []
    diffs = []

    seen = set()
    for screenshot in iter_screenshots(report):
        if screenshot in seen:
            continue
        seen.add(screenshot)
        stats = image_stats(screenshot, report_path)
        images.append(stats)
        if not stats.get("ok"):
            issues.append({
                "level": "error",
                "type": "opencv-image-unreadable",
                "path": screenshot,
            })
        elif stats.get("blank_like"):
            issues.append({
                "level": "error",
                "type": "opencv-blank-screen",
                "path": screenshot,
                "stats": stats,
            })
        elif stats.get("edge_ratio", 0) < 0.001:
            issues.append({
                "level": "warning",
                "type": "opencv-low-detail-screen",
                "path": screenshot,
                "stats": stats,
            })

    for key, action, before, after in iter_pairs(report):
        stats = diff_stats(before, after, report_path)
        stats["actionGroup"] = key
        stats["actionName"] = action.get("name")
        stats["route"] = action.get("route")
        stats["label"] = action.get("label")
        diffs.append(stats)
        if not stats.get("ok"):
            issues.append({
                "level": "error",
                "type": "opencv-diff-unreadable",
                "action": action.get("name"),
                "route": action.get("route"),
            })
            continue
        if action.get("expectVisualChange") and not stats.get("changed"):
            issues.append({
                "level": "error",
                "type": "opencv-expected-change-missing",
                "action": action.get("name"),
                "route": action.get("route"),
                "stats": stats,
            })

    opencv_report = {
        "ok": not any(issue.get("level") == "error" for issue in issues),
        "image_count": len(images),
        "diff_count": len(diffs),
        "issues": issues,
        "images": images,
        "diffs": diffs,
    }
    output_path = output_root / "opencv-ui-audit.json"
    output_path.write_text(json.dumps(opencv_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "opencv_ok": opencv_report["ok"],
        "image_count": len(images),
        "diff_count": len(diffs),
        "issues": len(issues),
        "output": str(output_path),
    }, ensure_ascii=False))
    return 0 if opencv_report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
