"""arena-agent: drive Rockwell Arena from the command line or from an AI agent. Every command prints JSON.

Exit codes: 0 success, 1 the result reports an error or ok: false, 2 a bad argument or usage.

  arena-agent doctor                            # is everything in place? (Arena, COM, edition, AI clients)
  arena-agent install [--client NAME ...]       # register the MCP server and skill with Claude Code / Desktop / Codex
  arena-agent uninstall [--client NAME ...]
  arena-agent mcp                               # MCP server on stdio (AI clients start this themselves)
  arena-agent edition limited|full|auto         # record whether this Arena is a size-limited edition
  arena-agent guide [TOPIC]                     # print the arena-modeling guide (topics: skill, build-spec, ...)

  arena-agent info
  arena-agent panels
  arena-agent defs BasicProcess
  arena-agent operands BasicProcess Create
  arena-agent reference Process [--panel BasicProcess]      # Arena's own operand reference for a module type
  arena-agent inspect model.doe [out.json] [--full]
  arena-agent build  spec.json                  # new model, or extend "base" and save as "save_as"
  arena-agent set    model.doe Module "Operand=Value" ["Operand=Value" ...] [--no-save]
  arena-agent setup  model.doe "replications=10" "replication_length=480" ...   # Run Setup
  arena-agent delete model.doe Module [Module ...]
  arena-agent check  model.doe
  arena-agent run    model.doe [--reps N] [--set Module:Operand=Value ...] [--show EXPR ...] [--measures S ...]
                                [--per-rep] [--timeout SECONDS]
  arena-agent scenarios model.doe cases.json [--reps N] [--measures S ...] [--timeout SECONDS]
  arena-agent results model.doe [--per-rep] [--all]   # statistics of the last run (no new run)
  arena-agent views model.doe                   # list named views
  arena-agent screenshot model.doe out.png [--view NAME | --region X0 Y0 X1 Y1] [--ppu P] [--tiles N]
  arena-agent animation-frame model.doe out.png --view NAME --time T [--speed S] [--timeout SECONDS]
  arena-agent animation-frames model.doe out_dir --view NAME --time T [T ...] [--speed S] [--prefix P]
  arena-agent validate model.doe [--reps N] [--png out.png] [--view NAME] [--ppu P] [--timeout SECONDS]
  arena-agent close  [model.doe ...] [--save]
"""
import json
import sys

from . import __version__


def out(obj):
    print(json.dumps(obj, indent=2, default=str))


def opt_list(args, flag):
    """Values after --flag up to the next --option."""
    if flag not in args:
        return []
    vals = []
    for x in args[args.index(flag) + 1:]:
        if x.startswith("--"):
            break
        vals.append(x)
    return vals


def batch_timeout(args):
    if "--timeout" not in args:
        return None
    i = args.index("--timeout") + 1
    if i == len(args) or args[i].startswith("--") or args.count("--timeout") != 1:
        raise ValueError("--timeout needs one value in seconds")
    return float(args[i])


# How many values each option takes, so the command's own arguments can be found wherever the options sit.
ONE_VALUE_FLAGS = {"--reps", "--png", "--view", "--ppu", "--tiles", "--speed", "--timeout", "--prefix",
                   "--client", "--panel"}
LIST_VALUE_FLAGS = {"--show", "--measures", "--set", "--time"}
FOUR_VALUE_FLAGS = {"--region"}


def positional(args):
    """The command's own arguments: every token that is neither an option nor a value belonging to one. An option may
    stand before the file names too (`run --reps 5 model.doe`), not only after them."""
    res, i = [], 0
    while i < len(args):
        x = args[i]
        if x in FOUR_VALUE_FLAGS:
            i += 5
        elif x in ONE_VALUE_FLAGS:
            i += 2
        elif x in LIST_VALUE_FLAGS:
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                i += 1
        elif x.startswith("--"):
            i += 1  # an option this command does not know: skip the flag itself
        else:
            res.append(x)
            i += 1
    return res


def kv(items):
    """["Operand=Value", ...] -> {"Operand": "Value"}"""
    res = {}
    for x in items:
        if "=" not in x:
            raise ValueError(f"{x!r} is not Operand=Value")
        k, v = x.split("=", 1)
        res[k] = v
    return res


def parse_changes(items):
    """["Doctor:Capacity=4", ...] -> {"Doctor": {"Capacity": "4"}}"""
    ch = {}
    for x in items:
        if ":" not in x or "=" not in x.split(":", 1)[1]:
            raise ValueError(f"{x!r} is not Module:Operand=Value")
        mod, rest = x.split(":", 1)
        k, v = rest.split("=", 1)
        ch.setdefault(mod, {})[k] = v
    return ch


def num(s):
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def com(fn, *args, **kwargs):
    import pythoncom
    from . import core
    pythoncom.CoInitialize()
    try:
        return fn(core.app(), *args, **kwargs)
    except core.ArenaError as e:
        return {"error": str(e)}
    except Exception as e:  # a COM failure or an unexpected model state: JSON, not a traceback
        return {"error": f"{type(e).__name__}: {e}"}


# Arguments each command needs (the ones with their own validation are not listed), and their usage line.
ARGUMENTS = {"defs": 1, "operands": 2, "reference": 1, "inspect": 1, "build": 1, "set": 2, "setup": 2, "delete": 2,
             "check": 1, "run": 1, "scenarios": 2, "results": 1, "views": 1, "screenshot": 2, "animation-frame": 2,
             "animation-frames": 2, "validate": 1}
USAGE = {"defs": "arena-agent defs BasicProcess",
         "operands": "arena-agent operands BasicProcess Create",
         "reference": "arena-agent reference Process [--panel BasicProcess]",
         "inspect": "arena-agent inspect model.doe [out.json] [--full]",
         "build": "arena-agent build spec.json",
         "set": "arena-agent set model.doe Module Operand=Value [Operand=Value ...]",
         "setup": "arena-agent setup model.doe replications=10 replication_length=480",
         "delete": "arena-agent delete model.doe Module [Module ...]",
         "check": "arena-agent check model.doe",
         "run": "arena-agent run model.doe [--reps N] [--set Module:Operand=Value ...]",
         "scenarios": "arena-agent scenarios model.doe cases.json [--reps N]",
         "results": "arena-agent results model.doe [--per-rep] [--all]",
         "views": "arena-agent views model.doe",
         "screenshot": "arena-agent screenshot model.doe out.png [--view NAME] [--ppu P] [--tiles N]",
         "animation-frame": "arena-agent animation-frame model.doe out.png --view NAME --time T",
         "animation-frames": "arena-agent animation-frames model.doe out_dir --view NAME --time T [T ...]",
         "validate": "arena-agent validate model.doe [--reps N] [--png out.png]"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    pos = positional(args)
    if len(pos) < ARGUMENTS.get(cmd, 0):
        out({"error": f"{cmd} needs at least {ARGUMENTS[cmd]} argument(s)", "usage": USAGE.get(cmd, "")})
        return 2
    try:
        return dispatch(cmd, args, pos)
    except (ValueError, KeyError, IndexError, OSError) as e:  # a bad argument or a missing file: JSON, no traceback
        out({"error": f"{type(e).__name__}: {e}", "usage": USAGE.get(cmd, "")})
        return 2


def dispatch(cmd, args, pos):
    """Run one command and return its exit code."""
    if cmd in ("--version", "version"):
        out({"arena-agent": __version__})
        return 0
    if cmd == "mcp":
        from .server import main as serve
        serve()
        return 0
    if cmd == "doctor":
        from . import doctor
        res = doctor.run(start_arena="--no-arena" not in args)
        out(res)
        return 0 if res["ok"] else 1
    if cmd in ("install", "uninstall"):
        from . import install
        if "--print" in args:
            out({"mcpServers": {install.NAME: dict(zip(("command", "args"), (install.server_command()[0],
                                                                            install.server_command()[1:])))}})
            return 0
        clients = opt_list(args, "--client") or None
        bad = [c for c in clients or [] if c not in install.CLIENTS]
        if bad:
            out({"error": f"unknown client(s) {bad}; use {list(install.CLIENTS)}"})
            return 2
        out(install.install(clients) if cmd == "install" else install.uninstall(clients))
        return 0
    if cmd == "edition":
        from . import edition
        val = {"limited": True, "full": False, "auto": None}.get(pos[0] if pos else "")
        if not pos or pos[0] not in ("limited", "full", "auto"):
            out({"error": "use: arena-agent edition limited|full|auto"})
            return 2
        out({"saved_to": edition.set_limited(val), "limited": val})
        return 0
    if cmd == "guide":
        from . import paths
        topics = paths.guide_topics()
        t = pos[0] if pos else "skill"
        if t not in topics:
            out({"error": f"unknown topic {t!r}", "topics": sorted(topics)})
            return 2
        with open(topics[t], encoding="utf-8") as f:
            print(f.read())
        return 0
    if cmd == "results":
        from . import core
        out(core.last_results(pos[0], "--per-rep" in args, "--all" in args))
        return 0
    from . import core
    if cmd == "info":
        res = com(core.info)
    elif cmd == "panels":
        res = com(core.panels)
    elif cmd == "defs":
        res = com(core.defs, pos[0])
    elif cmd == "operands":
        res = com(core.operands, pos[0], pos[1])
    elif cmd == "reference":
        panel = opt_list(args, "--panel")
        try:
            res = core.template_reference(pos[0], panel[0] if panel else None)
        except core.ArenaError as e:
            res = {"error": str(e)}
        if "text" in res:
            print(res["text"])
            return 0
    elif cmd == "inspect":
        res = com(core.inspect, pos[0], "--full" in args, pos[1] if len(pos) > 1 else None)
    elif cmd == "build":
        res = com(core.build, pos[0])
    elif cmd == "set":
        res = com(core.set_data, pos[0], pos[1], kv(pos[2:]), "--no-save" not in args)
    elif cmd == "setup":
        res = com(core.setup, pos[0], {k: num(v) for k, v in kv(pos[1:]).items()}, "--no-save" not in args)
    elif cmd == "delete":
        res = com(core.delete, pos[0], pos[1:])
    elif cmd == "check":
        res = com(core.check, pos[0])
    elif cmd == "run":
        reps = opt_list(args, "--reps")
        res = com(core.run, pos[0], int(reps[0]) if reps else None, None, parse_changes(opt_list(args, "--set")),
                  opt_list(args, "--show"), True, "--per-rep" in args, timeout=batch_timeout(args))
        ms = opt_list(args, "--measures")
        if ms and "statistics" in res:
            res["statistics"] = {k: v for k, v in res["statistics"].items() if any(q.lower() in k.lower() for q in ms)}
    elif cmd == "scenarios":
        reps = opt_list(args, "--reps")
        with open(pos[1], encoding="utf-8") as f:
            cases = json.load(f)
        res = com(core.scenarios, pos[0], cases, int(reps[0]) if reps else None, None, None,
                  opt_list(args, "--measures") or None, timeout=batch_timeout(args))
    elif cmd == "views":
        res = com(core.list_named_views, pos[0])
    elif cmd == "screenshot":
        from .snapshot import snapshot
        try:
            if len(pos) != 2:
                raise ValueError("use: arena-agent screenshot model.doe out.png [--view NAME | --region X0 Y0 X1 Y1]"
                                 " [--ppu P] [--tiles N]")
            if any(flag in args and len(values) != 1
                   for flag, values in (("--ppu", opt_list(args, "--ppu")), ("--view", opt_list(args, "--view")),
                                        ("--tiles", opt_list(args, "--tiles")))):
                raise ValueError("--ppu, --view and --tiles take one value each")
            region = [float(x) for x in opt_list(args, "--region")] or None
            if region is not None and len(region) != 4:
                raise ValueError("--region takes four numbers: X0 Y0 X1 Y1")
            ppu, view, tiles = (opt_list(args, flag) for flag in ("--ppu", "--view", "--tiles"))
            pixels_per_unit = float(ppu[0]) if ppu else None
            max_tiles = int(tiles[0]) if tiles else 64
        except ValueError as e:
            out({"error": str(e)})
            return 2
        res = com(snapshot, pos[0], pos[1], region, pixels_per_unit, max_tiles,
                  view_name=view[0] if view else None)
    elif cmd == "animation-frame":
        from .snapshot import capture_animation_frame
        view, at, speed, timeout = (opt_list(args, flag) for flag in ("--view", "--time", "--speed", "--timeout"))
        try:
            if len(pos) != 2 or len(view) != 1 or len(at) != 1 or len(speed) > 1 or len(timeout) > 1:
                raise ValueError("use: arena-agent animation-frame model.doe out.png --view NAME --time T")
            simulation_time = float(at[0])
            run_speed = float(speed[0]) if speed else 0.5
            wait = float(timeout[0]) if timeout else 120
        except ValueError as e:
            out({"error": str(e)})
            return 2
        res = com(capture_animation_frame, pos[0], pos[1], view[0], simulation_time, speed=run_speed, timeout=wait)
    elif cmd == "animation-frames":
        from .snapshot import capture_animation_frames
        view, times, speed, timeout, prefix = (opt_list(args, flag) for flag in
                                               ("--view", "--time", "--speed", "--timeout", "--prefix"))
        try:
            if len(pos) != 2 or len(view) != 1 or not times or len(speed) > 1 or len(timeout) > 1 or len(prefix) > 1:
                raise ValueError("use: arena-agent animation-frames model.doe out_dir --view NAME --time T [T ...]")
            values = [float(t) for t in times]
            run_speed = float(speed[0]) if speed else 0.5
            wait = float(timeout[0]) if timeout else 120
        except ValueError as e:
            out({"error": str(e)})
            return 2
        res = com(capture_animation_frames, pos[0], pos[1], view[0], values, speed=run_speed, timeout=wait,
                  **({"prefix": prefix[0]} if prefix else {}))
    elif cmd == "validate":
        from .validation import validate
        reps = opt_list(args, "--reps")
        png = opt_list(args, "--png")
        view = opt_list(args, "--view")
        ppu = opt_list(args, "--ppu")
        try:
            if len(pos) != 1 or any(flag in args and len(values) != 1
                                    for flag, values in (("--reps", reps), ("--png", png), ("--view", view), ("--ppu", ppu))):
                raise ValueError("use: arena-agent validate model.doe [--reps N] [--png out.png] [--view NAME] [--ppu P]")
            replications = int(reps[0]) if reps else 1
            pixels_per_unit = float(ppu[0]) if ppu else None
        except ValueError as e:
            out({"error": str(e)})
            return 2
        res = com(validate, pos[0], replications, png[0] if png else None, view[0] if view else None, pixels_per_unit,
                  timeout=batch_timeout(args))
        out(res)
        return 0 if res.get("ok") else 1
    elif cmd == "close":
        res = com(core.close, pos or None, save_changes="--save" in args)
    else:
        print(__doc__)
        return 2
    out(res)
    if isinstance(res, dict) and res.get("error"):
        return 1
    if isinstance(res, dict) and res.get("ok") is False:  # a failed check, build or run: not a successful command
        return 1
    if cmd == "scenarios" and any(case.get("ok") is False for case in res.get("scenarios", [])):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
