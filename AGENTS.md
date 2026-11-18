# 仓库开发说明

- 此仓库提供 Windows 下的 Arena COM 自动化、MCP、命令行和 arena-modeling 技能。
- 核心在 `src/arena_agent/`，技能在 `skills/arena-modeling/`，公开示例在 `examples/`。
- 本地用 `uv sync --extra dev` 安装依赖。使用 `uv run pytest` 运行离线测试。已安装 Arena 时运行 `uv run pytest -m arena`。
- Arena 14 是本仓库的原生验收重点。更新前复验冷启动、MCP 重连、Check、批跑、报告读回以及命名视图导图。
- `upstream` 指向原作者仓库。上游有更新时先比较提交和行为，合入适用于本仓库的改动，再复验 Arena 14 和中文技能入口。
- 打包前核对许可证、安装源、技能入口和测试状态。发布到 `1837620622/arena-agent-cn`，避免提交本机配置、私有模型或原生运行临时文件。
