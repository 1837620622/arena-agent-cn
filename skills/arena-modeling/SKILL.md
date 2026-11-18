---
name: arena-modeling
description: 在 Windows 上使用 Rockwell Arena 14 或 16 构建、修改和验收可编辑的离散事件仿真模型（.doe）。适用于队列、产线、诊所、交通和物流流程，以及原生批跑、方案对比、逐次重复统计、命名视图和 PNG 导图。
---

# Arena 离散事件仿真

通过 [arena-agent-cn](https://github.com/1837620622/arena-agent-cn) 提供的 MCP 或 `arena-agent` 命令行操作本机 Arena。安装、更新和 CC Switch 同步方式见[仓库说明](https://github.com/1837620622/arena-agent-cn#安装和更新)。

## 接入与版本

- 先调用 `arena_status`，或执行 `arena-agent info`，查看当前 COM 版本、许可状态和兼容模式。连接失败时运行 `arena-agent doctor`。
- Arena 14 是当前的原生验收重点。批跑从本轮 SIMAN `.out` 取统计，离线回读按报告最后写入时间选择。Arena 16.x 批跑优先读取 SQLite `.db`。不同版本的模板字段以 `module_operands` 和 `template_reference` 为准。
- 如果许可状态未知且会影响建模规模，再确认使用的是学生版还是完整许可。遇到 `edition_limit_exceeded` 时准确指出限制并调整模型规模。

## 建模与验收

1. 从题目、已有数据或需求中确认到达、服务、资源、路径、队列、终止条件和评价指标。运行长度、热身时间及时间单位应在 `run_setup` 中明确。可合理设定的缺项应在交付时说明假设。
2. 已有模型先用 `inspect_model` 看模块、参数和连线。修改另存时用 `build_model` 的 `base` 与 `save_as`。新模型使用 [JSON 建模规格](references/build-spec.md)，字段值按 Arena 原生写法填写，如 `EXPO(5)`、`Minutes`。
3. 主流程采用统一阅读方向，分支各占一行或独立区域。用显式 `x`、`y` 定位，留足标签、折线和队列计数空间。检查 `check_errors`、`dropped_connections`、`layout_warnings` 和操作中的读回异常。
4. 用 `validate_model` 对已保存模型执行原生检查和批跑。指定 `png_path` 可以同时出图。验收结果包含模型文件是否变化、仿真结束时间、原生结果来源与统计项数量。需要明细时用 `run_model` 或 `get_results` 的 `per_replication`。验收会在模型旁生成原生报告文件。
5. 用流量守恒、利用率数量级和定义的约束核对结果。比较方案时固定观察口径、热身和重复次数，再看差值与 95% 置信区间半宽。统计字段和典型用法见 [结果读取](references/results.md)。
6. 大模型建模逻辑与动画分别保存命名视图。用 `list_named_views` 查看名称，用 `screenshot_model(view_name=...)` 分区导出。缩放至 100% 核对文字、箭头和拼接处。排版调整保存后重开模型、重新 Check 并按原输入复跑。

## 常用入口

| 用途 | MCP | 命令行 |
| --- | --- | --- |
| 查看运行环境 | `arena_status` | `arena-agent info` |
| 建模与检查 | `build_model`、`check_model` | `arena-agent build`、`arena-agent check` |
| 一次验收 | `validate_model` | `arena-agent validate` |
| 仿真与结果 | `run_model`、`run_scenarios`、`get_results` | `arena-agent run`、`arena-agent scenarios`、`arena-agent results` |
| 命名视图和截图 | `list_named_views`、`screenshot_model` | `arena-agent views`、`arena-agent screenshot` |

建模时按需读取 [模块用法](references/modules.md)、[兼容性和故障排查](references/gotchas.md) 或[原生行为记录](references/discoveries.md)。导图依赖可见的 Arena 窗口，修改或替换 DOE 前先关闭已打开的相应模型。交付按用户指定的文件类型和范围组织。
