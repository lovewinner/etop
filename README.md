# etop

一个类似 htop 的极简终端监控工具，只关注四项实时利用率：

| 指标 | 说明 |
| --- | --- |
| **CPU** | 整体利用率与方括号包裹的彩色 `|` 进度条 |
| **内存** | 物理内存使用率、Cache/Buffers、Swap |
| **GPU** | 利用率（检测到 GPU 时显示） |
| **显存** | 显存占用率（检测到显存信息时显示） |

界面为纯 curses 实现，无第三方依赖。每项指标一行：`[|...]` 彩色进度条 + 右对齐的百分比/显存/温度；多 GPU 时每张卡一行。不绘制面板边框和历史曲线。

## 快速开始

```bash
# 直接运行
./etop

# 或者
python3 -m etop_tui

# 安装为命令
pip install -e .
etop
```

要求 Linux + Python 3.9+。若终端不支持 UTF-8，可加 `--ascii`。

## 界面

```text
 dev24G 10.14.25.86 up 11d 16h                     10:53:41
 load 0.14 0.37 0.36  cpu 2/2028  gpu 4
 CPU  [||||                            ]              12.4%
 MEM  [||||||||||||||||||||            ]   9.6/15.6G(61.5%)
 GPU  [||||||||||||||||||||||||||||||  ] 14.9/16G(93%) 54°C
 etop 0.1.0  q quit  p pause  +/-  h help
```

主机名使用高亮蓝色加粗显示，便于在多台机器间快速确认当前主机；紧随其后的是默认路由所在网卡的 IPv4 地址（没有默认路由或取不到地址时省略）。版本信息位于底部左下角 footer；第二行还显示 CPU 任务数（running/total 线程）和 GPU 任务数（检测到 GPU 时）。所有指标（CPU、内存、GPU 利用率与显存）都使用 htop 风格、以 `[` `]` 包裹的彩色 `|` 条。单张 GPU 也只占一行：显存与温度附在利用率条之后，不再单独占用 VRAM 行。

颜色按 4 档分色：低于 40% 绿色、40–60% 黄色、60–85% 橙色、85% 以上红色。分档作用于所有进度条。`--once` 在彩色终端下同样着色；256 色终端使用低饱和度的绿/黄/橙/红（#87AF87 / #D7D787 / #D7AF87 / #D78787），8 色终端会合并中间两档。若终端不支持颜色，则用加粗/下划线区分档位。背景始终沿用终端默认设置，不写入背景色，也不会回退到黑色；同时不使用 faint/dim 属性，避免部分终端把它渲染成底色。终端主题开启透明背景时即可透出桌面。

## 按键

| 按键 | 作用 |
| --- | --- |
| `q` / `Esc` | 退出 |
| `p` / 空格 | 暂停 / 继续采样 |
| `+` / `-` | 加快 / 减慢刷新（0.25s 步进，范围 0.25–10s） |
| `h` / `?` | 帮助 |

## 命令行

```text
etop [-d SECONDS] [--once] [--gpu {auto,nvidia,sysfs,none}]
     [--demo] [--no-color] [--ascii] [--version]
```

- `-d, --delay`：刷新间隔，默认 1 秒。
- `--once`：打印一次纯文本快照后退出，适合脚本或非交互环境。
- `--gpu`：GPU 后端，默认自动探测。
- `--demo`：使用合成 GPU 数据，便于在没有 GPU 的机器上预览 GPU 行。
- `--no-color` / `--ascii`：关闭颜色（仍使用终端默认背景）/ 使用纯 ASCII 字符。

## 数据来源

- **CPU**：`/proc/stat`，两次采样之间 busy/total 的增量；首帧显示开机以来的平均值。
  `iowait` 计入空闲，与 htop 口径一致。
- **内存**：`/proc/meminfo`。使用量 = `MemTotal - MemAvailable`，并单独展示 Cache/Buffers 与 Swap。
- **GPU**：
  - NVIDIA：`nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu`；
  - AMD / 其他 DRM 驱动：`/sys/class/drm/card*/device/gpu_busy_percent`、`mem_info_vram_used`、`mem_info_vram_total`、`hwmon/*/temp1_input`。
  - 未检测到 GPU 时自动隐藏 GPU 与显存行；驱动短暂失败时保留上一帧并标记 `stale`。
- **IP 地址**：从 `/proc/net/route` 找到目标为 `0.0.0.0` 且处于 UP 状态的默认路由网卡，再用 `SIOCGIFADDR` 读取其 IPv4 地址。
- **Load / uptime / tasks**：`os.getloadavg()`、`/proc/uptime`，任务数取自 `/proc/loadavg` 的 `running/total`。
- **GPU 任务数**：NVIDIA 统计 `nvidia-smi --query-compute-apps=pid` 的唯一 PID 数；sysfs 后端近似统计持有 `/dev/dri` 文件描述符的进程数。

## 多 GPU

检测到多张 GPU 时，每张卡一行：利用率条 + 右对齐的显存/温度文字。单卡时格式相同，只是标签写作 `GPU`。

```text
 GPU0 [||||||||||||||||||||||||||      ] 38.7/48G(81%) 34°C
 GPU1 [||||||||||||||||||||||||||      ] 38.7/48G(81%) 34°C
 GPU2 [|||||||||||||                   ] 20.2/48G(42%) 33°C
 GPU3 [||                              ]  2.4/48G(5%)  59°C
```

44x8 时可同时显示 CPU、内存和最多 4 张 GPU（空间不足时会自动省略底部快捷键行）；每多一张卡需要多一行，例如 8 张卡至少需要 44x12。单张 GPU 只占一行，因此 44x5 即可显示 CPU、内存和 GPU 三项。

## 终端尺寸

每项指标占一行（标签 + `[|...]` 彩色条 + 右对齐文字），最小 **44x5**。单张 GPU 时 GPU 利用率、显存和温度合并为一行，因此在 **44x5** 下就能显示全部三行（此时 footer 自动省略）：

```text
 host 10.0.0.5 up 11d 16h          10:53:49
 load 0.14 0.37 0.36  cpu 2/2028  gpu 1
 CPU  [||||||||||        ]            53.7%
 MEM  [|||               ]   77/504G(15.3%)
 GPU  [|                 ] 1.2/24G(5%) 34°C
```

多 GPU 时会把每张卡写成：

```text
 CPU  [|||||||||||||||||               ]              53.7%
 MEM  [|||||                           ]     77/504G(15.3%)
 GPU0 [||||||||||||||||||||||||||      ] 38.7/48G(81%) 34°C
 GPU1 [||||||||||||||||||||||||||      ] 38.7/48G(81%) 34°C
 GPU2 [|||||||||||||                   ] 20.2/48G(42%) 33°C
 GPU3 [||                              ]  2.4/48G(5%)  59°C
```

多张 GPU 而高度不足时（例如 44x5）会退回仅 CPU 和内存两行：

```text
 host 10.0.0.5 up 11d 16h          10:53:49
 load 0.14 0.37 0.36  cpu 2/2028  gpu 4
 CPU  [|||||||||||         ]          53.7%
 MEM  [|||                 ] 77/504G(15.3%)
 etop 0.1.0  q quit  p pause  +/-  h help
```

空间富余时在底部留白。极紧尺寸下 footer（含版本和快捷键）会自动省略，以保留全部指标行；低于 **44x5** 时显示提示。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖 `/proc` 解析、NVIDIA CSV 解析、sysfs GPU 采样、布局与格式化。
