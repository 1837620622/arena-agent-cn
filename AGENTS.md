# 仓库开发说明

- 此仓库提供 Windows 下的 Arena COM 自动化、MCP、命令行和 arena-modeling 技能。
- 核心在 `src/arena_agent/`，技能在 `skills/arena-modeling/`，公开示例在 `examples/`。
- 本地用 `uv sync --extra dev` 安装依赖。使用 `uv run pytest` 运行离线测试。已安装 Arena 时运行 `uv run pytest -m arena`。
- Arena 14 与 16 分别核验版本分流和报表读回。涉及界面导图时，在相应原生版本的可见窗口复验 PNG。
- 打包前核对许可证、安装源、技能入口和测试状态。发布到 `1837620622/arena-agent-cn`，避免提交本机配置、私有模型或原生运行临时文件。
