# Qualisys × Elastreme 同步采集软件

这个程序在运行 Qualisys Track Manager（QTM）的 Windows 电脑上，同时接收两个 Elastreme 单通道采集盒的数据，并按照 QTM 的开始/停止广播自动建立记录。

程序通过两个 USB 串口接收传感器帧，通过 QTM Real-Time Output 的 UDP `CaptureStart` / `CaptureStop` 消息控制一次测量的记录边界。每个传感器帧保留原始值、原始文本和电脑接收时间，便于后续和 C3D/QTM 数据按时间对齐。

## 功能

- 两个独立串口通道：S1、S2。
- 串口参数：`115200, 8N1`，分号 `;` 作为一帧结束符。
- 处理半帧、粘帧和无效帧；原始帧不会被覆盖。
- QTM 开始时自动建立记录，停止时自动关闭记录。
- 默认保留 QTM 开始前 1 秒的预触发数据。
- 采集前可做 3 秒基准归零和 3 到 5 点踝关节角度标定。
- 标定使用稳定窗口中位数和分段线性插值，并保存标定方案版本。
- 运行时同时保存原始值、基准差、角度和校准状态。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `sync_capture.py` | 串口读取、QTM UDP 监听、记录文件写入 |
| `sync_gui.py` | Windows 图形界面 |
| `calibration.py` | 稳定窗口统计、基准校准和角度插值 |
| `simulate_qtm.py` | 发送模拟 QTM 开始/停止消息 |
| `test_sync_capture.py` | 单元测试 |
| `config.json` | 默认通道、串口、QTM 端口和保存目录 |
| `启动同步采集.bat` | 使用本机虚拟环境启动图形界面 |
| `install.ps1` | 创建本机虚拟环境并安装依赖 |

## 硬件连接

1. 将两个 USB 接收器插入运行 QTM 的同一台 Windows 电脑。
2. 打开两个 Elastreme 采集盒并确认传感器已连接、已配对。
3. 关闭官方 Elastreme 采集软件，避免它占用串口。
4. 在设备管理器确认两个 CH340 串口。COM 号可能随电脑或插拔顺序变化，必须以当前电脑显示为准。
5. 在本软件界面中分别选择 S1 和 S2 的串口，不能选择同一个端口。

接收器外壳上的 `0051`、`0053` 编号不会由 CH340 自动报告给 Windows。若无法确认编号与 COM 口的对应关系，请保留 `unverified`，不要根据 COM 号猜测物理编号。

## 安装和启动

### 源码运行

在项目目录打开 PowerShell：

```powershell
.\install.ps1
.\.venv-local\Scripts\pythonw.exe .\sync_gui.py
```

也可以双击 `启动同步采集.bat`。脚本会使用 `.venv-local`，不会依赖复制过来的旧虚拟环境。

### 便携版

可以使用发行包 `Qualisys_Elastreme_Sync_20261003.zip`。解压后必须保留同一个目录中的：

- `Qualisys_Elastreme_Sync.exe`
- `_internal` 文件夹
- `config.json`

便携版电脑不需要安装 Python。若找不到 CH340 串口，先安装设备驱动，再重新插拔接收器。

## QTM 设置

1. 打开 QTM 项目。
2. 进入 `Project Options > Real-Time output`。
3. 确认 `Capture Broadcast Port` 为 `8989`。
4. 若 QTM 使用其他端口，在同步软件界面中填写完全相同的端口。
5. 先在同步软件点击“进入待机”。
6. 确认“传感器”显示 `2 / 2 有数据`，并且“QTM 监听”显示“等待 QTM”。
7. 再在 QTM 开始采集。
8. QTM 停止后，同步软件会自动保存并回到待机状态。

建议第一次使用先做 5 秒短采集，确认记录目录、帧数和 QTM 起止事件都正常，再开始正式实验。

## 踝关节标定

标定需要在进入待机、两个传感器都有数据后进行。S1 和 S2 需要分别标定。

### 基准归零

1. 在“踝关节标定”中选择通道。
2. 把踝关节放在规定的中立位并保持静止。
3. 点击“3 秒基准归零”。
4. 软件在最近 3 秒有效帧中去掉前后 10% 后计算统计量，并把中位数保存为 `x0`。
5. 如果稳定窗口标准差超过阈值，软件拒绝保存并提示重新采集。

后续数据首先计算：

```text
delta = raw_value - x0
```

重新佩戴、重新插拔、改变传感器位置或灵敏度后，应重新归零。

### 多点角度标定

建议至少采集以下姿态点：

```text
中立位       0°
背屈         10°、20°
跖屈        -10°、-20°
```

每个姿态保持静止，输入实际角度和方向，然后点击“采集当前标定点（3 秒）”。当前实现使用分段线性插值，超出标定范围时使用边界值，不进行大幅外推。

标定方案保存为 `calibration_profile.json`，包括基准值、标定点统计量、稳定性阈值、版本和拟合指标（R²、RMSE、最大误差）。温度目前只预留字段和记录接口，不自动做温度补偿。

## 输出文件

每次 QTM 测量在 `records` 下生成一个独立目录：

```text
records/
└─ MeasurementName_YYYYMMDD_HHMMSS_mmm/
   ├─ elastreme_raw.csv
   ├─ qtm_events.jsonl
   └─ session.json
```

`elastreme_raw.csv` 是长表，每行一个传感器帧，主要字段包括：

- `channel`、`sample_index`：通道和通道内编号；
- `host_monotonic_ns`、`host_datetime_iso`：电脑接收时间；
- `relative_to_qtm_start_ms`：相对 QTM 开始广播的时间；
- `raw_value`、`raw_frame`、`frame_valid`：原始数据；
- `pretrigger`：是否属于 QTM 开始前缓存；
- `baseline_delta`、`calibrated_angle_deg`、`calibration_status`：标定结果。

`qtm_events.jsonl` 保存 QTM 原始开始/停止 XML 和接收时间。`session.json` 保存测量名、通道配置、帧数、有效帧数、实测到帧率和标定方案版本。

## 无硬件测试

窗口一：

```powershell
.\.venv-local\Scripts\python.exe .\sync_capture.py --simulate-sensor
```

窗口二：

```powershell
.\.venv-local\Scripts\python.exe .\simulate_qtm.py --duration 3
```

运行测试：

```powershell
.\.venv-local\Scripts\python.exe -m unittest -v test_sync_capture
```

## 常见问题

**串口已打开但没有数据**：检查采集盒电源、传感器连接、无线配对和官方软件是否占用串口，并确认 S1/S2 没有选择同一个 COM 口。

**QTM 开始但没有记录**：确认 QTM 的 `Capture Broadcast Port` 和软件界面端口相同，默认都是 `8989`；检查 Windows 防火墙是否拦截 UDP。

**标定点不稳定**：保持踝关节静止，确认传感器贴合，等待数据稳定后重新采集。

## 同步和采样边界

`20 ms` 是当前设备协议确认的最快请求档位，理论上对应 50 Hz；实际串口输出速率必须以 `session.json` 中的 `measured_host_receive_rate_hz_by_channel` 为准。软件会保存收到的全部有效帧。

当前同步属于电脑端软件同步：时间零点是本机收到 QTM UDP 开始消息的时刻，传感器时间是本机收到串口帧的时刻。它不等同于硬件触发同步，也不应据此宣称固定毫秒级误差。

## 使用范围

本项目当前用于实验室内部开发和数据采集。仓库不包含真实采集记录、虚拟环境、构建缓存或未确认编号的实验数据。
