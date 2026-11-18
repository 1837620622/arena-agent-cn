# Arena Agent 中文版

在 Windows 上通过 MCP 或命令行操作 Rockwell Arena。可以把流程规格生成可编辑的 `.doe`，原生检查和批跑仿真，读回逐次重复结果，并按命名视图导出流程图。

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

## 示例和验收

在新工作目录中运行示例。将下面的 `examples/bank.json` 改为克隆目录下该文件的实际路径。Arena 会在当前目录保存 `bank.doe`。

```powershell
arena-agent build "E:\path\to\arena-agent-cn\examples\bank.json"
arena-agent validate bank.doe --reps 3
arena-agent results bank.doe --per-rep
```

`validate` 依次调用 Arena 原生 Check 和批跑，报告布局警告、结束时间、重复次数、原生结果来源和统计项数量，并比对验收前后的 DOE 文件哈希。`ok: true` 表示这些检查通过。原生 `.out` 或 `.db` 等运行文件保存在模型旁，需要隔离结果时请先复制模型及其相对路径依赖。

Arena 14 批跑以本轮原生 `.out` 中的统计为准。`results` 离线回读时，从已有的可读报告中按最后写入时间选取最新结果。

导出整图可在验收时加 `--png`。模型已保存命名视图时，可读取视图列表，按实际名称导出分区图。可调 `--ppu` 提高文字和连线分辨率。出图时保持 Arena 窗口可见且无遮挡。

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

建模参数参考 [JSON 规格](skills/arena-modeling/references/build-spec.md)。大模型先按业务环节划分命名视图，原生检查通过后导出总图和细节图，并以 100% 比例核对字号、模块边缘和分支连线。Arena 14 的报表弹窗、视口拼接和权限排查见 [兼容性说明](skills/arena-modeling/references/gotchas.md)。

## 开发和反馈

```powershell
uv sync --extra dev
uv run pytest
uv run pytest -m arena
```

第一组测试使用本地测试样本。原生测试需要 Windows、Arena 和有效许可。提交 [问题](https://github.com/1837620622/arena-agent-cn/issues) 时可附 Arena 版本、失败命令、精简模型与 `arena-agent doctor` 的相关输出。

## 来源与许可

基于 [Musaib Bin Bashir 的 arena-agent](https://github.com/MusaibBashir/arena-agent) 开发，保留原作者版权与 [MIT 许可证](LICENSE)。
