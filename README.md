# Arena Agent 中文版

在 Windows 上通过 MCP 或命令行操作 Rockwell Arena。提供可编辑 `.doe` 建模、原生检查、批量仿真、方案比较、报表读取与 CSV 导出、命名视图导图和 Arena 14 二维动画定点截图。可用于 Codex、Claude Code 和支持 MCP 标准输入输出传输的其他客户端。

[安装和更新](#安装和更新) · [运行自带示例](#示例和验收) · [方案对比](#方案对比) · [读取报表与导出 CSV](#读取报表与导出-csv) · [命令速查](#cli-与-mcp-命令速查) · [动画和排版](#二维动画及导图) · [排查与验证](#验证边界与常见问题)

## 已实现的功能

| 功能 | 当前实现 | 可检查的结果 |
| --- | --- | --- |
| 环境与冷启动 | 从实际 COM 版本选择 Arena 14 或 16 的处理路径。Arena 14 从可写目录启动 | 版本、安装目录、许可状态和打开的模型 |
| 原生建模 | 读取 Arena 模板和模块字段，按规格创建模块和连接，或基于已有模型另存 | 可编辑 DOE、模型参数、原生连线和布局警告 |
| 原生仿真 | 检查模型，跑指定重复次数，应用并恢复方案参数 | 仿真是否完成、终止时刻、模型基本时间单位、原生报告路径和统计值 |
| 报表读取与 CSV | DOE 自动选报表，也可直接指定 SIMAN `.out` 或 SQLite `.db`。支持按统计名筛选，导出汇总或逐次重复表 | 原生报表路径、均值和置信区间半宽、实际重复编号与对应值 |
| 方案对比 | 在同一模型上依次应用场景参数，验证回退并汇总所有方案。需要表格时输出一个带场景名和重复编号的 CSV | 每组是否成功、方案结果、合并的统计表 |
| 流程图 | 按模块范围、矩形或命名视图导出；缺省按 Arena 当前缩放分块，块数上限可调 | PNG、尺寸、范围、拼接块数、实际比例和捕获方式 |
| 二维动画截图 | 已暂停时抓取当前帧，或在 Arena 14 中自动播放到指定时刻导出，可按多个时刻成组导出 | 原生 PNG、命名视图、像素尺寸，以及自动运行实际到达的仿真时刻 |

## Arena 版本

| 版本 | 运行方式 | 验证范围 |
| --- | --- | --- |
| Arena 14.x | 自动识别 COM 主版本。从可写目录冷启动，批跑使用原生 SIMAN `.out`，完成后恢复模型的旧报表设置 | Arena 14.0 Professional 原生验证冷启动、MCP 重连、建模、仿真与导图 |
| Arena 16.x | 自动识别 Arena 16，沿用标准 COM 批跑，优先读 SQLite `.db`，可回退到原生 `.out` | 16.20 来自上游运行记录。本仓库测试覆盖版本分流和 SQLite 报表样本。新版功能的本机原生验证使用 Arena 14.0 |

运行 `arena-agent info` 或 MCP 的 `arena_status`，可查看已连接的 Arena 版本、版本模式和安装目录。模型的 `Record` 行由当前模板的实际字段决定。

## 安装和更新

先安装可正常打开的 Rockwell Arena。需要导图或动画时，在可交互的 Windows 桌面中运行。Windows 安装脚本通过 uv 管理独立的 Python 3.12 环境，并登记检测到的本地客户端。 [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)

```powershell
git clone https://github.com/1837620622/arena-agent-cn.git
Set-Location arena-agent-cn
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
arena-agent --version
arena-agent doctor
```

需要单独登记某个客户端时，执行对应命令。登记后重新连接 MCP，工具就会出现在新会话中。

```powershell
arena-agent install --client codex
arena-agent install --client claude-code
```

已有客户端配置时可用 `-UpdateOnly` 更新运行包。安装器会在替换环境前检查进程，提示占用时先结束对应的 Arena MCP 会话或命令行任务。

```powershell
git pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -UpdateOnly
arena-agent --version
arena-agent doctor
```

脚本默认从 GitHub 的 `main` 安装。需固定到某个已发布版本时，把 `ARENA_AGENT_SOURCE` 设为该版本的 GitHub 源码 ZIP 地址再运行。此变量也支持本地仓库目录，适用于开发验证。 [已发布版本](https://github.com/1837620622/arena-agent-cn/releases)

Claude Code 插件入口是 `/plugin marketplace add 1837620622/arena-agent-cn`，添加后运行 `/plugin install arena-agent`。

### CC Switch 安装技能

在 CC Switch 的 Skills 页面添加仓库 `1837620622/arena-agent-cn`，分支选 `main`。在列表中安装 `arena-modeling`，再选需要同步的客户端。技能位于 [skills/arena-modeling](skills/arena-modeling)。之后可在 CC Switch 检查并应用技能更新。

CC Switch 的 Skills 功能同步技能文件。Python 后端和 MCP 服务通过本节的安装命令管理。使用符号链接或 Windows 目录联接同步时，`arena-agent install` 和 `uninstall` 会保留链接，并在结果中标注 `skill_managed_externally`。从 CC Switch 更新这些链接指向的技能副本。

### Codex 和其他 MCP 客户端

安装完成后可运行 `arena-agent install --print` 查看本机实际启动命令和参数，用于登记支持 stdio 的客户端。接入后，新会话依次调用 `arena_status` 和 `arena_guide`。文件参数指向运行后端的 Windows 主机，优先使用绝对路径。一次完成一个原生 Arena 操作，批跑或动画任务完成后再开始下一项。

| 工作 | 工具调用顺序 |
| --- | --- |
| 新模型或修改旧模型 | `template_reference` / `module_operands` → `build_model` → `validate_model` |
| 读取已有 DOE | `inspect_model` → `list_named_views` → `screenshot_model` |
| 方案优化 | `run_scenarios` 直接返回每组结果，或通过 `csv_path` 导出同一张表，再按相同单位、时间窗口和重复次数比较 |
| 动画定点验收 | `list_named_views` → `capture_animation_frame`，再检查输出的仿真时刻和图像 |

仓库的 [AGENTS.md](AGENTS.md) 记录了字体、布局、画面验收及打包顺序。交付项目可按同样的流程增加自己的任务清单和指标。

## 示例和验收

仓库自带 [银行分流与排队](examples/bank.json) 和 [诊所排队](examples/clinic.json) 示例。在已克隆的仓库根目录运行下面的命令。示例把 DOE 和报表放到独立工作目录，便于复跑和检查产物。

```powershell
$repo = (Get-Location).Path
$work = Join-Path $env:LOCALAPPDATA ('arena-agent\examples\bank-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work -Force | Out-Null
Push-Location $work
try {
    arena-agent build (Join-Path $repo 'examples\bank.json')
    arena-agent validate bank.doe --reps 3 --png bank.png
    arena-agent results bank.doe --per-rep --csv bank-replications.csv --measures 'Waiting' 'Utilization'
} finally {
    Pop-Location
}
```

`validate` 依次调用 Arena 原生 Check 和批跑，报告布局警告、结束时间、重复次数、原生结果来源和统计项数量，并比对验收前后的 DOE 文件哈希。`ok: true` 表示这些检查通过。原生 `.out` 或 `.db` 等运行文件保存在模型旁，需要隔离结果时请先复制模型及其相对路径依赖。

### 批跑超时和场景回退

Arena 14 的批跑默认等待 300 秒。长任务可以用 `arena-agent run bank.doe --reps 30 --timeout 900` 延长，`scenarios` 和 `validate` 同样支持 `--timeout`。MCP 的 `run_model`、`run_scenarios`、`validate_model` 使用 `timeout` 参数。时间以墙钟秒计，场景比较对每个场景单独计时。

已有运行或暂停中的模型会保留原状态，批跑返回提示。场景参数先检查原值是否可读，临时改名和参数会在运行结束后恢复。运行设置先检查字段和单位，遇到 COM 写入失败会尝试恢复已写入的值。单独执行 `setup` 或 `set_run_setup` 时，若有错误会返回 `saved: false`。Arena 14 批跑等待超时会返回 `timed_out: true`、请求 Arena 结束该次运行，并避免把未完成报表当作本轮结果。该时限约束的是批跑轮询，Windows COM 调用本身和 Arena 的模态对话框由应用控制。Arena 16 保留原生同步批跑方式。

`arena-agent close model.doe` 和 MCP 的 `close_models` 默认只关闭空闲、已保存的模型。Arena 将文档标记为已修改时，可回到界面决定保存或放弃。要明确保存一个已命名的模型并关闭，可用 `arena-agent close model.doe --save`，或在 MCP 传入 `save_changes: true`。

命令行成功返回 0。工具报告操作失败、找不到可读报表或导出失败时返回 1。命令行参数或用法解析失败时返回 2。自动化脚本可在每一步检查 `$LASTEXITCODE`。

### 方案对比

在 `bank.doe` 所在目录写入 `cases.json`。下例只改变柜员资源容量，使用每组 10 次重复。每个场景可以用 `replications`（也接受 `reps`）覆盖通用重复次数，并用 `run_setup` 写自己的运行设置。若同时写两个次数键，它们必须一致。

```json
[
  {"name": "两位柜员", "changes": {"Teller": {"Capacity": "2"}}},
  {"name": "三位柜员", "changes": {"Teller": {"Capacity": "3"}}}
]
```

```powershell
arena-agent scenarios bank.doe cases.json --reps 10 --per-rep --csv teller-replications.csv --measures 'Teller Service.Queue.Waiting'
```

对应的 MCP 工具是 `run_scenarios`。把 JSON 数组作为 `scenarios`，同时传入模型绝对路径、`replications: 10`、`per_replication: true`、`measures` 和 `csv_path`。逐组结果保存在返回值的 `scenarios` 中。CSV 在所有方案成功后写出，失败时可在 `scenarios` 中查看具体错误。如果重复利用同一个 DOE 路径，旁边的原生报表会随运行更新，因此请用本次返回值或 CSV 做多方案比较。

### 读取报表与导出 CSV

`results` 和 MCP 的 `get_results` 可以读取已有原生报表，无需重新运行 Arena。路径有两种用法：

- 输入 `bank.doe` 时，在同名 `.out` 和 `.db` 中选最近更新、可读且有统计的文件。SQLite 的 WAL 更新时间也纳入比较，时间相同时优先选择 SQLite。
- 输入 `bank.out` 或 `bank.db` 时，直接读取指定文件。适用于对照不同阶段留存的报表。

```powershell
arena-agent results bank.out --csv bank-summary.csv --measures 'Waiting' 'Utilization'
arena-agent results bank.out --per-rep --csv bank-replications.csv --measures 'Waiting'
```

MCP `get_results` 示例：

```json
{
  "model": "E:\\work\\bank.out",
  "per_replication": true,
  "measures": ["Waiting"],
  "csv_path": "E:\\work\\bank-replications.csv"
}
```

| 字段或选项 | 含义 |
| --- | --- |
| `source` / `results_source` | 离线读取或实际批跑采用的原生报表路径 |
| `measures` / `--measures` | 按统计名称做不区分大小写的子串匹配。多个关键字满足其一即可 |
| `include_empty` / `--all` | 保留 SQLite 中无观测值的统计；SIMAN 文本保持原生行 |
| `replication_ids`、`replications` | 逐次模式下并列返回真实重复编号和值。原始报表中的编号缺口保留，缺值呈现为 `null` |
| `csv_path` / `--csv` | 输出 UTF-8 BOM CSV。默认每个统计一行；逐次模式每个统计与重复编号一行 |
| `csv.path`、`csv.rows`、`csv.mode` | 输出路径、数据行数及模式。统计筛选无结果或逐次数据缺失时会报告写表错误 |

CSV 的汇总列包含均值、95% 置信区间半宽、最小值、最大值和重复数，单次 SIMAN 数据还可包含观测次数、输出值或计数。逐次表包含 `replication` 和 `value`。两种表均有统计名称、来源及方案列。同名统计如在 SQLite 中来自不同类别或数据源，会保留各条，并在名称上附加来源。报表写入失败时会保留已有 CSV。统计口径和独立、配对比较的前提见[结果读取说明](skills/arena-modeling/references/results.md)。

## CLI 与 MCP 命令速查

| CLI | MCP 工具 | 用途 |
| --- | --- | --- |
| `--version` / `info` / `doctor` | `arena_status` | 程序版本、Arena 兼容模式、许可及接入诊断 |
| `guide` / `edition` | `arena_guide` / `set_edition` | 建模参考及许可状态说明 |
| `panels` / `defs` | `list_panels` / `list_module_types` | 查当前安装的模板面板和模块类型 |
| `operands` / `reference` | `module_operands` / `template_reference` | 查询模板的实际参数名称、默认值和原生参考 |
| `build` / `inspect` | `build_model` / `inspect_model` | 生成、另存并检查可编辑模型，读取模块、连线和运行设置 |
| `set` / `setup` / `delete` | `set_module` / `set_run_setup` / `delete_modules` | 改模块参数、改运行设置和删除指定模块 |
| `check` / `validate` | `check_model` / `validate_model` | 原生 Check，或完成检查、批跑和可选 PNG 导出 |
| `run` / `scenarios` | `run_model` / `run_scenarios` | 单次批跑或一组方案对比，支持逐次重复和统计筛选 |
| `results` | `get_results` | 离线读取原生报表、选统计、导出 CSV |
| `views` / `screenshot` | `list_named_views` / `screenshot_model` | 列出命名视图，导出整图或指定分区 |
| `animation-frame` / `animation-frames` | `capture_animation_frame` / `capture_animation_frames` | Arena 14 播放到一个或多个指定时刻并导出 PNG |
| `close` | `close_models` | 关闭空闲模型，保护未保存编辑 |

技能入口按任务列出工具和需要的参考资料。MCP 可用 `arena_guide` 读取，例如 `topic: "skill"` 或 `topic: "delivery"`，CLI 对应 `arena-agent guide` 或 `arena-agent guide delivery`。 [技能入口](skills/arena-modeling/SKILL.md)

建模参数、视图及标题样式见 JSON 规格。排版复看、原生动画和从解压目录复验的步骤见交付指南。Arena 14 的报表弹窗、视口拼接和权限排查见兼容性说明。 [JSON 规格](skills/arena-modeling/references/build-spec.md) · [排版、动画与交付指南](skills/arena-modeling/references/delivery.md) · [兼容性说明](skills/arena-modeling/references/gotchas.md)

## 二维动画及导图

命名视图可在 Arena 中保存，也可以在建模规格中为 `views[].modules` 写模块 `id` 或原生模块名。模块边界和 `margin` 决定截图范围。也可传入 `region: [x0, y0, x1, y1]` 固定矩形。

```powershell
arena-agent views existing.doe
arena-agent screenshot existing.doe upstream.png --view "ARRIVAL_UPSTREAM" --ppu 0.22
arena-agent screenshot existing.doe overview.png --tiles 96
```

未指定 `--ppu` 时，导图优先按 Arena 当前缩放。区域较大时会分块拼接，达到块数或图像尺寸上限时允许降低比例。独立截图的块数上限默认 64。导出后检查返回的 `size`、`tiles` 和 `pixels_per_unit`。工具会恢复绘图视野和分屏设置。

先在 DOE 里建立实体图片、站点和路线、队列、状态值及命名视图。实机从零时刻播放，检查初始化表达式、运动方向、排队、状态切换和时钟。建议把动画、主要逻辑和资源回程分成独立的命名视图，图上保留业务标签及单位。

```powershell
arena-agent views "E:\work\animation.doe"
arena-agent animation-frame "E:\work\animation.doe" "E:\work\at80.png" --view "ANIMATION" --time 80
arena-agent animation-frames "E:\work\animation.doe" "E:\work\frames" --view "ANIMATION" --time 5 20 40 60 80
```

MCP 的同一操作接受下列参数。

```json
{
  "model": "E:\\work\\animation.doe",
  "out_path": "E:\\work\\at80.png",
  "view_name": "ANIMATION",
  "simulation_time": 80,
  "speed": 0.5,
  "timeout": 120
}
```

- `simulation_time` 使用模型的基本时间单位。Arena 的第一个断点取大于零的时刻，且模型须能在此次重复内到达该时刻。工具初始化后先设置断点，再开始播放，并核对 Arena 的实际暂停时刻后才写出 PNG。
- `speed` 是 Arena 的播放速度设置，`timeout` 是墙钟等待时间（秒）。慢速或长时间模型可调高 `timeout`。已有活动运行或暂停的模型时，自动截图会返回提示。自动截图会结束自己的临时运行并恢复选项，恢复失败时会报告受影响的设置。
- 需要保留当前动画状态时，先在 Arena 中显示完整目标视图并暂停，再调用 `screenshot_model`。暂停态使用原生单帧，保持当前视野。需要更大分辨率时先增大可用绘图区。
- `capture_animation_frames` 按递增时刻成组导出。工具会为每帧从零重放，适合取 5 到 8 个关键时刻，例如起始、首次排队、稳态、状态切换和结束。相邻帧出现跳跃时，先比对仿真时钟和对应事件，再检查播放是否丢帧。
- MP4 录制采用现场播放并编码的方式。可在 Windows 上用 Python/COM 控制 Arena、捕获绘图区，再用 FFmpeg 编码。在抛出异常、段落切换和实际匀速播放处核对运动，最终对 MP4 全片解码，检查黑帧、录制窗口遮挡及模拟时钟变化。连续视频编码目前由外部录制流程完成，MCP 直接提供的是原生 PNG。

## 排版、字体与出图

排版先在建模规格里定好，再靠导图核对，顺序比事后修图省事。

- **位置**：没有写 `x`、`y` 的模块按连接图排布——跟着上游模块向右一列，分支上下居中，长链折行成带；`layout` 可调 `dx`、`dy`、`columns`、`origin`（缺省 2600 x 1800，12 列一带）。排布结果会整体收进 ±32,000 的视图范围，压缩时在 `warnings` 里说明；显式坐标优先，但仍受同一范围限制。数据模块（资源、变量、队列、实体等）不进流程图，与自动声明的属性、变量、队列、资源和实体类型一起按 6 列网格从 `declare_at` 排开，不会叠成一团，也不会夹在流程模块之间。
- **字号层级**：`shapes` 的 `level` 提供三级：`title`（700 模型单位）、`section`（480，缺省）、`note`（320），`height` 可覆盖；`font`、`angle`、`italic`、`underline`、`bold` 直传 Arena。同一张图只用一种字体（Arial 或 Segoe UI）和一套层级，标题、区域名、注释不要混用字号。
- **命名视图**：`views` 按业务区域保存视图，给 `modules` 就按真实模块边界加 `margin` 计算，给 `region` 就用给定矩形。导图按视图名导出，分区图与整图都能对上同一块区域。
- **导图比例**取决于 Arena 当前视野、分块上限和图像尺寸上限。缺省区域覆盖流程图模块和附近 5,000 单位内的图形与文字，仅有声明时则覆盖数据模块。导图前把 Arena 缩放到所需清晰度，导图后检查返回的 `pixels_per_unit`。如需精确比例，显式指定 `pixels_per_unit`。超出块数上限、单张 1.2 亿像素或边长 30,000 像素时会报错。缩小用 LANCZOS，放大用 BICUBIC。
- **核对**：按最终观看尺寸逐张看 PNG，检查标签、单位、遮挡、边缘和拼接处；区域截面上的跨区连线要能通过相邻视图或整图追踪。原生对话框截短长表达式时，在截图旁补充从模型读回的完整字段。

## 验证边界与常见问题

| 情况 | 当前边界和处理方式 |
| --- | --- |
| Arena 14 | 原生验证覆盖冷启动、重连、建模、仿真、结果读取、编辑态导图、暂停态单帧和自动定点截图。不同模板仍须读回字段并运行 Check |
| Arena 16 | 保留版本识别、标准 COM 路径、报表读取和通用导图。使用 Arena 16 真实报表样本做过离线回归。当前发布使用 Arena 14 实机，Arena 16 的新版冷启动、运行和导图待实机复验 |
| 桌面或黑帧 | PNG 和连续动画需要交互式 Windows 桌面。导出期间会保持显示器唤醒。首次捕获全黑时重绘一次，仍返回全黑则停止导出。最小化、锁屏或远程桌面状态变化后，恢复活动桌面再重试 |
| 动画无法到达指定时刻 | 检查该时刻是否在一次重复的运行范围内，并确认模型通过原生 Check。慢速播放可以加长 `timeout`，暂停成功时核对 PNG 对应的 `simulation_time` |
| 字体、边缘和显示层 | 工具能检测模块边界和导图视野。文字与轨道重叠、模板对话框截短、不同缩放和字体回退仍需人工检查最终 PNG、SVG 和 MP4。长文字优先调整布局或分行 |
| 原生运行文件 | Check、仿真和自动动画可能在 DOE 旁生成 Arena 原生运行文件。用工作副本隔离原始结果，并在打包时按任务清单选取成品 |
| Arena 14 报表弹出 `Invalid key password` | 关闭对话框和当前运行，再用 `run` 或 `run_model` 批跑。工具在运行期间关闭数据库报表并读取本轮 SIMAN `.out`，结束后恢复原报表设置 |
| 找不到报告 | 检查 `source`、时间戳及模型所在目录。指定确切的 `.out` 或 `.db` 可避免复读同名其他报表。Arena 14 批跑从本轮 SIMAN `.out` 读取统计 |
| CSV 写入或筛选失败 | 检查目标后缀是否为 `.csv`，以及文件是否已在 Excel 中打开。先用 `results` 查看统计名。只有汇总的报表可导出汇总表，逐次表需要原生重复记录 |
| CC Switch 与工具版本不一致 | 用 `arena-agent --version` 核对后端，检查 CC Switch 的技能来源是否为本仓库 `main`，然后重新连接 MCP 会话 |
| 许可、依赖与版本 | 大模型受本机许可限制。复制 DOE 时保留用户要求的相对路径依赖和模板。使用当前安装版本重新检查和仿真 |

## 开发和反馈

```powershell
uv sync --extra dev
uv run pytest
uv run pytest -m arena
```

第一组测试使用本地测试样本。原生测试需要 Windows、Arena 和有效许可。提交 [问题](https://github.com/1837620622/arena-agent-cn/issues) 时可附 Arena 版本、失败命令、精简模型与 `arena-agent doctor` 的相关输出。

## 来源与许可

基于 [Musaib Bin Bashir 的 arena-agent](https://github.com/MusaibBashir/arena-agent) 开发，保留原作者版权与 [MIT 许可证](LICENSE)。
