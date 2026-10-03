from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from calibration import CalibrationProfile

try:
    import serial
except ImportError:
    serial = None


PROGRAM_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or datetime.now().strftime("capture_%Y%m%d_%H%M%S")


@dataclass(frozen=True)
class QtmEvent:
    kind: str
    name: str
    packet_id: str
    result: str
    raw_xml: str
    received_monotonic_ns: int
    received_iso: str


@dataclass(frozen=True)
class SensorSample:
    channel: str
    index: int
    monotonic_ns: int
    received_iso: str
    raw_frame: str
    raw_value: str
    valid: bool


def parse_qtm_xml(data: bytes, monotonic_ns: int | None = None) -> QtmEvent | None:
    text = data.decode("utf-8-sig", errors="replace").strip("\x00 \r\n\t")
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    tag = root.tag.rsplit("}", 1)[-1]
    if tag not in {"CaptureStart", "CaptureStop"}:
        return None

    values = {}
    for child in root:
        values[child.tag.rsplit("}", 1)[-1]] = child.attrib.get("VALUE", "")
    return QtmEvent(
        kind="start" if tag == "CaptureStart" else "stop",
        name=values.get("Name", ""),
        packet_id=values.get("PacketID", ""),
        result=root.attrib.get("RESULT", ""),
        raw_xml=text,
        received_monotonic_ns=monotonic_ns if monotonic_ns is not None else time.perf_counter_ns(),
        received_iso=iso_now(),
    )


class CsvSession:
    HEADER = [
        "channel",
        "sample_index",
        "host_monotonic_ns",
        "host_datetime_iso",
        "relative_to_qtm_start_ms",
        "raw_value",
        "raw_frame",
        "frame_valid",
        "pretrigger",
        "baseline_delta",
        "calibrated_angle_deg",
        "calibration_status",
    ]

    def __init__(self, root: Path, event: QtmEvent, metadata: dict, flush_interval: float,
                 calibration: CalibrationProfile | None = None):
        self.event = event
        self.flush_interval = flush_interval
        self.last_flush = time.monotonic()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        self.directory = root / f"{safe_name(event.name)}_{stamp}"
        self.directory.mkdir(parents=True, exist_ok=False)
        self.csv_file = (self.directory / "elastreme_raw.csv").open("w", newline="", encoding="utf-8-sig")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(self.HEADER)
        self.events_file = (self.directory / "qtm_events.jsonl").open("w", encoding="utf-8")
        self.channel_frame_counts: dict[str, int] = {}
        self.channel_first_ns: dict[str, int] = {}
        self.channel_last_ns: dict[str, int] = {}
        self.metadata = dict(metadata)
        self.calibration = calibration
        self.metadata.update({
            "qtm_measurement_name": event.name,
            "qtm_start_packet_id": event.packet_id,
            "qtm_start_received_iso": event.received_iso,
            "qtm_start_received_monotonic_ns": event.received_monotonic_ns,
            "output_directory": str(self.directory.resolve()),
        })
        self.write_event(event)

    def write_event(self, event: QtmEvent) -> None:
        self.events_file.write(json.dumps(event.__dict__, ensure_ascii=False) + "\n")
        self.events_file.flush()

    def write_sample(self, sample: SensorSample, pretrigger: bool) -> None:
        relative_ms = (sample.monotonic_ns - self.event.received_monotonic_ns) / 1_000_000
        self.channel_frame_counts[sample.channel] = self.channel_frame_counts.get(sample.channel, 0) + 1
        self.channel_first_ns.setdefault(sample.channel, sample.monotonic_ns)
        self.channel_last_ns[sample.channel] = sample.monotonic_ns
        baseline_delta = ""
        calibrated_angle = ""
        calibration_status = "unavailable"
        if sample.valid and self.calibration:
            try:
                baseline_delta = f"{self.calibration.delta(sample.channel, float(sample.raw_value)):.6f}"
                value = self.calibration.angle(sample.channel, float(sample.raw_value))
                calibrated_angle = "" if value is None else f"{value:.6f}"
                calibration_status = "calibrated" if value is not None else "baseline_only"
            except ValueError:
                calibration_status = "baseline_missing"
        self.csv_writer.writerow([
            sample.channel,
            sample.index,
            sample.monotonic_ns,
            sample.received_iso,
            f"{relative_ms:.3f}",
            sample.raw_value,
            sample.raw_frame,
            int(sample.valid),
            int(pretrigger),
            baseline_delta,
            calibrated_angle,
            calibration_status,
        ])
        if time.monotonic() - self.last_flush >= self.flush_interval:
            self.csv_file.flush()
            self.last_flush = time.monotonic()

    def close(self, stop_event: QtmEvent | None, stats: dict) -> Path:
        if stop_event:
            self.write_event(stop_event)
            self.metadata.update({
                "qtm_stop_packet_id": stop_event.packet_id,
                "qtm_stop_result": stop_event.result,
                "qtm_stop_received_iso": stop_event.received_iso,
                "qtm_stop_received_monotonic_ns": stop_event.received_monotonic_ns,
            })
        measured_hz = {}
        for channel, count in self.channel_frame_counts.items():
            elapsed_ns = self.channel_last_ns[channel] - self.channel_first_ns[channel]
            if count > 1 and elapsed_ns > 0:
                measured_hz[channel] = round((count - 1) * 1_000_000_000 / elapsed_ns, 3)
        self.metadata.update(stats)
        self.metadata["recorded_frames_by_channel"] = dict(self.channel_frame_counts)
        self.metadata["measured_host_receive_rate_hz_by_channel"] = measured_hz
        self.metadata["receive_rate_note"] = (
            "Estimated only from frames written in this session, including pretrigger; "
            "not a hardware sampling-clock measurement."
        )
        self.metadata["closed_iso"] = iso_now()
        self.csv_file.flush()
        self.csv_file.close()
        self.events_file.close()
        (self.directory / "session.json").write_text(
            json.dumps(self.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.directory


class SyncRecorder:
    def __init__(self, config: dict, log: Callable[[str], None] = print):
        self.config = config
        self.log = log
        self.stop_flag = threading.Event()
        self.lock = threading.RLock()
        self.samples: deque[SensorSample] = deque()
        self.session: CsvSession | None = None
        self.sample_indices: dict[str, int] = {}
        self.valid_counts: dict[str, int] = {}
        self.invalid_counts: dict[str, int] = {}
        self.last_packet_key = None
        self.calibration: CalibrationProfile | None = None
        profile_path = config.get("calibration_profile")
        if profile_path:
            path = Path(profile_path)
            if not path.is_absolute():
                path = PROGRAM_DIR / path
            if path.exists():
                try:
                    self.calibration = CalibrationProfile.load(path)
                    self.log(f"已加载标定方案：{path}")
                except Exception as exc:
                    self.log(f"警告：标定方案加载失败：{exc}")
        self.output_root = Path(config["output_directory"])
        if not self.output_root.is_absolute():
            self.output_root = PROGRAM_DIR / self.output_root
        self.output_root.mkdir(parents=True, exist_ok=True)

    def add_sample(self, raw_frame: str, channel: str = "S1") -> None:
        now_ns = time.perf_counter_ns()
        raw_frame = raw_frame.strip()
        valid = bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw_frame))
        index = self.sample_indices.get(channel, 0)
        sample = SensorSample(
            channel=channel,
            index=index,
            monotonic_ns=now_ns,
            received_iso=iso_now(),
            raw_frame=raw_frame,
            raw_value=raw_frame if valid else "",
            valid=valid,
        )
        self.sample_indices[channel] = index + 1
        self.valid_counts[channel] = self.valid_counts.get(channel, 0) + int(valid)
        self.invalid_counts[channel] = self.invalid_counts.get(channel, 0) + int(not valid)
        cutoff = now_ns - int(float(self.config["pretrigger_seconds"]) * 1_000_000_000)
        with self.lock:
            self.samples.append(sample)
            while self.samples and self.samples[0].monotonic_ns < cutoff:
                self.samples.popleft()
            if self.session:
                self.session.write_sample(sample, pretrigger=False)

    def recent_values(self, channel: str, seconds: float = 3.0) -> list[float]:
        cutoff = time.perf_counter_ns() - int(float(seconds) * 1_000_000_000)
        with self.lock:
            return [float(s.raw_value) for s in self.samples
                    if s.channel == channel and s.valid and s.monotonic_ns >= cutoff]

    def save_calibration(self, profile: CalibrationProfile, path: Path) -> None:
        profile.save(path)
        self.calibration = profile

    def handle_event(self, event: QtmEvent) -> None:
        key = (event.kind, event.packet_id, event.name)
        with self.lock:
            if key == self.last_packet_key:
                return
            self.last_packet_key = key
            if event.kind == "start":
                if self.session:
                    old = self.session.close(None, self.stats())
                    self.log(f"警告：收到新的开始消息，已关闭未正常停止的记录：{old}")
                metadata = {
                    "program": "Qualisys-Elastreme Sync Recorder",
                    "program_version": "0.1.0",
                    "channels": self.config["channels"],
                    "baudrate": self.config["baudrate"],
                    "pretrigger_seconds": self.config["pretrigger_seconds"],
                    "sync_level": "software_udp_host_timestamp",
                    "calibration_profile_id": self.calibration.profile_id if self.calibration else None,
                    "calibration_profile_version": self.calibration.version if self.calibration else None,
                }
                self.session = CsvSession(
                    self.output_root, event, metadata, float(self.config["flush_interval_seconds"]), self.calibration
                )
                for sample in self.samples:
                    self.session.write_sample(sample, pretrigger=True)
                self.log(f"QTM 开始：{event.name or '(未命名)'} -> {self.session.directory}")
            elif self.session:
                directory = self.session.close(event, self.stats())
                self.session = None
                self.log(f"QTM 停止：RESULT={event.result or '未提供'} -> {directory}")
            else:
                self.log("收到 QTM 停止消息，但当前没有活动记录。")

    def stats(self) -> dict:
        return {
            "sensor_frames_total_by_channel": dict(self.sample_indices),
            "sensor_frames_valid_by_channel": dict(self.valid_counts),
            "sensor_frames_invalid_by_channel": dict(self.invalid_counts),
        }

    def serial_loop(self, channel: str = "S1", port_name: str | None = None, simulate: bool = False) -> None:
        if simulate:
            value = 600
            while not self.stop_flag.wait(0.02):
                value = 600 + ((value + 1 - 600) % 200)
                self.add_sample(str(value), channel)
            return
        if serial is None:
            raise RuntimeError("缺少 pyserial，请先运行 install.ps1。")
        port = serial.Serial(
            port_name,
            int(self.config["baudrate"]),
            timeout=float(self.config["serial_timeout_seconds"]),
        )
        self.log(f"{channel} 串口已打开：{port.port} @ {port.baudrate}")
        interval = self.config.get("sampling_interval_ms")
        if interval is not None:
            interval = int(interval)
            if interval not in {20, 50, 100}:
                raise ValueError("sampling_interval_ms 只允许 20、50 或 100。")
            port.write(bytes([interval]))
            port.flush()
            self.log(f"已发送单字节采样间隔命令：{interval} ms。")
        buffer = bytearray()
        waiting_since = time.monotonic()
        no_data_reported = False
        first_frame_reported = False
        try:
            while not self.stop_flag.is_set():
                chunk = port.read(port.in_waiting or 1)
                if not chunk:
                    if not no_data_reported and time.monotonic() - waiting_since >= 3.0:
                        self.log(f"{channel} 串口已打开，但 3 秒内没有收到数据；请检查采集盒电源与配对。")
                        no_data_reported = True
                    continue
                buffer.extend(chunk)
                while b";" in buffer:
                    frame, _, remainder = buffer.partition(b";")
                    buffer = bytearray(remainder)
                    frame_text = frame.decode("ascii", errors="replace")
                    self.add_sample(frame_text, channel)
                    if not first_frame_reported:
                        self.log(f"{channel} 数据已到达：首帧 {frame_text!r}")
                        first_frame_reported = True
                if len(buffer) > 4096:
                    self.add_sample(buffer.decode("ascii", errors="replace"), channel)
                    buffer.clear()
        finally:
            port.close()

    def udp_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.config["udp_bind"], int(self.config["udp_port"])))
        sock.settimeout(0.5)
        self.log(f"正在监听 QTM UDP：{self.config['udp_bind']}:{self.config['udp_port']}")
        try:
            while not self.stop_flag.is_set():
                try:
                    data, address = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                event = parse_qtm_xml(data)
                if event:
                    self.log(f"收到 {address[0]}:{address[1]} 的 QTM {event.kind} 消息")
                    self.handle_event(event)
        finally:
            sock.close()

    def close(self) -> None:
        self.stop_flag.set()
        with self.lock:
            if self.session:
                directory = self.session.close(None, self.stats())
                self.session = None
                self.log(f"程序退出，已安全关闭记录：{directory}")


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if "channels" not in config and "serial_port" in config:
        config["channels"] = [
            {"name": "S1", "serial_port": config.pop("serial_port")},
            {"name": "S2", "serial_port": ""},
        ]
    required = {"channels", "baudrate", "udp_bind", "udp_port", "pretrigger_seconds", "output_directory"}
    missing = required - set(config)
    if missing:
        raise ValueError(f"配置缺少字段：{', '.join(sorted(missing))}")
    config.setdefault("serial_timeout_seconds", 0.1)
    config.setdefault("sampling_interval_ms", 20)
    config.setdefault("flush_interval_seconds", 1.0)
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Qualisys QTM 与 Elastreme UDP 同步记录器")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--listen-only", action="store_true", help="只监听 QTM，不打开串口")
    parser.add_argument("--simulate-sensor", action="store_true", help="使用模拟传感器数据")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROGRAM_DIR / config_path
    config = load_config(config_path)
    recorder = SyncRecorder(config)
    threads = [threading.Thread(target=recorder.udp_loop, name="qtm-udp", daemon=True)]
    if not args.listen_only:
        channels = config["channels"]
        if args.simulate_sensor:
            channels = [{"name": "S1", "serial_port": "SIM1"}, {"name": "S2", "serial_port": "SIM2"}]
        for item in channels:
            if item.get("serial_port"):
                threads.append(threading.Thread(
                    target=recorder.serial_loop,
                    kwargs={"channel": item["name"], "port_name": item["serial_port"],
                            "simulate": args.simulate_sensor},
                    name=f"sensor-{item['name']}", daemon=True,
                ))
    for thread in threads:
        thread.start()
    print("同步记录器运行中。按 Ctrl+C 安全退出。")
    try:
        while all(thread.is_alive() for thread in threads):
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        recorder.close()
        for thread in threads:
            thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
