from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from sync_capture import SyncRecorder, load_config
from calibration import CalibrationProfile

try:
    from serial.tools import list_ports
except ImportError:
    list_ports = None


APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIG_PATH = APP_DIR / "config.json"


class SyncApp(tk.Tk):
    BG = "#F4F7FB"
    SURFACE = "#FFFFFF"
    TEXT = "#172033"
    MUTED = "#667085"
    BORDER = "#DDE4EE"
    PRIMARY = "#2563EB"
    PRIMARY_HOVER = "#1D4ED8"
    GREEN = "#16A36A"
    AMBER = "#D98B14"
    RED = "#DC4455"

    def __init__(self):
        super().__init__()
        self.title("Qualisys × Elastreme 同步采集")
        self.geometry("780x630")
        self.minsize(740, 580)
        self.configure(bg=self.BG)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.messages: queue.Queue[str] = queue.Queue()
        self.recorder: SyncRecorder | None = None
        self.threads: list[threading.Thread] = []
        self.running = False
        self.connected_channels: set[str] = set()
        self.receiving_channels: set[str] = set()
        self.config_data = load_config(CONFIG_PATH)
        self.calibration_profile = CalibrationProfile()
        profile_path = self.config_data.get("calibration_profile", "calibration_profile.json")
        self.calibration_path = Path(profile_path)
        if not self.calibration_path.is_absolute():
            self.calibration_path = APP_DIR / self.calibration_path
        if self.calibration_path.exists():
            try:
                self.calibration_profile = CalibrationProfile.load(self.calibration_path)
            except Exception:
                self.calibration_profile = CalibrationProfile()

        self._configure_styles()
        self._build_ui()
        self.refresh_ports()
        self.after(100, self._drain_messages)

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=self.SURFACE, background=self.SURFACE,
                        bordercolor=self.BORDER, lightcolor=self.BORDER, darkcolor=self.BORDER,
                        padding=7, arrowsize=15)
        style.map("TCombobox", bordercolor=[("focus", self.PRIMARY)])

    def _build_ui(self):
        outer = tk.Frame(self, bg=self.BG)
        outer.pack(fill="both", expand=True, padx=20, pady=18)

        header = tk.Frame(outer, bg=self.BG)
        header.pack(fill="x", pady=(0, 20))
        tk.Label(header, text="Q × E", fg="white", bg=self.PRIMARY,
                 font=("Segoe UI", 15, "bold"), width=5, height=2).pack(side="left")
        title_box = tk.Frame(header, bg=self.BG)
        title_box.pack(side="left", padx=14)
        tk.Label(title_box, text="Qualisys × Elastreme 同步采集",
                 fg=self.TEXT, bg=self.BG, font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        tk.Label(title_box, text="QTM 开始时自动记录传感器数据",
                 fg=self.MUTED, bg=self.BG, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(3, 0))

        status_row = tk.Frame(outer, bg=self.BG)
        status_row.pack(fill="x", pady=(0, 16))
        self.serial_status = self._status_card(status_row, "传感器", "0 / 2 已连接", self.MUTED)
        self.qtm_status = self._status_card(status_row, "QTM 监听", "未启动", self.MUTED)
        self.record_status = self._status_card(status_row, "采集状态", "待机", self.MUTED)

        content = tk.Frame(outer, bg=self.BG)
        content.pack(fill="both", expand=True)
        settings = self._card(content)
        settings.pack(side="left", fill="y", padx=(0, 16))
        settings.configure(width=280)
        settings.pack_propagate(False)
        logs = self._card(content)
        logs.pack(side="left", fill="both", expand=True)

        self._build_settings(settings)
        self._build_logs(logs)

    def _card(self, parent):
        return tk.Frame(parent, bg=self.SURFACE, highlightbackground=self.BORDER,
                        highlightthickness=1, bd=0)

    def _status_card(self, parent, title, value, color):
        card = self._card(parent)
        card.pack(side="left", fill="x", expand=True, padx=(0, 12))
        body = tk.Frame(card, bg=self.SURFACE)
        body.pack(fill="both", padx=18, pady=14)
        dot = tk.Label(body, text="●", fg=color, bg=self.SURFACE, font=("Segoe UI", 12))
        dot.pack(side="left")
        labels = tk.Frame(body, bg=self.SURFACE)
        labels.pack(side="left", padx=(9, 0))
        tk.Label(labels, text=title, fg=self.MUTED, bg=self.SURFACE,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        label = tk.Label(labels, text=value, fg=self.TEXT, bg=self.SURFACE,
                         font=("Microsoft YaHei UI", 11, "bold"))
        label.pack(anchor="w")
        return dot, label

    def _set_status(self, widget, text, color):
        dot, label = widget
        dot.configure(fg=color)
        label.configure(text=text)

    def _build_settings(self, parent):
        pad = tk.Frame(parent, bg=self.SURFACE)
        pad.pack(fill="both", expand=True, padx=22, pady=20)
        tk.Label(pad, text="采集设置", fg=self.TEXT, bg=self.SURFACE,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", pady=(0, 18))

        channel_config = {item["name"]: item.get("serial_port", "") for item in self.config_data["channels"]}
        receiver_ids = {item["name"]: item.get("receiver_id", "unverified") for item in self.config_data["channels"]}
        self.port1_var = tk.StringVar(value=channel_config.get("S1", ""))
        self.port2_var = tk.StringVar(value=channel_config.get("S2", ""))
        self.interval_var = tk.StringVar(value=str(self.config_data.get("sampling_interval_ms", 20)))
        self.udp_port_var = tk.StringVar(value=str(self.config_data.get("udp_port", 8989)))
        output = Path(self.config_data["output_directory"])
        if not output.is_absolute():
            output = APP_DIR / output
        self.output_var = tk.StringVar(value=str(output.resolve()))

        self._field_label(pad, "两个传感器通道")
        self.port1_combo = self._port_row(pad, self._channel_label("S1", receiver_ids), self.port1_var)
        self.port2_combo = self._port_row(pad, self._channel_label("S2", receiver_ids), self.port2_var)
        self._small_button(pad, "刷新串口", self.refresh_ports).pack(anchor="e", pady=(0, 12))

        self._field_label(pad, "采样间隔")
        self.interval_combo = ttk.Combobox(pad, textvariable=self.interval_var,
                                           values=("20", "50", "100"), state="readonly")
        self.interval_combo.pack(fill="x", pady=(0, 4))
        tk.Label(pad, text="20 ms ≈ 50 Hz；以设备实际到帧率为准",
                 fg=self.MUTED, bg=self.SURFACE, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(0, 14))

        self._field_label(pad, "QTM 广播端口")
        self.udp_port_entry = tk.Entry(
            pad, textvariable=self.udp_port_var, relief="flat", bg="#F8FAFC", fg=self.TEXT,
            insertbackground=self.TEXT, highlightbackground=self.BORDER, highlightthickness=1
        )
        self.udp_port_entry.pack(fill="x", ipady=7, pady=(0, 4))
        tk.Label(pad, text="必须与 QTM 的 Capture Broadcast Port 一致",
                 fg=self.MUTED, bg=self.SURFACE, font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(0, 14))

        self._field_label(pad, "保存位置")
        output_line = tk.Frame(pad, bg=self.SURFACE)
        output_line.pack(fill="x", pady=(0, 20))
        self.output_entry = tk.Entry(output_line, textvariable=self.output_var, relief="flat",
                                     bg="#F8FAFC", fg=self.TEXT, insertbackground=self.TEXT,
                                     highlightbackground=self.BORDER, highlightthickness=1)
        self.output_entry.pack(side="left", fill="x", expand=True, ipady=8)
        self._small_button(output_line, "选择", self.choose_output).pack(side="left", padx=(8, 0))

        self._field_label(pad, "踝关节标定")
        calibration_row = tk.Frame(pad, bg=self.SURFACE)
        calibration_row.pack(fill="x", pady=(0, 5))
        self.cal_channel_var = tk.StringVar(value="S1")
        self.cal_channel_combo = ttk.Combobox(calibration_row, textvariable=self.cal_channel_var,
                                              values=("S1", "S2"), state="readonly", width=5)
        self.cal_channel_combo.pack(side="left", padx=(0, 6))
        self._small_button(calibration_row, "3 秒基准归零", self.calibrate_baseline).pack(side="left")
        self.angle_var = tk.StringVar(value="0")
        self.direction_var = tk.StringVar(value="unknown")
        tk.Entry(calibration_row, textvariable=self.angle_var, width=7, relief="flat",
                 bg="#F8FAFC", fg=self.TEXT, highlightbackground=self.BORDER,
                 highlightthickness=1).pack(side="left", padx=(8, 4), ipady=5)
        tk.Label(calibration_row, text="°", fg=self.MUTED, bg=self.SURFACE).pack(side="left")
        self.direction_combo = ttk.Combobox(calibration_row, textvariable=self.direction_var,
                                            values=("unknown", "dorsiflexion", "plantarflexion"),
                                            state="readonly", width=15)
        self.direction_combo.pack(side="left", padx=(8, 0))
        self._small_button(pad, "采集当前标定点（3 秒）", self.calibrate_point).pack(anchor="w", pady=(0, 4))
        self.calibration_status = tk.Label(pad, text="标定方案：未完成", fg=self.MUTED,
                                           bg=self.SURFACE, font=("Microsoft YaHei UI", 8))
        self.calibration_status.pack(anchor="w", pady=(0, 12))

        self.start_button = tk.Button(pad, text="进入待机", command=self.start,
                                      bg=self.PRIMARY, fg="white", activebackground=self.PRIMARY_HOVER,
                                      activeforeground="white", relief="flat", cursor="hand2",
                                      font=("Microsoft YaHei UI", 11, "bold"), pady=10)
        self.start_button.pack(fill="x")
        self.stop_button = tk.Button(pad, text="停止监听", command=self.stop,
                                     bg="#EEF2F7", fg=self.TEXT, activebackground="#E4E9F0",
                                     relief="flat", cursor="hand2", pady=9, state="disabled")
        self.stop_button.pack(fill="x", pady=(9, 0))

        tk.Frame(pad, bg=self.BORDER, height=1).pack(fill="x", pady=20)
        tk.Label(pad, text="正确顺序", fg=self.TEXT, bg=self.SURFACE,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        steps = "1  传感器上电并连接\n2  点击进入待机\n3  在 QTM 开始采集\n4  QTM 停止后自动保存"
        tk.Label(pad, text=steps, justify="left", fg=self.MUTED, bg=self.SURFACE,
                 font=("Microsoft YaHei UI", 9), pady=8).pack(anchor="w")

    def _field_label(self, parent, text):
        tk.Label(parent, text=text, fg=self.TEXT, bg=self.SURFACE,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

    def _port_row(self, parent, label, variable):
        row = tk.Frame(parent, bg=self.SURFACE)
        row.pack(fill="x", pady=(0, 7))
        tk.Label(row, text=label, width=10, anchor="w", fg=self.PRIMARY, bg=self.SURFACE,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        combo = ttk.Combobox(row, textvariable=variable, state="readonly")
        combo.pack(side="left", fill="x", expand=True)
        return combo

    @staticmethod
    def _channel_label(name, receiver_ids):
        receiver_id = receiver_ids.get(name)
        return f"{name} · {receiver_id}" if receiver_id and receiver_id != "unverified" else f"{name} · 待核对"

    def _small_button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, bg="#EEF2F7", fg=self.TEXT,
                         activebackground="#E4E9F0", relief="flat", cursor="hand2", padx=12, pady=6)

    def _build_logs(self, parent):
        header = tk.Frame(parent, bg=self.SURFACE)
        header.pack(fill="x", padx=22, pady=(18, 10))
        tk.Label(header, text="运行记录", fg=self.TEXT, bg=self.SURFACE,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        self._small_button(header, "打开记录文件夹", self.open_output).pack(side="right")

        self.log_text = tk.Text(parent, bg="#101828", fg="#D7E1F2", insertbackground="white",
                                relief="flat", wrap="word", padx=16, pady=14,
                                font=("Cascadia Mono", 9), state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=22, pady=(0, 22))
        self._append_log("软件已就绪。确认设备上电后，点击“进入待机”。")

    def _calibration_channel(self):
        return self.cal_channel_var.get().strip() or "S1"

    def _save_profile(self):
        self.calibration_profile.save(self.calibration_path)
        try:
            self.config_data["calibration_profile"] = str(self.calibration_path.relative_to(APP_DIR))
        except ValueError:
            self.config_data["calibration_profile"] = str(self.calibration_path)
        CONFIG_PATH.write_text(json.dumps(self.config_data, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.recorder:
            self.recorder.calibration = self.calibration_profile
        channel = self._calibration_channel()
        points = len(self.calibration_profile.ensure_channel(channel).get("points", []))
        baseline = bool(self.calibration_profile.ensure_channel(channel).get("baseline"))
        self.after(0, lambda: self.calibration_status.configure(
            text=f"标定方案：基准 {'已完成' if baseline else '未完成'} · {points} 个点"))

    def _run_calibration(self, action):
        if not self.recorder or not self.running:
            self.log_from_worker("错误：请先点击“进入待机”，确保传感器正在接收数据。")
            return
        threading.Thread(target=action, daemon=True).start()

    def calibrate_baseline(self):
        def action():
            try:
                channel = self._calibration_channel()
                values = self.recorder.recent_values(channel, 3.0)
                if len(values) < 10:
                    raise ValueError("S1 最近 3 秒有效数据不足，请等待数据稳定后重试")
                self.calibration_profile.set_baseline(channel, values, 3.0)
                self._save_profile()
                self.log_from_worker(f"{channel} 基准归零完成。")
            except Exception as exc:
                self.log_from_worker(f"标定失败：{exc}")
        self._run_calibration(action)

    def calibrate_point(self):
        try:
            angle = float(self.angle_var.get().strip())
        except ValueError:
            messagebox.showwarning("角度错误", "请输入实际角度，例如 0、10 或 -20。")
            return
        direction = self.direction_var.get().strip() or "unknown"
        def action():
            try:
                channel = self._calibration_channel()
                values = self.recorder.recent_values(channel, 3.0)
                if len(values) < 10:
                    raise ValueError("S1 最近 3 秒有效数据不足，请等待数据稳定后重试")
                self.calibration_profile.add_point(channel, angle, values, direction, 3.0)
                self._save_profile()
                self.log_from_worker(f"{channel} 标定点已保存：{angle:g}° ({direction})。")
            except Exception as exc:
                self.log_from_worker(f"标定失败：{exc}")
        self._run_calibration(action)

    def refresh_ports(self):
        detected = list(list_ports.comports()) if list_ports else []
        ports = [p.device for p in detected]
        # Keep the two manually verified receiver mappings visible while a receiver
        # is unplugged. CH340 devices do not expose the printed 0051/0053 label.
        remembered = [self.port1_var.get().strip(), self.port2_var.get().strip()]
        choices = ports + [p for p in remembered if p and p not in ports]
        self.port1_combo["values"] = choices
        self.port2_combo["values"] = choices
        ch340_ports = [p.device for p in detected if "CH340" in p.description.upper()]
        if not self.port1_var.get():
            self.port1_var.set(ch340_ports[0] if ch340_ports else "")
        if not self.port2_var.get():
            unused = [p for p in ch340_ports if p != self.port1_var.get()]
            self.port2_var.set(unused[0] if unused else "")

    def choose_output(self):
        selected = filedialog.askdirectory(initialdir=self.output_var.get())
        if selected:
            self.output_var.set(selected)

    def open_output(self):
        path = Path(self.output_var.get())
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _append_log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def log_from_worker(self, message: str):
        self.messages.put(message)

    def _drain_messages(self):
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            self._append_log(message)
            if "串口已打开" in message and message[:2] in {"S1", "S2"}:
                self.connected_channels.add(message[:2])
                count = len(self.connected_channels)
                self._set_status(self.serial_status, f"{count} / 2 已打开", self.AMBER)
            elif "数据已到达" in message and message[:2] in {"S1", "S2"}:
                self.receiving_channels.add(message[:2])
                count = len(self.receiving_channels)
                self._set_status(self.serial_status, f"{count} / 2 有数据",
                                 self.GREEN if count == 2 else self.AMBER)
            elif message.startswith("正在监听 QTM"):
                self._set_status(self.qtm_status, "等待 QTM", self.GREEN)
            elif message.startswith("QTM 开始"):
                self._set_status(self.record_status, "正在记录", self.RED)
            elif message.startswith("QTM 停止"):
                self._set_status(self.record_status, "已保存 · 待机", self.GREEN)
            elif message.startswith("错误"):
                self._set_status(self.serial_status, "连接失败", self.RED)
        self.after(100, self._drain_messages)

    def _thread_runner(self, target):
        try:
            target()
        except Exception as exc:
            self.log_from_worker(f"错误：{exc}")
            self.after(0, self.stop)

    def start(self):
        if self.running:
            return
        port1 = self.port1_var.get().strip()
        port2 = self.port2_var.get().strip()
        if not port1 or not port2:
            messagebox.showwarning("需要两个串口", "请连接两个采集盒，并分别选择 S1 和 S2 的 COM 口。")
            return
        if port1 == port2:
            messagebox.showwarning("串口重复", "S1 和 S2 必须使用两个不同的 COM 口。")
            return
        try:
            udp_port = int(self.udp_port_var.get().strip())
            if not 1 <= udp_port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning("端口错误", "QTM 广播端口必须是 1 到 65535 的整数。")
            return
        available = {p.device for p in list_ports.comports()} if list_ports else set()
        missing = [name for name, port in (("S1", port1), ("S2", port2)) if port not in available]
        if missing:
            messagebox.showwarning("接收器未连接", "当前未检测到：" + "、".join(missing) + "。请接通后刷新串口。")
            return
        receiver_ids = {item["name"]: item.get("receiver_id", "unverified") for item in self.config_data["channels"]}
        config = dict(self.config_data)
        config.update({
            "channels": [
                {"name": "S1", "receiver_id": receiver_ids.get("S1", "unverified"), "serial_port": port1},
                {"name": "S2", "receiver_id": receiver_ids.get("S2", "unverified"), "serial_port": port2},
            ],
            "sampling_interval_ms": int(self.interval_var.get()),
            "udp_port": udp_port,
            "output_directory": self.output_var.get(),
        })
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.recorder = SyncRecorder(config, log=self.log_from_worker)
        self.running = True
        self.connected_channels.clear()
        self.receiving_channels.clear()
        self._set_status(self.serial_status, "连接中", self.AMBER)
        self._set_status(self.qtm_status, "启动中", self.AMBER)
        self._set_status(self.record_status, "等待开始", self.AMBER)
        self.start_button.configure(state="disabled", text="已进入待机")
        self.stop_button.configure(state="normal")
        self.port1_combo.configure(state="disabled")
        self.port2_combo.configure(state="disabled")
        self.interval_combo.configure(state="disabled")
        self.udp_port_entry.configure(state="disabled")
        self.threads = [threading.Thread(target=self._thread_runner, args=(self.recorder.udp_loop,), daemon=True)]
        for item in config["channels"]:
            self.threads.append(threading.Thread(
                target=self._thread_runner,
                args=(lambda c=item: self.recorder.serial_loop(c["name"], c["serial_port"]),),
                daemon=True,
            ))
        for thread in self.threads:
            thread.start()

    def stop(self):
        if self.recorder:
            self.recorder.close()
        self.recorder = None
        self.running = False
        self.start_button.configure(state="normal", text="进入待机")
        self.stop_button.configure(state="disabled")
        self.port1_combo.configure(state="readonly")
        self.port2_combo.configure(state="readonly")
        self.interval_combo.configure(state="readonly")
        self.udp_port_entry.configure(state="normal")
        self.connected_channels.clear()
        self.receiving_channels.clear()
        self._set_status(self.serial_status, "0 / 2 已连接", self.MUTED)
        self._set_status(self.qtm_status, "未启动", self.MUTED)
        self._set_status(self.record_status, "待机", self.MUTED)

    def on_close(self):
        if self.recorder:
            self.recorder.close()
        self.destroy()


if __name__ == "__main__":
    SyncApp().mainloop()
