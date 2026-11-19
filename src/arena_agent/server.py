"""MCP server: Arena as tools for any MCP client (Claude Code, Claude Desktop, Codex, Cursor, ...).

    arena-agent mcp        # stdio transport; the client starts it
"""
import json
import os
from typing import Any

from mcp.server.mcpserver import Image, MCPServer

from . import __version__, core, edition, paths, results, worker

INSTRUCTIONS = """\
在本机 Windows 上操作 Rockwell Arena，交付可编辑的 DOE 和用户需要的成果。

客户端未加载 arena-modeling 时，通过 arena_guide 的 skill 主题读取流程。需要原生操作时先查 arena_status，
用实际主版本和许可判断可用功能。仅回读报表时直接用 get_results。许可未知且影响本次任务时再确认。

先用 inspect_model 读回已有模型。需要保留原件时用 build_model 的 base 和新的 save_as，并复制相对路径依赖。
从原生 Check、实际运行和报表确认模型与结果。需要排版、图纸、动画或成品验收时读取 delivery 主题。
以用户要求确定交付文件，说明已执行的检查和当前环境能验证的范围。文件参数是本机 Windows 路径。"""

mcp = MCPServer("arena-agent", title="Arena simulation", version=__version__, instructions=INSTRUCTIONS)


def _guard(fn, *args, retry=False, **kwargs):
    try:
        return worker.call(fn, *args, retry=retry, **kwargs)
    except core.ArenaError as e:
        return {"error": str(e)}
    except Exception as e:  # COM errors carry a tuple; keep the readable part
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def arena_status() -> dict:
    """Arena's version, install folder, edition (license type, whether it is size-limited) and open models. Starts
    Arena if it is not running (allow up to a minute). Call before native operations. For offline reports,
    call get_results directly."""
    return _guard(core.info, retry=True)


@mcp.tool()
def set_edition(limited: bool | None) -> dict:
    """Record whether this Arena is a size-limited edition (Student/Evaluation: true) or a full/lab license (false);
    null forgets the answer. Use it once the user has told you, when arena_status reports edition status "unknown"."""
    p = edition.set_limited(limited)
    return {"saved_to": p, "edition": _guard(core.info, retry=True).get("edition")}


@mcp.tool()
def arena_guide(topic: str = "skill") -> dict:
    """Read the arena-modeling guide without opening Arena. Use "skill" for the overview, "build-spec" for the
    JSON format, "results" for reports and statistics, and "delivery" for layout, animation and acceptance."""
    topics = paths.guide_topics()
    p = topics.get(topic)
    if not p or not os.path.exists(p):
        return {"error": f"unknown topic {topic!r}", "topics": sorted(topics)}
    with open(p, encoding="utf-8") as f:
        return {"topic": topic, "topics": sorted(topics), "text": f.read()}


@mcp.tool()
def list_panels() -> dict:
    """Template panels attached in Arena (internal names such as BasicProcess, AdvancedProcess)."""
    return _guard(core.panels, retry=True)


@mcp.tool()
def list_module_types(panel: str = "BasicProcess") -> dict:
    """Module types on a template panel, e.g. Create, Process, Decide, Assign, Dispose on BasicProcess."""
    return _guard(core.defs, panel, retry=True)


@mcp.tool()
def module_operands(module_type: str, panel: str = "BasicProcess") -> dict:
    """Every operand of a module type with its default value (the names to use in a spec's "data")."""
    return _guard(core.operands, panel, module_type, retry=True)


@mcp.tool()
def template_reference(module_type: str, panel: str | None = None) -> dict:
    """Arena's own reference text for one module type, from the template .txt files in the Arena folder: every operand
    including hidden exit/entry points, which operands repeat, and the choice labels. Read it before guessing
    operand names for a module you have not used yet."""
    try:
        return core.template_reference(module_type, panel)
    except core.ArenaError as e:
        return {"error": str(e)}


@mcp.tool()
def inspect_model(model: str, full: bool = False, out_path: str | None = None) -> dict:
    """Every module of a .doe model (type, name, position, operand values incl. repeat rows), every connection and
    the run setup. full=true includes data modules' operands too. With out_path, writes the JSON there and returns a
    summary (use it for big models)."""
    return _guard(core.inspect, model, full, out_path, retry=True)


@mcp.tool()
def build_model(spec: dict[str, Any] | None = None, spec_path: str | None = None) -> dict:
    """Build a new model, or extend an existing one, from a build spec (pass the spec object, or spec_path to a JSON
    file). For a new model use "save_as". To preserve an existing model, pass "base" and a distinct "save_as".
    Passing "base" alone edits it in place. The spec lists modules (panel, type, x, y, data, rows/cases/resources), connections
    (from, to, exit), updates to existing modules, run_setup, declare, delete. Modules without x/y are placed from the
    connection graph (tune with "layout": dx, dy, columns, origin); "views" saves named views from module IDs, names or a
    region; "shapes" adds titles with level title|section|note. Names used by Assign, ReadWrite, Process, Hold,
    Search and Create are declared automatically, in a grid beside the flow rather than stacked, and data modules
    listed without x/y go into that same grid. Compiles at the end: read
    check_errors (each names the module at fault) and dropped_connections. Spec format: arena_guide "build-spec"."""
    if spec is None and spec_path is None:
        return {"error": "pass spec or spec_path"}
    if spec is None:
        try:
            with open(spec_path, encoding="utf-8") as f:
                spec = json.load(f)
        except (OSError, ValueError) as e:
            return {"error": f"cannot read the spec file: {type(e).__name__}: {e}"}
    return _guard(core.build, spec)


@mcp.tool()
def set_module(model: str, module: str, data: dict[str, Any], save: bool = True) -> dict:
    """Change operands of one module by name, e.g. data={"Capacity": "4"} on Resource "Doctor", then save. Values
    are strings as typed in Arena's dialogs; repeat rows are "Operand(i)". Reads every value back and reports
    invalid choices. For several modules at once use build_model with base and updates, plus save_as to preserve
    the original model."""
    return _guard(core.set_data, model, module, data, save)


@mcp.tool()
def delete_modules(model: str, names: list[str]) -> dict:
    """Delete modules by name and save."""
    return _guard(core.delete, model, names)


@mcp.tool()
def set_run_setup(model: str, run_setup: dict[str, Any], save: bool = True) -> dict:
    """Change Run Setup: replications, replication_length, replication_length_units, warm_up, warm_up_units,
    base_time_units (units: days|hours|minutes|seconds), hours_per_day, terminating_condition."""
    return _guard(core.setup, model, run_setup, save)


@mcp.tool()
def check_model(model: str) -> dict:
    """Compile the model. errors name the module at fault; dropped_connections lists connections Arena deleted;
    edition_limit_exceeded is set when the model is too big for a limited edition."""
    return _guard(core.check, model)


@mcp.tool()
def run_model(model: str, replications: int | None = None, run_setup: dict[str, Any] | None = None,
              changes: dict[str, dict[str, Any]] | None = None, show: list[str] | None = None,
              measures: list[str] | None = None, per_replication: bool = False, timeout: float | None = None) -> dict:
    """Run the model in batch mode and return every statistic across replications: average, 95% half_width, min,
    max over replications and n. Keys look like "Process.Queue.Waiting Time", "Resource.Instantaneous Utilization",
    "Entity.Total Time", "Entity.WIP", output names. run_setup and changes ({module: {operand: value}}) apply to this
    run only and are restored afterwards, without saving. show evaluates Arena expressions at the end of the last
    replication. measures keeps only statistics whose names contain one of these strings.
    timeout sets the Arena 14 batch polling limit in seconds (default 300)."""
    res = _guard(core.run, model, replications, run_setup, changes, show, True, per_replication, timeout=timeout)
    if "statistics" in res:
        res["statistics"] = results.filter_statistics(res["statistics"], measures)
    return res


@mcp.tool()
def run_scenarios(model: str, scenarios: list[dict[str, Any]], replications: int | None = None,
                  run_setup: dict[str, Any] | None = None, measures: list[str] | None = None,
                  show: list[str] | None = None, timeout: float | None = None,
                  per_replication: bool = False, csv_path: str | None = None) -> dict:
    """Run several what-if scenarios in one Arena session without saving. Each scenario is {"name", "changes":
    {module: {operand: value}}, "replications", "run_setup"}; e.g. [{"name": "3 doctors", "changes": {"Doctor":
    {"Capacity": "3"}}}, {"name": "4 doctors", "changes": {"Doctor": {"Capacity": "4"}}}]. Use the same number of
    replications in every scenario you compare. measures keeps only statistics whose names contain these strings.
    timeout sets the Arena 14 polling limit in seconds for each scenario. per_replication includes native
    replication IDs. csv_path writes one combined CSV when every scenario succeeds and has matching rows."""
    return _guard(core.scenarios, model, scenarios, replications, run_setup, show, measures, timeout=timeout,
                  per_replication=per_replication, csv_path=csv_path)


@mcp.tool()
def get_results(model: str, per_replication: bool = False, include_empty: bool = False,
                measures: list[str] | None = None, csv_path: str | None = None) -> dict:
    """Read statistics offline. Pass a DOE to select the newest readable companion report, or a .db or .out path
    to use that exact report. per_replication adds native replication IDs and values. Set csv_path to write a
    summary or replication CSV, and measures to filter by case-insensitive statistic name fragments."""
    try:
        return results.last_results(model, per_replication, include_empty, measures=measures, csv_path=csv_path)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def list_named_views(model: str) -> dict:
    """Read saved Arena view names and their bounds as [x0, y0, x1, y1] in model units."""
    return _guard(core.list_named_views, model, retry=True)


@mcp.tool()
def screenshot_model(model: str, out_path: str | None = None, region: list[float] | None = None,
                     pixels_per_unit: float | None = None, view_name: str | None = None,
                     max_tiles: int = 64) -> Any:
    """Export an Arena PNG by view_name or by region [x0, y0, x1, y1]. Omit both to export the whole flowchart.
    Without pixels_per_unit the export prefers Arena's current zoom. A larger region is tiled up to max_tiles.
    The scale can be reduced to fit the tile count and image size limits. Set pixels_per_unit to force a scale. For animation,
    first pause with the target region visible in Arena: the paused snapshot captures one frame without changing the
    current view."""
    from .snapshot import snapshot
    out_path = out_path or os.path.splitext(os.path.abspath(model))[0] + "_flowchart.png"
    res = _guard(snapshot, model, out_path, region, pixels_per_unit, max_tiles, view_name=view_name)
    if "error" in res:
        return res
    return [json.dumps(res), Image(path=res["out"])]


@mcp.tool()
def capture_animation_frame(model: str, out_path: str, view_name: str, simulation_time: float,
                            speed: float = 0.5, timeout: float = 120) -> Any:
    """Play one replication with Arena 14's visible 2D animation, pause at simulation_time, and export a PNG.
    Time is in the model's base units. Select an existing named view. Ends the temporary run afterward.
    Arena may write native reports beside the model. Use a working copy when existing reports matter."""
    from .snapshot import capture_animation_frame
    res = _guard(capture_animation_frame, model, out_path, view_name, simulation_time, speed=speed, timeout=timeout)
    if "error" in res:
        return res
    return [json.dumps(res), Image(path=res["out"])]


@mcp.tool()
def capture_animation_frames(model: str, out_dir: str, view_name: str, simulation_times: list[float],
                             speed: float = 0.5, timeout: float = 120, prefix: str = "frame") -> Any:
    """Export a sequence of animation frames (Arena 14) at increasing simulation times, to check that motion,
    the clock, queues and status text at 5-8 key moments. Each frame replays the run from zero. Timeout is the
    wait limit for one frame. Returns the frame list and the first three images."""
    from .snapshot import capture_animation_frames as render_frames
    res = _guard(render_frames, model, out_dir, view_name, simulation_times, speed=speed, timeout=timeout,
                 prefix=prefix)
    if "error" in res:
        return res
    return [json.dumps(res)] + [Image(path=f["out"]) for f in res["frames"][:3]]


@mcp.tool()
def validate_model(model: str, replications: int = 1, png_path: str | None = None,
                   view_name: str | None = None, pixels_per_unit: float | None = None,
                   timeout: float | None = None) -> dict:
    """Check native compilation, layout warnings, batch results, and optionally export a PNG. Verify the DOE file
    stays unchanged. Arena must be visible for PNG export. Native reports are written next to the model.
    timeout sets the Arena 14 polling limit in seconds for the batch step."""
    from .validation import validate
    return _guard(validate, model, replications, png_path, view_name, pixels_per_unit, timeout=timeout)


@mcp.tool()
def close_models(models: list[str] | None = None, save_changes: bool = False) -> dict:
    """Close idle, saved models (all open models when none are given). With save_changes=true, explicitly save
    pending changes to an already named model before closing. Active runs always stay open."""
    return _guard(core.close, models, retry=False, save_changes=save_changes)


def main():
    mcp.run("stdio")
