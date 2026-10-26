# etop
<img width="1033" height="793" alt="image" src="https://github.com/user-attachments/assets/afcfd191-9cc0-4776-a475-8d050f99cd72" />

一个类似 htop 的极简终端监控工具，只关注四项实时利用率：

| 指标 | 说明 |
| --- | --- |
| **CPU** | 整体利用率、逻辑核数、温度、频率与按时间类别分段的彩色 `|` 进度条 |
| **内存** | 物理内存使用情况，条内按 used/shared/buffers/cache 分段 |
| **GPU** | 利用率、CUDA 核心数（NVIDIA）、显存与温度（同一行显示） |
| **显存** | 显存占用量与百分比（与 GPU 同一行显示） |

界面为纯 curses 实现，无第三方依赖。每项指标一行：`[|...]` 彩色进度条 + 右对齐的百分比/显存/温度；CPU 与内存条由多段颜色组成，GPU 每张卡一行，单卡时利用率、显存与温度合并到同一行。不绘制面板边框和历史曲线。

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
 dev24G 10.14.25.86 up 11d 16h                     11:33:59
 load 0.14 0.37 0.36  cpu 2/2028  gpu 4
 CPU(20)    [|||||                 ]      13.4% 3.2GHz 52°C
 MEM(15.6G) [||||||||||||||||||||||]                  62.2%
 GPU(16384) [|||||||||||||||||     ] 79% 14.9/16G(93%) 54°C
 etop 0.1.0  q quit  p pause  +/-  h help
```

示例只展示字符，实际颜色见下方的分段说明。主机名使用高亮蓝色加粗显示，便于在多台机器间快速确认当前主机；紧随其后的是默认路由所在网卡的 IPv4 地址（没有默认路由或取不到地址时省略）。版本信息位于底部左下角 footer；第二行还显示 CPU 任务数（running/total 线程）和 GPU 任务数（检测到 GPU 时）。所有指标（CPU、内存、GPU 利用率与显存）都使用 htop 风格、以 `[` `]` 包裹的彩色 `|` 条。逻辑核数、内存总量、GPU 核心数这类常量直接放在标签里（如 `CPU(20)`、`MEM(15.6G)`、`GPU(16384)`），启动时读取一次，不随刷新变化；CPU 核心数取自首次采样的 `/proc/stat`，GPU 核心数通过 NVML 读取并缓存。行尾只显示动态值：CPU 是利用率、频率（如 `3.2GHz`，取当前最高的核频）和温度（如 `52°C`），虚拟机等无传感器/无频率信息的环境会自动省略对应项；内存只显示使用率。单张 GPU 也只占一行：条后依次是利用率、显存占用/总量(百分比)、温度（如 `79% 14.9/16G(93%) 54°C`，前者无括号是 GPU 利用率，括号内是显存占用率），不再单独占用 VRAM 行。

进度条使用两套配色：

- **CPU**：按时间类别分段堆叠 —— user 绿、nice 蓝、system 红、irq 橙、steal 紫、iowait 青，各段合计为整体利用率（iowait 计入利用率，与 htop 详细模式一致；iowait 为 0 时不显示该段）。
- **内存**：按类别分段堆叠 —— used 绿、shared 橙、buffers 蓝、cache 黄。条的长度是这四类之和（等于 `MemTotal - MemFree`）占物理内存的比例，因此通常比括号里的 used 百分比长；剩余空白表示空闲内存。计算方式与 htop 一致：

  ```text
  used   = MemTotal - MemFree - Cached - SReclaimable - Buffers
  shared = Shmem
  cache  = Cached + SReclaimable - Shmem
  文字   = (used + shared) / MemTotal
  ```

- **GPU**：没有可拆分的类别，整条按 4 档负载着色：低于 40% 绿色、40–60% 黄色、60–85% 橙色、85% 以上红色。

`--once` 的纯文本输出仍统一按 4 档负载着色。256 色终端使用低饱和度的绿/黄/橙/红（#87AF87 / #D7D787 / #D7AF87 / #D78787）以及柔和的蓝/橙；8 色终端会合并相近颜色。终端不支持颜色时，CPU/内存条用不同字符（`|#*@$%&`）区分分段，GPU 条用加粗/下划线区分档位。背景始终沿用终端默认设置，不写入背景色，也不会回退到黑色；同时不使用 faint/dim 属性，避免部分终端把它渲染成底色。终端主题开启透明背景时即可透出桌面。

## 按键

| 按键 | 作用 |
| --- | --- |
| `q` / `Q` / `Esc` | 退出 |
| `p` / `P` / 空格 | 暂停 / 继续采样（暂停时 header 显示 `PAUSED`） |
| `+` / `=` / `-` / `_` | 加快 / 减慢刷新（0.25s 步进，范围 0.25–10s） |
| `h` / `H` / `?` | 帮助 |

## 命令行

```text
etop [-d SECONDS] [--once] [--gpu {auto,nvidia,sysfs,none}]
     [--demo] [--no-color] [--ascii] [--version]
```

- `-d, --delay`：刷新间隔，默认 1 秒，超出 0.25–10 秒会被夹到边界值。
- `--once`：打印一次纯文本快照后退出，适合脚本或非交互环境；GPU 与显存在这里仍分两行输出。
- `--gpu`：GPU 后端，可选 `auto`、`nvidia`、`sysfs`、`none`，默认自动探测。
- `--demo`：使用合成 GPU 数据，便于在没有 GPU 的机器上预览 GPU 行。
- `--no-color` / `--ascii`：关闭颜色（仍使用终端默认背景）/ 使用纯 ASCII 字符（如 `°C` 变为 `C`）。
- `--version`：打印版本号后退出。

## 数据来源

- **CPU**：`/proc/stat`，两次采样之间 busy/total 的增量；首帧显示开机以来的平均值。
  busy 包含 `iowait`（与 htop 详细模式一致），user/nice/system/irq/steal/iowait 的增量分别用于进度条分段，各段之和即显示的整体利用率。
- **CPU 温度**：优先读取 `/sys/class/hwmon/hwmon*` 中 coretemp/k10temp 等 CPU 传感器的 `temp*_input`，取最高值；没有 hwmon 时回退到 `/sys/class/thermal/thermal_zone*` 中类型含 cpu/pkg/package 的区域。无传感器时省略。
- **CPU 频率**：优先读取 `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`（实时频率，取最高核频）；没有 cpufreq 时回退到 `/proc/cpuinfo` 的 `cpu MHz`（标称频率，虚拟机通常只有这个）。两者都不可用时省略。
- **内存**：`/proc/meminfo`。按 htop 的口径拆分 `MemFree`、`Buffers`、`Cached`、`SReclaimable` 和 `Shmem`：used = `MemTotal - MemFree - Cached - SReclaimable - Buffers`，shared = `Shmem`，cache = `Cached + SReclaimable - Shmem`；文字显示 `(used + shared)/MemTotal`，四个分段之和等于 `MemTotal - MemFree`。
- **GPU**：
  - NVIDIA：`nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu`（名称仅用于识别，界面不显示型号）；
  - AMD / 其他 DRM 驱动：`/sys/class/drm/card*/device/gpu_busy_percent`、`mem_info_vram_used`、`mem_info_vram_total`、`hwmon/*/temp1_input`。
  - 未检测到 GPU 时自动隐藏 GPU 与显存行；驱动短暂失败时保留上一帧并标记 `stale`。
- **IP 地址**：从 `/proc/net/route` 找到目标为 `0.0.0.0` 且处于 UP 状态的默认路由网卡，再用 `SIOCGIFADDR` 读取其 IPv4 地址（不支持 IPv6 地址显示）。
- **Load / uptime / tasks**：`os.getloadavg()`、`/proc/uptime`，任务数取自 `/proc/loadavg` 的 `running/total`。
- **GPU 任务数**：NVIDIA 统计 `nvidia-smi --query-compute-apps=pid` 的唯一 PID 数；sysfs 后端近似统计持有 `/dev/dri` 文件描述符的进程数。
- **GPU 核心数**：nvidia-smi 没有核心数字段，通过 ctypes 调用 NVML 的 `nvmlDeviceGetNumGpuCores` 获取（无第三方依赖）；仅 NVIDIA 可用，AMD/sysfs 与无 NVML 的环境自动省略。

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
 host 10.0.0.5 up 11d 16h          11:34:16
 load 0.14 0.37 0.36  cpu 2/2028  gpu 1
 CPU(64)    [|||||||  ]   55.7% 2.4GHz 61°C
 MEM(504G)  [|||||||  ]               15.3%
 GPU(16384) [|        ] 5% 1.2/24G(5%) 34°C
```

多 GPU 时会把每张卡写成：

```text
 CPU(64)     [||||||||||||||       ]      55.7% 2.4GHz 61°C
 MEM(504G)   [||||||||||||         ]                  15.3%
 GPU0(16384) [|||||||||||||||||    ] 81% 38.7/48G(81%) 34°C
 GPU1(9728)  [|||||||||||||||||    ] 81% 38.7/48G(81%) 34°C
 GPU2(16384) [|||||||||            ] 42% 20.2/48G(42%) 33°C
 GPU3(2560)  [|                    ]  5%  2.4/48G(5%)  59°C
```

多张 GPU 而高度不足时（例如 44x5）会退回仅 CPU 和内存两行：

```text
 host 10.0.0.5 up 11d 16h          11:34:16
 load 0.14 0.37 0.36  cpu 2/2028  gpu 4
 CPU(64)   [|||||||||   ] 55.7% 2.4GHz 61°C
 MEM(504G) [|||||||     ]             15.3%
 etop 0.1.0  q quit  p pause  +/-  h help
```

空间富余时在底部留白。极紧尺寸下 footer（含版本和快捷键）会自动省略，以保留全部指标行；高度不足时 GPU 行会被暂时隐藏（只保留 CPU 和内存）。低于 **44x5** 时显示形如 `too small (min 44x5)` 的提示。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖 `/proc` 解析、CPU 时间分类、内存分类核算、默认路由与 IP 解析、NVIDIA CSV 解析、sysfs GPU 采样、分段进度条绘制、布局与格式化。
