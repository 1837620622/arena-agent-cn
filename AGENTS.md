# 仓库开发说明

- 此仓库提供 Windows 下的 Arena COM 自动化、MCP、命令行和 arena-modeling 技能。
- 核心在 `src/arena_agent/`，技能在 `skills/arena-modeling/`，公开示例在 `examples/`。
- 本地用 `uv sync --extra dev` 安装依赖。使用 `uv run pytest` 运行离线测试。已安装 Arena 时运行 `uv run pytest -m arena`。
- Arena 14 是本仓库的原生验收重点。更新前复验冷启动、MCP 重连、Check、批跑、报告读回以及命名视图导图。
- `upstream` 指向原作者仓库。上游有更新时先比较提交和行为，合入适用于本仓库的改动，再复验 Arena 14 和中文技能入口。
- 打包前核对许可证、安装源、技能入口和测试状态。发布到 `1837620622/arena-agent-cn`，避免提交本机配置、私有模型或原生运行临时文件。

## Codex 和其他 MCP 客户端的执行顺序

1. 读取安装后的 `arena-modeling/SKILL.md`、任务书和项目说明，通过 `arena_status` 或 `arena-agent info` 确认实际版本、桌面会话和许可。已有 DOE 先用 `inspect_model`，新建或修改用 `build_model`，导出前用 `list_named_views` 核对命名视图。原始模型另存后再改。
2. `validate_model` 检查原生模块、连接和批跑结果。逐次统计使用 `get_results`，核对运行长度、热身、单位和重复次数。Arena 14 使用本轮 `.out`，Arena 16 按自己的报表路径读取。批跑之后另做可见动画测试。
3. 冷打开动画模型，从第零分钟正常播放，检查时钟、移动方向、队列、资源或船闸状态以及返回路径。动画表达式从初始状态起就要有效，比例和取余先检查分母为零的情形。Arena 14 自动定点截图用 `capture_animation_frame`，人工暂停后可用 `screenshot_model` 抓取完整显示的视图。逐帧检查视频黑帧和模拟时钟，对照现场播放区分视觉停顿与模型本身的排队等待。
4. 导出后核对原生视图的窗口、缩放和分屏状态。实机测试需覆盖编辑态与暂停态截图、冷启动和 MCP 重连。模型、截图与原生统计一起从 ZIP 解压复验，再确认仓库版本和已安装版本一致。

## 原生排版、字体和成品交付

- 先依题目和本机《Getting Started with Arena》里的二维动画、资源和 Navigate 示例划分流程与动画区域。主流程统一阅读方向，绕行和回程独立走线。命名视图按功能区域和阅读顺序设置，整图与关键局部都应可读。 [Rockwell Arena 官方课程](https://www.rockwellautomation.com/en-mde/products/software/arena-simulation/support/training/course-agenda.html)
- 排版在建模规格里定：没写坐标的模块按连接图排布，`layout` 调 `dx`、`dy`、`columns`；标题用 `shapes.level`（`title`、`section`、`note`）统一层级；分区用 `views` 保存命名视图。导图缺省按 Arena 当前缩放分块，模型大可调高 `max_tiles`，不要靠降比例换整图。
- 同一图中标题、区域标签、统计值和注释使用一致的字体层级。英文可用系统 Arial 或 Segoe UI，中文按任务要求选本机可显示的中文字体。轨迹与业务文字分离，状态文字避免覆盖连线和图标。轴、时间、长度、速率等标明单位，用足够的前景和背景反差。图中文字的可读性可参考 4.5:1 的对比度基准，字号以最终 PNG 或视频在实际观看尺寸下清楚为准。 [W3C 文字对比度参考](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum)
- 逐张打开导出的 PNG 和矢量图，核对标签、数字、单位、图例、边缘和拼接处。区域截面上的跨区连线要能通过相邻视图或整图追踪。原生对话框截短长表达式时，保留原截图，在旁边补充从模型读回的完整字段，按运算边界换行。
- DOE、图纸、截图和视频只呈现任务相关信息。交付清单按任务书及用户范围核对。没有交付要求的临时脚本、JSON、日志、工具标记和第三方水印留在成品包之外。对 ZIP 做 CRC 校验、解压逐文件比对和原生复开。仓库提交只包含工具和公开可复现的测试。
