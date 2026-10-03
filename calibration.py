from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def robust_stats(values: Iterable[float], trim_fraction: float = 0.1) -> dict:
    data = [float(v) for v in values]
    if not data:
        raise ValueError("稳定窗口没有有效数据")
    ordered = sorted(data)
    trim = int(len(ordered) * trim_fraction)
    core = ordered[trim:len(ordered) - trim] if len(ordered) - 2 * trim > 0 else ordered
    mean = statistics.fmean(core)
    median = statistics.median(core)
    stdev = statistics.stdev(core) if len(core) > 1 else 0.0
    return {
        "count": len(data),
        "trimmed_count": len(core),
        "mean": mean,
        "median": median,
        "stdev": stdev,
        "minimum": min(data),
        "maximum": max(data),
        "range": max(data) - min(data),
        "trim_fraction": trim_fraction,
    }


@dataclass
class CalibrationPoint:
    angle_deg: float
    direction: str
    value: float
    stats: dict
    collected_iso: str
    temperature_c: float | None = None


class CalibrationProfile:
    VERSION = "1.0"

    def __init__(self, profile_id: str = "ankle_profile", stability_stdev_threshold: float = 20.0):
        self.profile_id = profile_id
        self.version = self.VERSION
        self.created_iso = now_iso()
        self.updated_iso = self.created_iso
        self.stability_stdev_threshold = float(stability_stdev_threshold)
        self.channels: dict[str, dict] = {}

    def ensure_channel(self, channel: str) -> dict:
        return self.channels.setdefault(channel, {"baseline": None, "points": []})

    def set_baseline(self, channel: str, values: Iterable[float], duration_seconds: float,
                     temperature_c: float | None = None) -> dict:
        stats = robust_stats(values)
        stable = stats["stdev"] <= self.stability_stdev_threshold
        entry = self.ensure_channel(channel)
        entry["baseline"] = {
            "value": stats["median"],
            "stats": stats,
            "duration_seconds": float(duration_seconds),
            "stable": stable,
            "temperature_c": temperature_c,
            "collected_iso": now_iso(),
        }
        self.updated_iso = now_iso()
        if not stable:
            raise ValueError(f"{channel} 基准窗口不稳定：标准差 {stats['stdev']:.3f} > {self.stability_stdev_threshold:.3f}")
        return entry["baseline"]

    def add_point(self, channel: str, angle_deg: float, values: Iterable[float], direction: str,
                  duration_seconds: float, temperature_c: float | None = None) -> CalibrationPoint:
        stats = robust_stats(values)
        stable = stats["stdev"] <= self.stability_stdev_threshold
        if not stable:
            raise ValueError(f"{channel} 标定点不稳定：标准差 {stats['stdev']:.3f} > {self.stability_stdev_threshold:.3f}")
        point = CalibrationPoint(float(angle_deg), direction, stats["median"], stats,
                                 now_iso(), temperature_c)
        entry = self.ensure_channel(channel)
        entry["points"] = [p for p in entry["points"] if not (
            float(p["angle_deg"]) == point.angle_deg and p["direction"] == direction
        )]
        entry["points"].append(asdict(point))
        entry["points"].sort(key=lambda p: (p["angle_deg"], p["direction"]))
        self.updated_iso = now_iso()
        return point

    def delta(self, channel: str, raw_value: float) -> float:
        baseline = self.ensure_channel(channel).get("baseline")
        if not baseline:
            raise ValueError(f"{channel} 尚未完成基准校准")
        return float(raw_value) - float(baseline["value"])

    def angle(self, channel: str, raw_value: float, direction: str = "unknown") -> float | None:
        entry = self.ensure_channel(channel)
        baseline = entry.get("baseline")
        points = entry.get("points", [])
        if not baseline or len(points) < 2:
            return None
        x = float(raw_value) - float(baseline["value"])
        usable = [p for p in points if direction in {"unknown", "", p["direction"]}]
        if len(usable) < 2:
            usable = points
        pairs = sorted((float(p["value"]) - float(baseline["value"]), float(p["angle_deg"])) for p in usable)
        if x <= pairs[0][0]:
            return pairs[0][1]
        if x >= pairs[-1][0]:
            return pairs[-1][1]
        for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
            if x0 <= x <= x1:
                if x1 == x0:
                    return (y0 + y1) / 2
                return y0 + (x - x0) * (y1 - y0) / (x1 - x0)
        return None

    def metrics(self, channel: str) -> dict:
        entry = self.ensure_channel(channel)
        baseline = entry.get("baseline")
        points = entry.get("points", [])
        if not baseline or len(points) < 2:
            return {"point_count": len(points), "r2": None, "rmse_deg": None, "max_error_deg": None}
        errors = []
        for point in points:
            predicted = self.angle(channel, float(point["value"]), point["direction"])
            if predicted is not None:
                errors.append(predicted - float(point["angle_deg"]))
        if not errors:
            return {"point_count": len(points), "r2": None, "rmse_deg": None, "max_error_deg": None}
        mean = statistics.fmean(float(p["angle_deg"]) for p in points)
        ss_tot = sum((float(p["angle_deg"]) - mean) ** 2 for p in points)
        ss_res = sum(e * e for e in errors)
        return {
            "point_count": len(points),
            "r2": None if ss_tot == 0 else 1 - ss_res / ss_tot,
            "rmse_deg": math.sqrt(statistics.fmean(e * e for e in errors)),
            "max_error_deg": max(abs(e) for e in errors),
        }

    def to_dict(self) -> dict:
        return {
            "schema": "elastreme_calibration_profile",
            "profile_id": self.profile_id,
            "version": self.version,
            "created_iso": self.created_iso,
            "updated_iso": self.updated_iso,
            "stability_stdev_threshold": self.stability_stdev_threshold,
            "channels": self.channels,
            "metrics": {channel: self.metrics(channel) for channel in self.channels},
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CalibrationProfile":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        profile = cls(data.get("profile_id", "ankle_profile"), data.get("stability_stdev_threshold", 20.0))
        profile.version = data.get("version", cls.VERSION)
        profile.created_iso = data.get("created_iso", profile.created_iso)
        profile.updated_iso = data.get("updated_iso", profile.updated_iso)
        profile.channels = data.get("channels", {})
        return profile
