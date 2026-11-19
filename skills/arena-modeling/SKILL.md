---
name: arena-modeling
description: 在 Windows 上用 Rockwell Arena 14 或 16 建模、修改和验收可编辑的离散事件仿真 DOE。用于排队、生产、交通和物流流程的原生检查、批跑、方案比较、报表读取、布局导图及 Arena 14 二维动画采帧。
---

# Arena 离散事件仿真

通过 `arena-agent-cn` 的 MCP 或 `arena-agent` CLI 操作本机 Arena。安装、版本和 CC Switch 更新方式见[仓库说明](https://github.com/1837620622/arena-agent-cn#安装和更新)。

## 接入与版本

- 建模、批跑、导图前先用 `arena_status` 或 `arena-agent info` 查看 COM 版本、兼容模式、许可和已打开的模型。连接失败可运行 `arena-agent doctor`。只读已有报表时直接用 `get_results` 或 `arena-agent results`，可离线完成。
- Arena 14 批跑读取本轮 SIMAN `.out`，自动定点采帧在 Arena 14 路径上执行。Arena 16 批跑优先读取当轮 SQLite `.db`。只有本机实际运行过的版本和功能才能标为实机通过。模板字段以当前版本的 `module_operands`、`template_reference` 或现场读回为准。
- 许可状态未知时，在模型规模或许可功能影响任务时再确认。遇到 `edition_limit_exceeded`，据实说明并在许可允许的范围内调整。CLI 或 MCP 的实际参数以 `--help` 或 `tools/list` 为准，技能和后端用同一修订版。

## 建模与验收

1. 从需求和资料中提取到达与服务过程、资源容量、路径、队列、终止条件和评价指标。明确时间单位、运行长度、热身及重复次数。缺项可合理设定时记录假设，只有无法可靠推断且会改变结果的输入才需要确认。用用户指令确定报告、模型、图纸、截图和动画的交付范围。
2. 修改已有 DOE 前用 `inspect_model` 读回逻辑、参数与连线。保留原件时，用 `build_model` 的 `base` 和一个新 `save_as` 路径，复制模型所需的相对路径依赖。只有明确需要原位修改时才用 `base` 单独保存或提交单模块修改。运行报表会写到模型旁，需保留基准数据时在工作副本运行。
3. 先布置主流程、分支、回路和数据声明区，再按业务保存命名视图。用 `layout` 调自动排布，用 `shapes.level` 统一标题层级。建模后核对模块操作数读回、`check_errors` 和 `dropped_connections`。布局检查发现的问题与导图发现的文字遮挡、裁切分别核对。
4. 对保存后的 DOE 执行原生 Check 和批跑。`validate_model` 同时返回编译、运行、布局及可选 PNG 的结果。核对实际终止条件、重复次数、报告路径和关键指标，用流量守恒、利用率和约束做数量级检查。Arena 14 批跑可用 `timeout`，有其他活动或暂停运行时保留原运行并排查。批跑超时先确认运行状态，再决定是否重试。
5. 图形交付按业务分区导出，并在实际观看尺寸逐张复看。动画从初始化开始检查时钟、队列、状态和实际移动，核对定点帧的 `simulation_time`。排版或模型有改动时，保存、复开并复跑相关检查。需要 ZIP 时，按交付清单打包并从解压路径复开验收。

## 按任务查阅

| 任务 | MCP / CLI | 参考 |
| --- | --- | --- |
| 新建、另存或编辑模型 | `build_model`、`check_model` / `build`、`check` | [JSON 规格](references/build-spec.md)、[模块用法](references/modules.md) |
| 批跑、统计、方案对比 | `run_model`、`run_scenarios`、`get_results` / `run`、`scenarios`、`results` | [统计与 CSV](references/results.md) |
| 模型验收与图纸、动画交付 | `validate_model`、`list_named_views`、`screenshot_model`、`capture_animation_frames` / `validate`、`views`、`screenshot`、`animation-frames` | [原生排版、动画和交付](references/delivery.md) |
| 启动、报表或原生操作异常 | `arena_status`、`arena_guide` / `info`、`doctor`、`guide` | [兼容性排查](references/gotchas.md)、[原生行为记录](references/discoveries.md) |

MCP 客户端可用 `arena_guide` 读取对应主题，如 `delivery` 或 `results`。仅关闭指定的任务模型时，给 `close_models` 传明确的 `models` 列表。原生关闭可能保存修改，默认保留含未保存改动的模型。
