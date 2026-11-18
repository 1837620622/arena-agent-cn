# Arena Agent 中文版

在 Windows 上通过 MCP 或命令行操作 Rockwell Arena。提供可编辑 `.doe` 建模、原生检查、批量仿真、逐次结果读取、命名视图导图和 Arena 14 二维动画定点截图。可用于 Codex、Claude Code 和支持 MCP 标准输入输出传输的其他客户端。

## 已实现的功能

| 功能 | 当前实现 | 可检查的结果 |
| --- | --- | --- |
| 环境与冷启动 | 从实际 COM 版本选择 Arena 14 或 16 的处理路径。Arena 14 从可写目录启动 | 版本、安装目录、许可状态和打开的模型 |
| 原生建模 | 读取 Arena 模板和模块字段，按规格创建模块和连接，或基于已有模型另存 | 可编辑 DOE、模型参数、原生连线和布局警告 |
| 原生仿真 | 检查模型，跑指定重复次数，应用并恢复方案参数 | 仿真是否完成、终止时刻、模型基本时间单位、原生报告路径和统计值 |
| 报表和方案对比 | 读取 Arena 14 本次 SIMAN 文本报告，读回 Arena 16 SQLite 同一轮统计，支持逐次重复 | 统计项、重复值、均值和原生报告提供的置信区间半宽 |
| 流程图 | 按模块范围、矩形或命名视图导出；缺省按 Arena 当前缩放分块，块数上限可调 | PNG、尺寸、范围、拼接块数、实际比例和捕获方式 |
| 二维动画截图 | 已暂停时抓取当前帧，或在 Arena 14 中自动播放到指定时刻导出，可按多个时刻成组导出 | 原生 PNG、命名视图、像素尺寸，以及自动运行实际到达的仿真时刻 |

## Arena 版本

| 版本 | 运行方式 | 验证范围 |
| --- | --- | --- |
| Arena 14.x | 自动识别 COM 主版本。从可写目录冷启动，批跑使用原生 SIMAN `.out`，完成后恢复模型的旧报表设置 | Arena 14.0 Professional 原生验证冷启动、MCP 重连、建模、仿真与导图 |
| Arena 16.x | 自动识别 Arena 16，沿用标准 COM 批跑，优先读 SQLite `.db`，可回退到原生 `.out` | 16.20 来自上游运行记录。本仓库测试覆盖版本分流和 SQLite 报表样本。新版功能的本机原生验证使用 Arena 14.0 |

运行 `arena-agent info` 或 MCP 的 `arena_status`，可查看已连接的 Arena 版本、版本模式和安装目录。模型的 `Record` 行由当前模板的实际字段决定。

## 安装和更新

先安装 Rockwell Arena 并确认能正常打开。Windows 安装脚本通过 [uv](https://docs.astral.sh/uv/getting-started/installation/) 管理独立的 Python 环境，并登记受支持的本地客户端。

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
```

Claude Code 插件入口是 `/plugin marketplace add 1837620622/arena-agent-cn`，添加后运行 `/plugin install arena-agent`。

### CC Switch 安装技能

在 CC Switch 的 Skills 页面添加仓库 `1837620622/arena-agent-cn`，分支选 `main`。在列表中安装 `arena-modeling`，再选需要同步的客户端。技能位于 [skills/arena-modeling](skills/arena-modeling)。之后可在 CC Switch 检查并应用技能更新。

CC Switch 的 Skills 功能同步技能文件。Python 后端和 MCP 服务通过本节的安装命令管理。

### Codex 和其他 MCP 客户端

安装完成后可运行 `arena-agent install --print` 查看本机实际启动命令和参数，用于登记支持 stdio 的客户端。接入后，新会话依次调用 `arena_status` 和 `arena_guide`。文件参数指向运行后端的 Windows 主机，优先使用绝对路径。一次完成一个原生 Arena 操作，批跑或动画任务完成后再开始下一项。

| 工作 | 工具调用顺序 |
| --- | --- |
| 新模型或修改旧模型 | `template_reference` / `module_operands` → `build_model` → `validate_model` |
| 读取已有 DOE | `inspect_model` → `list_named_views` → `screenshot_model` |
| 方案优化 | `run_scenarios` → `get_results`，再按相同单位、时间窗口和重复次数比较 |
| 动画定点验收 | `list_named_views` → `capture_animation_frame`，再检查输出的仿真时刻和图像 |

仓库的 [AGENTS.md](AGENTS.md) 记录了字体、布局、画面验收及打包顺序。交付项目可按同样的流程增加自己的任务清单和指标。

## 示例和验收

在新工作目录中运行示例。将下面的 `examples/bank.json` 改为克隆目录下该文件的实际路径。Arena 会在当前目录保存 `bank.doe`。

```powershell
arena-agent build "E:\path\to\arena-agent-cn\examples\bank.json"
arena-agent validate bank.doe --reps 3
arena-agent results bank.doe --per-rep
```

`validate` 依次调用 Arena 原生 Check 和批跑，报告布局警告、结束时间、重复次数、原生结果来源和统计项数量，并比对验收前后的 DOE 文件哈希。`ok: true` 表示这些检查通过。原生 `.out` 或 `.db` 等运行文件保存在模型旁，需要隔离结果时请先复制模型及其相对路径依赖。

Arena 14 批跑以本轮原生 `.out` 中的统计为准。`results` 离线回读时，从已有的可读报告中按最后写入时间选取最新结果。
SQLite 统计在同一次读事务内按最新运行汇总，支持读取尚未合并到主文件的已提交日志。

导出整图可在验收时加 `--png`。不给 `--ppu` 时导出比例不低于 Arena 当前缩放（屏幕上文字和连线的大小）：区域能在一块绘图区内按接近该比例显示时正好拼成一块，较大时按屏幕比例分块拼接。块数上限用 `--tiles` 调整（缺省 64），只有超出上限才等比缩小。模型已保存命名视图时，可读取视图列表，按实际名称导出分区图。导出后恢复绘图视野和分屏设置。动画截图先在 Arena 中显示整个目标区域并暂停，工具从当前绘图区截取同一帧。导图需要有桌面的 Windows 会话。

```powershell
arena-agent validate bank.doe --reps 3 --png bank.png
arena-agent views existing.doe
arena-agent screenshot existing.doe upstream.png --view "ARRIVAL_UPSTREAM" --ppu 0.22
```

| MCP 工具 | 用途 |
| --- | --- |
| `arena_status` / `arena_guide` | 查询版本、许可状态或建模参考 |
| `build_model` / `inspect_model` / `set_module` | 生成和检查可编辑模型，读取模块及连接，修改参数 |
| `check_model` / `validate_model` | 原生 Check，或一次完成检查、批跑、结果验收和可选 PNG 导出 |
| `run_model` / `run_scenarios` / `get_results` | 批跑、比较方案和读取结果，支持逐次重复 |
| `list_named_views` / `screenshot_model` | 列出命名视图，导出整图或指定分区 |
| `capture_animation_frame` | Arena 14 原生播放并跳停在指定仿真时刻，保存一张 PNG |
| `capture_animation_frames` | 同上，按多个递增时刻成组导出，用于核对运动是否连贯 |

建模参数参考 [JSON 规格](skills/arena-modeling/references/build-spec.md)。大模型先按业务环节划分命名视图，原生检查通过后导出总图和细节图，并以 100% 比例核对字号、模块边缘和分支连线。Arena 14 的报表弹窗、视口拼接和权限排查见 [兼容性说明](skills/arena-modeling/references/gotchas.md)。

## 二维动画及导图

先在 DOE 里建立实体图片、站点和路线、队列、状态值及命名视图。实机从零时刻播放，检查初始化表达式、运动方向、排队、状态切换和时钟。建议把动画、主要逻辑和资源回程分成独立的命名视图，图上保留业务标签及单位。

```powershell
arena-agent views "E:\work\animation.doe"
arena-agent animation-frame "E:\work\animation.doe" "E:\work\at80.png" --view "ANIMATION" --time 80
arena-agent animation-frames "E:\work\animation.doe" "E:\work\frames" --view "ANIMATION" --time 0 20 40 60 80
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

- `simulation_time` 使用模型的基本时间单位。模型必须在此次重复中能到达指定时刻。非常靠近零的时刻可降低 `speed`，让工具有时间设置断点。
- `speed` 是 Arena 的播放速度设置，`timeout` 是墙钟等待时间（秒）。模型已有活动运行时先结束原运行。自动截图完成后结束这次临时运行并恢复选项。
- 需要保留当前动画状态时，先在 Arena 中显示完整目标视图并暂停，再调用 `screenshot_model`。暂停态使用原生单帧，保持当前视野。需要更大分辨率时先增大可用绘图区。
- `capture_animation_frames` 按递增时刻成组导出。Arena 14 的暂停不能通过 COM 续播，每一帧都从零重放一次，所以取 5 到 8 个关键时刻（起始、首次排队、稳态、状态切换、结束）比抓几十帧更能看出运动是否连贯。相邻两帧之间出现跳跃时，先比对两帧上的仿真时钟，再判断是模型事件还是播放丢帧。
- MP4 录制采用现场播放并编码的方式。可在 Windows 上用 Python/COM 控制 Arena、捕获绘图区，再用 FFmpeg 编码。在抛出异常、段落切换和实际匀速播放处核对运动，最终对 MP4 全片解码，检查黑帧、录制窗口遮挡及模拟时钟变化。连续视频编码目前由外部录制流程完成，MCP 直接提供的是原生 PNG。

## 排版、字体与出图

排版先在建模规格里定好，再靠导图核对，顺序比事后修图省事。

- **位置**：没有写 `x`、`y` 的模块按连接图排布——跟着上游模块向右一列，分支上下居中，长链折行成带；`layout` 可调 `dx`、`dy`、`columns`、`origin`（缺省 2600 x 1800，12 列一带）。显式坐标优先，仍受 ±32,000 的视图范围限制。工具自动声明的属性、变量、队列、资源和实体类型按 6 列网格从 `declare_at` 排开，不会叠成一团。
- **字号层级**：`shapes` 的 `level` 提供三级：`title`（700 模型单位）、`section`（480，缺省）、`note`（320），`height` 可覆盖；`font`、`angle`、`italic`、`underline`、`bold` 直传 Arena。同一张图只用一种字体（Arial 或 Segoe UI）和一套层级，标题、区域名、注释不要混用字号。
- **命名视图**：`views` 按业务区域保存视图，给 `modules` 就按真实模块边界加 `margin` 计算，给 `region` 就用给定矩形。导图按视图名导出，分区图与整图都能对上同一块区域。
- **导图比例**：缺省区域是流程图模块加上附近 5,000 单位内的文字标题，远处孤立的图形不计入，只有声明没有流程图模块时按数据模块导出。缺省比例不低于 Arena 当前缩放，导图前先把 Arena 缩放到要交付的清晰度；区域大就提高 `max_tiles`（命令行 `--tiles`）分块拼接，只有块数超过上限才按比例降一档，不要靠降比例换整图。显式给 `pixels_per_unit` 时严格照办，超出块数或单张 1.2 亿像素上限会直接报错。缩小用 LANCZOS 保笔画，放大用 BICUBIC 防锯齿。
- **核对**：按最终观看尺寸逐张看 PNG，检查标签、单位、遮挡、边缘和拼接处；区域截面上的跨区连线要能通过相邻视图或整图追踪。原生对话框截短长表达式时，在截图旁补充从模型读回的完整字段。

## 验证边界与常见问题

| 情况 | 当前边界和处理方式 |
| --- | --- |
| Arena 14 | 原生验证覆盖冷启动、重连、建模、仿真、结果读取、编辑态导图、暂停态单帧和自动定点截图。不同模板仍须读回字段并运行 Check |
| Arena 16 | 保留版本识别、标准 COM 路径、报表读取和通用导图。使用 Arena 16 真实报表样本做过离线回归。当前发布使用 Arena 14 实机，Arena 16 的新版冷启动、运行和导图待实机复验 |
| 桌面或黑帧 | PNG 和连续动画需要交互式 Windows 桌面。导出期间会保持显示器唤醒。首次捕获全黑时重绘一次，仍返回全黑则停止导出。最小化、锁屏或远程桌面状态变化后，恢复活动桌面再重试 |
| 字体、边缘和显示层 | 工具能检测模块边界和导图视野。文字与轨道重叠、模板对话框截短、不同缩放和字体回退仍需人工检查最终 PNG、SVG 和 MP4。长文字优先调整布局或分行 |
| 原生运行文件 | Check、仿真和自动动画可能在 DOE 旁生成 Arena 原生运行文件。用工作副本隔离原始结果，并在打包时按任务清单选取成品 |
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
