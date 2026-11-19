# 原生排版、动画和交付验收

适用于需要可编辑 DOE、清晰流程图、原生截图或动画的任务。把任务书和样例作为规格，按用户指定的交付范围整理。已有正式模型和报表需要保留时，先将 DOE 和相对路径依赖复制到工作目录，再在副本上仿真、修改和重放动画。

## 排版和字体

- 主流程沿同一方向阅读。按业务分区布置模块，给并行分支、异常路径和回程线预留独立空间。把数据声明放到流程外。可先用 `layout` 排自动坐标，再根据模块的实际边界和线端调整关键位置。
- 同一级模块、区域标题和状态数字对齐。用 `shapes.level` 统一标题、分区名和说明的层级，以模型的业务标签、变量和单位为主。说明文字放在对应区域，避开分支出口、轨迹及活动图标。
- 同类标签统一字体和字号，英文可用 Arial 或 Segoe UI。要求中文时先确认本机字体可用，再在 Arena 和导出图里检查实际字形。用最长标签、多位数、带小数点或单位的值试显示，给换行、数值变化和箭头留空间。
- 为整图和关键分区保存命名视图。流程、资源回程和运行中的动画可各用独立视图。图边给标题、队列条和连线留出空间。跨区连线要能在整图或相邻分区追踪。
- `layout_warnings` 可发现模块包围框重叠或越界。文字、连线、动态值和模板对话框的裁切仍需要看原生导图。原生字段显示不足时，从模型读回完整内容补充到对应图注或说明中。

## 原生回读和导图

1. 保存后从交付目录复开 DOE，核对关键参数、重复行、连接及 `run_setup`。计算或排版修改后复核受影响的参数和分支。必要时对比修改前的 `inspect_model` 读回结果。
2. 调用 `validate_model` 或 `arena-agent validate`。检查 `check.errors`、`check.dropped_connections`、`check.layout_warnings`。查看 `run.ok`、`run.replications`、`run.end_time`、`run.results_source` 和 `model_file_unchanged`。允许的警告应说明原因。批跑通过后用原生报表核对要求的统计和单位。
3. 用 `list_named_views` 核对实际名称与范围，再用 `screenshot_model` 导出 `view_name`。也可用 `region` 指定递增的 `[x0, y0, x1, y1]`。需要固定比例时给 `pixels_per_unit`。大图优先拆成可读分区，或适度调高 `max_tiles`。留意导出实际的 `size`、`tiles` 和 `pixels_per_unit`。
4. 在实际观看尺寸逐张检查标题、文字、数值、单位、图例、线端、边缘和拼接接缝。图太密时同时提供概览及清晰局部。输出到新路径，并确认打开的是本轮文件。

## 二维动画

- 用题目对应的实体图片、站点与路线、队列、资源和状态数字说明模型中的实际活动。检查状态对象和 SIMAN 变量是否随运行更新。涉及除法、取余等表达式时，先检查从零时刻起分母和初始化都有效。
- 从零时刻以正常速度播放，检查实体移动方向、轨迹衔接、排队释放、资源状态和仿真时钟。不同颜色或线型有业务含义时，使用简洁图例。遇到等待或回程，结合时钟和模型事件判断画面变化。
- Arena 14 可用 `capture_animation_frame` 或 `capture_animation_frames`。主要参数是 `model`、`view_name`、`simulation_time` 或 `simulation_times`、`out_path` 或 `out_dir`。时刻按模型基础单位填写，须大于零且在一次重复能到达的范围内。工具在播放前设断点，返回 `simulation_time` 供核对。多帧操作会从零分别重放，可抽取初始化后、首次排队、状态切换及稳定阶段等关键时刻。
- 已有运行需要保持当前状态时，在 Arena 内切到完整目标视图并暂停，用 `screenshot_model` 抓取原生当前帧。自动采帧遇到其他活动运行时保留其状态。先确认运行的归属，再决定如何结束或单独复验。
- 需要连续视频时，录制完整的原生播放，复看起始、移动、状态切换和结束。解码确认时长、黑帧、仿真时钟推进和画面是否被其他窗口遮挡。定点 PNG 用于核对指定时刻，视频的流畅性看连续播放与录制成品。

已有模型保存了 `FLOW` 和 `ANIMATION` 命名视图时，可以按下面的命令验收。重复次数、时刻和视图名以当前任务为准。

```powershell
arena-agent views .\work\model.doe
arena-agent validate .\work\model.doe --reps 3 --png .\review\flow.png --view FLOW
arena-agent animation-frames .\work\model.doe .\review\frames --view ANIMATION --time 1 25 80
```

## 交付复验

- 按任务清单汇集可编辑模型、必要依赖和指定的图、截图、视频或原始结果。用户自行写报告时只交约定的成品。构建规格、脚本、调试输出和原始录制放在工作目录，按交付约定选取文件。文件名能对应模型、方案、视图或时刻，图面仅放任务需要的文字与标识。
- 需交 ZIP 时，核对清单与压缩成员，做 CRC 检查，从新目录解压并逐文件比较哈希。用解压后的 DOE 做原生复开、Check 和代表性仿真，依赖用解压目录中的实际相对路径。解压后的图和视频也应能打开。
- 给出交付路径、实际 Arena 版本、检查与运行状态、已核对的图面或视频，以及受安装版本、许可或显示环境限制的项目。用户只要求某一步时，保留对应的检查证据即可。

站点与路线、资源动画、状态板和仿真时钟的设置，可参照本机的 Arena Help、随安装附带的 *Getting Started with Arena* 及 Arena Smarts。 [Rockwell Arena 官方课程](https://www.rockwellautomation.com/en-us/products/software/arena-simulation/support/training/course-agenda.html)
