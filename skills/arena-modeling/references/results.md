# Arena 报表、逐次统计和方案比较

## 找到本轮报表

- `run_model` 完成后检查 `ok`、`errors`、`replications`、`end_time` 和 `results_source`。Arena 14 批跑读本轮 SIMAN `.out`，Arena 16 优先读本轮原生 SQLite `.db`。遇到 `timed_out`、`edition_limit_exceeded` 或意外提前停止，先定位原因并重新取得完整运行结果。
- `get_results` 可离线读取报表。传 DOE 路径时，根据更新时间选择最近可读的同名报表。已知来源时直接传 `.out` 或 `.db`，再核对返回的 `source` 和 `ok`。同一个 DOE 连续运行会更新旁边的原生文件，比较旧结果时应保留各自的报表或导出表。
- `measures=["Waiting", "Utilization"]` 按统计名片段筛选，忽略大小写。找不到要求的统计时，先取全部名称检查模型是否记录该指标以及名称是否相符。

## 读懂统计口径

| 字段或类型 | 验收时看什么 |
| --- | --- |
| `average`、`n` | 在跨重复汇总中，分别是有效重复结果的均值和参与汇总的重复数 |
| `half_width` | 跨重复 95% 置信区间半宽。SQLite 汇总按重复结果使用 t 分布计算，只有一次有效重复时为空。SIMAN 保留原生表字段及口径 |
| `min`、`max` | 跨重复汇总的最小和最大重复结果。单次文本报表的 `minimum`、`maximum` 按该表的观测口径解释 |
| `tally` | 每次重复中对事件观测值取平均，例如 `Examination.Queue.Waiting Time` 或 `Patient.Total Time` |
| `time-persistent` | 在一次重复内按时间加权，例如 `Examination.Queue.Number Waiting`、`Doctor.Instantaneous Utilization` 和 `Patient.WIP` |
| `counter`、`output` | 计数器或重复结束时记录的输出，如 `System.NumberOut` 或模型定义的统计表达式 |

SIMAN 文本记录可含 `section`、`reps`、`value`、`count`、`limit`、`average` 和原生的 `(Insuf)`、`(Corr)` 等标记。SQLite 记录使用 `kind`、`source` 等字段。同名指标来自不同数据源时须分别核对。

`per_replication=true` 返回相互对应的 `replication_ids` 和 `replications`。按报表中的实际编号配对。原始编号的缺口保留下来，有编号无观测时值为空。只有汇总表的报表可用于读汇总结果，逐次分析需取得原生重复数据。

## CSV 输出

```powershell
arena-agent results bank.out --csv bank-summary.csv --measures Waiting
arena-agent results bank.out --per-rep --csv bank-replications.csv --measures Waiting
arena-agent scenarios bank.doe cases.json --reps 10 --per-rep --csv comparison.csv
```

MCP 的 `get_results` 和 `run_scenarios` 通过 `csv_path` 指定文件名，`per_replication` 切换到逐次表。CSV 使用 UTF-8 BOM，包含报告路径、统计名称，以及适用时的场景名称和场景编号。逐次表用空单元格表示已记录的重复没有观测值。

场景仿真全部成功且每组都有匹配行后才写入合并 CSV。某个场景没有匹配行时，`csv_error` 会指出场景编号与名称，已有 CSV 可以继续使用。打开表格核对所需的场景、统计和重复编号。导出报错时检查筛选条件、`.csv` 后缀及目标文件是否受占用。

## 运行设计和合理性

- 终止型系统用真实的初始状态和终止条件，例如营业班次和清空要求。稳态分析应有足够长的运行窗口，并用队列、利用率随时间的变化选择热身。说明模型的基础时间单位、运行长度、热身和重复数。
- 按题目规定或估计精度设置重复次数。允许试跑时先拿一组有效重复估计半宽，再依目标精度增加。计算 `n × (当前半宽 / 目标半宽)²` 可以估计需要的重复数，最终以实际半宽复核。
- 比较方案时保持指标定义、计量单位、终止条件、热身及重复次数可比。使用同一组可比随机流设计时，按真实编号配对，考察各重复的差值及其区间。独立方案按独立样本分析，各自的置信区间用于分别描述平均值。
- 对实体数做流量平衡，将初始在制、实际进入、实际退出和终止时在制放在同一口径核对。简单固定容量队列可用到达率、平均服务时间及资源数估计利用率。符合 M/M/c 稳态假设时可与 Erlang C 对比。利用率长期接近 1 且队列持续增长时检查负荷、资源日程、热身和观测窗口。
- 需要说明结果时，给出运行配置、实际报表来源、指定指标及其单位，以及可用的区间或逐次结果。交付格式以用户要求为准。
