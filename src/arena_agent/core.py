"""Drive Rockwell Arena through its COM automation object (Arena.Application).

Every public function takes the Arena application object first and returns a JSON-serialisable dict. Nothing here
prints: the CLI prints the result, the MCP server returns it.
"""
import json
from contextlib import contextmanager
from functools import lru_cache
from itertools import combinations
import math
import os
import re
import subprocess
import tempfile
import time
import winreg

import pythoncom
import win32com.client as wc
import win32api
import winerror

from . import edition
from . import results as resultsmod

SEARCH_NAME = 2  # smFindName
CLSID_KEY = r"Arena.Application\CLSID"
ARENA_14 = 14
COMPATIBILITY_MODES = {ARENA_14: "arena14", 16: "arena16"}

# Friendly exit names -> exit operand. Arena's exit points are hidden operands (see BasicProcess.txt /
# AdvancedProcess.txt in the Arena folder).
EXITS = {
    "next": "Next Label", "true": "Next Label Yes", "false": "Next Label No", "else": "Next Label No",
    "found": "Next Label Found", "notfound": "Next Label None", "none": "Next Label None",
    "removed": "Next Label Removed", "duplicate": "Next Label",
}
ORIGINAL_EXIT = {"Separate": "Next Label Orig", "Clone": "Next Label Orig", "Remove": "Next Label"}
# Entry points: most modules use "Label"; Hold's is "QLabel". (A connection to Hold's "HoldLabel" is accepted but
# Check() silently deletes it, so it must not be tried.)
ENTRIES = {"Hold": ("QLabel",)}

# Operands whose display label Arena maps to an internal value; anything else is stored verbatim.
CHOICES = {
    ("Decide", "Type"): {"With", "If", "NWith", "NIf"},
    ("Separate", "Type"): {"Split", "Duplicate"},
    ("Hold", "Type"): {"Wait", "Scan", "Infinite"},
    ("Search", "Type"): {"Batch", "Queue", "Expression"},
    ("ReadWrite", "Type"): {"ReadFile", "WriteFile", "ReadKeyboard", "WriteScreen"},
    ("Create", "Interarrival Type"): {"Random", "Schedule", "Constant", "Expression"},
    ("Process", "Action"): {"D", "SD", "SDR", "DR"},
    ("Process", "Type"): {"Standard", "Submodel"},
    ("Process", "DelayType"): {"Triangular", "Normal", "Uniform", "Constant", "Expression"},
}
# Display labels Arena stores under another name (read back differently, but correct)
MAPPED = {("Record", "Type"): {"Time Interval": "Interval", "Time Between": "Between", "Entity Statistics": "Entity"},
          ("Resource", "Type"): {"Fixed Capacity": "Capacity", "Based on Schedule": "Schedule"}}
# Data-module types that declare a name, and the flowchart operands that use one.
DECLARERS = {"attribute": ("BasicProcess", "Attribute"), "variable": ("BasicProcess", "Variable"),
             "queue": ("BasicProcess", "Queue"), "resource": ("BasicProcess", "Resource"),
             "entity": ("BasicProcess", "Entity")}
TIME_UNITS = {"days": 0, "hours": 1, "minutes": 2, "seconds": 3}
# Run Setup field -> IModel property, shared by reading, applying and restoring the setup.
SETUP_PROPS = {"replications": "NumberOfReplications", "replication_length": "ReplicationLength",
               "replication_length_units": "ReplicationLengthTimeUnits", "warm_up": "WarmUpPeriod",
               "warm_up_units": "WarmUpPeriodTimeUnits", "base_time_units": "BaseTimeUnits",
               "hours_per_day": "HoursPerDay", "terminating_condition": "TerminatingCondition"}
VIEW_LIMIT = 30000  # Arena's flowchart view is 16-bit: -32,768..32,767
# Default layout for modules built without an explicit x/y, the grid the auto-declared data modules go in, and the
# text sizes for flowchart titles (shapes).
LAYOUT = {"dx": 2600, "dy": 1800, "columns": 12, "origin": [0, 0]}
DECLARE_STEP = (1500, 1100)
DECLARE_COLUMNS = 6
TEXT_LEVELS = {"title": 700, "section": 480, "note": 320}


class ArenaError(Exception):
    pass


@contextmanager
def temporary_settings(model, **settings):
    previous = {key: getattr(model, key) for key in settings}
    try:
        for key, value in settings.items():
            setattr(model, key, value)
        yield
    finally:
        for key, value in previous.items():
            setattr(model, key, value)


def arena_dir(a=None):
    """Prefer the executable of a running instance, then ARENA_DIR or the registered COM server."""
    if a is not None:
        executable = safe(lambda: a.FullName)
        if executable and os.path.isabs(executable):
            return os.path.dirname(executable)
    if os.environ.get("ARENA_DIR"):
        return os.environ["ARENA_DIR"]
    # Arena 14 registers a 32-bit COM server. Python may be using the other registry view.
    for view in (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, CLSID_KEY, 0, winreg.KEY_READ | view) as key:
                clsid = winreg.QueryValue(key, None)
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32", 0,
                                winreg.KEY_READ | view) as key:
                command = winreg.QueryValue(key, None)
        except OSError:
            continue
        match = re.match(r'\s*(?:"([^"]+)"|(.+?\.exe)(?=\s|$))', command, flags=re.I)
        if match:
            return os.path.dirname(os.path.expandvars(match[1] or match[2]))
    return None


@lru_cache(maxsize=8)
def _read_reserved_words(directory):
    """SIMAN block and element names (Blocks.txt, Elements.txt in the Arena folder). An attribute or variable with one
    of these names compiles with only a warning, but the run then stops at time 0. SIMAN is case-insensitive."""
    words = set()
    for f in ("Blocks.txt", "Elements.txt"):
        p = os.path.join(directory, f)
        if os.path.exists(p):
            with open(p, errors="replace") as template:
                words.update(w.lower() for w in re.findall(r"^Module:\s*(\w+)", template.read(), re.M))
    return words


def reserved_words(a=None):
    return _read_reserved_words(arena_dir(a) or "")


def reserved_clashes(names, a=None):
    rw = reserved_words(a)
    return sorted(n for n in names if n.split(".")[0].lower() in rw and not n.lower().endswith(".queue"))


def com_major_version(version):
    """Convert Arena's COM version (for example, 1400 or 1620) to a release major. Some builds report it as text."""
    try:
        version = int(version)
    except (TypeError, ValueError):
        return None
    return version // 100 if version >= 100 else None


def app(visible=True):
    try:
        a = wc.GetActiveObject("Arena.Application")
    except pythoncom.com_error as exc:
        if exc.hresult != winerror.MK_E_UNAVAILABLE:
            raise
        directory = arena_dir()
        exe = os.path.join(directory, "Arena.exe") if directory else None
        if not exe or not os.path.exists(exe):
            raise ArenaError("Arena is not registered on this machine: install Rockwell Arena, or set ARENA_DIR to "
                             "the folder that holds Arena.exe") from exc
        major = safe(lambda: win32api.GetFileVersionInfo(exe, "\\")["FileVersionMS"] >> 16)
        a = _launch_arena14(exe) if major == ARENA_14 else wc.Dispatch("Arena.Application")
    a.Visible = visible
    return a


def _launch_arena14(exe):
    # Arena 14 opens SIM.out relative to the process working directory, including during Check.
    # COM activation can put it in Program Files or System32. Start in a writable folder instead.
    work = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "arena-agent", "work")
    os.makedirs(work, exist_ok=True)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen([exe], cwd=work, startupinfo=startup)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            return wc.GetActiveObject("Arena.Application")
        except pythoncom.com_error as exc:
            if exc.hresult != winerror.MK_E_UNAVAILABLE:
                raise
        pythoncom.PumpWaitingMessages()
        time.sleep(0.2)
    raise ArenaError(f"Arena 14 did not register its COM object (launcher status: {process.poll()}). "
                     "Open Arena manually and check its startup dialog.")


def safe(f, default=None):
    try:
        return f()
    except Exception:
        return default


def mdef(panel, name):
    defs = panel.ModuleDefinitions
    i = defs.Find(SEARCH_NAME, name, 1)
    if not i:
        raise ArenaError(f"panel {panel.Name!r} has no module type {name!r} (list them with defs)")
    return defs.Item(i)


def setdata(mod, operand, value):
    o = mod._oleobj_
    o.Invoke(o.GetIDsOfNames("Data"), 0, pythoncom.DISPATCH_PROPERTYPUT, 0, operand, value)


def getdata(mod, operand):
    return safe(lambda: mod.Data(operand))


def open_model(a, path):
    path = os.path.abspath(path)
    for i in range(1, a.Models.Count + 1):
        m = a.Models.Item(i)
        if (safe(lambda: m.FullName) or "").lower() == path.lower():
            return m
    if not os.path.exists(path):
        raise ArenaError(f"no such model file: {path}")
    return a.Models.Open(path)


def modules_by_name(m):
    res = {}
    for i in range(1, m.Modules.Count + 1):
        md = m.Modules.Item(i)
        n = getdata(md, "Name")
        if n:
            res.setdefault(n, md)
    return res


def kind(mod):
    return safe(lambda: mod.Definition.Name, "")


def expand(ms, *, flat_record=False):
    """A module spec's data as an ordered list of (operand, value). "rows" lists repeat-group rows in order:
    {"rows": [{"Type": "Attribute", "AName": "x", "Value": "1"}, ...]} -> Type(1), AName(1), Value(1), Type(2) ...
    "cases" does the same for N-way Decide conditions (N If, N Value), "resources" for Process resources."""
    items = list((ms.get("data") or {}).items())
    for key in ("rows", "cases", "resources"):
        for i, row in enumerate(ms.get(key) or [], 1):
            if not isinstance(row, dict):
                raise ArenaError(f"{key} must be a list of objects; entry {i} is {row!r}")
            if key == "rows" and flat_record:
                if i > 1:
                    raise ArenaError("This Record template takes one row. Use a separate Record module for each statistic.")
                items.extend(row.items())
            else:
                items.extend((f"{k}({i})", v) for k, v in row.items())
    return [(k, str(v)) for k, v in items]


def _expand_for_definition(spec, definition, major):
    flat_record = False
    if spec.get("rows") and definition.Name == "Record":
        def has_repeat_group():
            operands = definition.Operands
            return any(operands.Item(i).Array for i in range(1, operands.Count + 1))

        flat_record = not safe(has_repeat_group, major != ARENA_14)
    return expand(spec, flat_record=flat_record)


def apply_data(mod, items, report, label):
    """Set operands in order, read each back, and record anything Arena did not keep."""
    t = kind(mod)
    for k, v in items:
        try:
            setdata(mod, k, v)
        except pythoncom.com_error as e:
            report["errors"].append(f"{label}: cannot set {k}={v!r} ({e.args[1] if len(e.args) > 1 else e})")
            continue
        got = getdata(mod, k)
        base = re.sub(r"\(\d+\)$", "", k)
        allowed = CHOICES.get((t, base))
        if allowed is not None and got not in allowed:
            report["errors"].append(f"{label}: {k}={v!r} is not a valid choice (stored {got!r}; valid {sorted(allowed)})")
        elif (allowed is None and got is not None and got != v and k != "Name"
              and MAPPED.get((t, base), {}).get(v) != got):
            report["warnings"].append(f"{label}: {k} set to {v!r} but reads back {got!r}")


def used_names(ms_items, t):
    """Attribute, variable, queue, resource and entity-type names a module defines, so they can be declared."""
    attrs, vars_, queues, res, ents = set(), set(), set(), set(), set()
    d = dict(ms_items)
    if t == "Create" and d.get("Entity Type"):
        ents.add(d["Entity Type"])
    if t == "Hold" and d.get("QName"):
        queues.add(d["QName"])
    if t in ("Search", "Remove", "Search and Remove") and d.get("Queue Name"):
        queues.add(d["Queue Name"])

    def rows(op):  # row numbers present for a repeat operand: "Type" -> 1 for plain "Type", n for "Type(n)"
        found = {1} if op in d else set()
        found |= {int(mm.group(1)) for k in d for mm in [re.fullmatch(re.escape(op) + r"\((\d+)\)", k)] if mm}
        return sorted(found)

    def val(op, i):
        return d.get(f"{op}({i})", d.get(op) if i == 1 else None)

    if t == "Assign":  # every row present, not only 1..n (an update may touch row 7 alone)
        for i in rows("Type"):
            typ = val("Type", i)
            if typ == "Attribute" and val("AName", i):
                attrs.add(val("AName", i))
            if typ == "Variable" and val("VName", i):
                vars_.add(val("VName", i))
    if t == "ReadWrite":
        for i in rows("Variable Type"):
            if val("Variable Type", i) == "Attribute" and val("Att", i):
                attrs.add(val("Att", i))
    if t == "Process":  # resources named in a Seize action are not declared through COM either
        for i in rows("Resource Name"):
            if (val("Resource Type", i) or "Resource") == "Resource" and val("Resource Name", i):
                res.add(val("Resource Name", i))
    return attrs, vars_, queues, res, ents


def declare_position(at):
    """Where the next auto-declared data module goes. `at` is a cursor [x0, y0, count]; the modules are laid out in a
    short grid so they never stack on one point (stacked data modules are unreadable in the flowchart and in exports)."""
    x0, y0, i = at
    return x0 + (i % DECLARE_COLUMNS) * DECLARE_STEP[0], y0 - (i // DECLARE_COLUMNS) * DECLARE_STEP[1]


def declare_origin(value):
    """[x0, y0] where the auto-declared data-module grid starts. Model coordinates are whole units: a float from the
    spec (or a text one) would reach Arena's COM method as the wrong type, so fall back to the default instead."""
    try:
        x0, y0 = (int(round(float(v))) for v in list(value or [])[:2])
        return [x0, y0]
    except (TypeError, ValueError):
        return [0, -4000]


def declare(a, m, existing, names, what, at, report):
    """Create Attribute / Variable / Queue / Resource / Entity data modules for names the model does not define yet."""
    pn, tn = DECLARERS[what]
    panel = a.Panels.Attach(pn)
    d = mdef(panel, tn)
    for n in sorted(names):
        if n in existing:
            continue
        x, y = declare_position(at)
        at[2] += 1
        mod = m.Modules.Create(panel, d, x, y)
        setdata(mod, "Name", n)
        existing[n] = mod
        report["declared"].append(f"{what} {n}")


def connect(m, made, c, report):
    src, dst = made.get(c.get("from")), made.get(c.get("to"))
    ex = str(c.get("exit") or "next").lower()
    idx = None
    if ex.startswith("case"):
        try:
            idx = int(ex.split(":")[1])
        except (IndexError, ValueError):
            report["errors"].append(f"connect {c.get('from')} -> {c.get('to')}: exit {ex!r} must be case:N with a "
                                    f"condition number (case:1, case:2 ...)")
            return
        operand = "N Next Label Yes"
    elif ex == "original":
        operand = ORIGINAL_EXIT.get(kind(src), "Next Label")
    elif ex in EXITS:
        operand = EXITS[ex]
    else:
        report["errors"].append(f"connect {c.get('from')} -> {c.get('to')}: unknown exit {ex!r} (valid: {sorted(EXITS)}, "
                                f"case:N, original)")
        return
    last = None
    for entry in ENTRIES.get(kind(dst), ("Label",)):
        try:
            if idx is None:
                m.Connections.Create(src, dst, operand, entry)
            else:
                m.Connections.Create(src, dst, operand, entry, idx, 1)
            return
        except pythoncom.com_error as e:
            last = e
    report["errors"].append(f"connect {c.get('from')} -[{ex}]-> {c.get('to')}: {last}")


# ---------------------------------------------------------------------------------------------------- information

def info(a):
    lic = safe(lambda: a.LicenseType)
    version = safe(lambda: a.Version)
    major = com_major_version(version)
    return {"name": safe(lambda: a.Name), "version": version, "version_major": major,
            "compatibility_mode": COMPATIBILITY_MODES.get(major, "default"), "arena_dir": arena_dir(a),
            "edition": edition.describe(lic), "models_open": [safe(lambda: a.Models.Item(i).FullName)
                                                             for i in range(1, a.Models.Count + 1)]}


def panels(a):
    return {"panels": [a.Panels.Item(i).Name for i in range(1, a.Panels.Count + 1)]}


def named_view_regions(model):
    views = model.NamedViews
    regions = {}
    for i in range(1, views.Count + 1):
        view = views.Item(i)
        x0, x1 = sorted((view.Left, view.Right))
        y0, y1 = sorted((view.Top, view.Bottom))
        regions[view.Name] = [x0, y0, x1, y1]
    return regions


def list_named_views(a, model):
    m = open_model(a, model)
    return {"model": m.FullName, "views": named_view_regions(m)}


def defs(a, panel):
    d = a.Panels.Attach(panel).ModuleDefinitions
    return {"panel": panel, "modules": [d.Item(i).Name for i in range(1, d.Count + 1)]}


def operands(a, panel, module_type):
    ops = mdef(a.Panels.Attach(panel), module_type).Operands
    return {"panel": panel, "type": module_type,
            "operands": [{"name": ops.Item(i).Name, "default": safe(lambda: ops.Item(i).DefaultValue)}
                         for i in range(1, ops.Count + 1)]}


def template_reference(module_type, panel=None):
    """The section of Arena's own template reference (<Panel>.txt in the Arena folder) for one module type: every
    operand, including the hidden exit/entry points, and the choice labels."""
    d = arena_dir()
    if not d:
        raise ArenaError("Arena folder not found; set ARENA_DIR")
    files = [f"{panel}.txt"] if panel else sorted(f for f in os.listdir(d) if f.endswith(".txt"))
    for f in files:
        p = os.path.join(d, f)
        if not os.path.exists(p):
            continue
        with open(p, errors="replace") as template:
            txt = template.read()
        mm = re.search(rf"^Module:\s*{re.escape(module_type)}\s*$(.*?)(?=^Module:|\Z)", txt, re.M | re.S)
        if mm:
            return {"file": p, "module": module_type, "text": ("Module: " + module_type + mm.group(1)).rstrip()}
    raise ArenaError(f"no module {module_type!r} in the template references in {d}")


def inspect(a, model, full=False, out_path=None):
    """Every module (type, name, position, operands incl. repeat rows), every connection, run setup. Operands still at
    their default value are left out unless full (full also includes data modules' operands)."""
    m = open_model(a, model)
    opcache, mods = {}, []
    for i in range(1, m.Modules.Count + 1):
        md = m.Modules.Item(i)
        d = md.Definition
        t = safe(lambda: d.Name)
        is_data = bool(safe(lambda: d.DataModule, False))
        sh = safe(lambda: md.shape)
        rec = {"type": t, "name": getdata(md, "Name"), "data_module": is_data,
               "x": safe(lambda: sh.Left), "y": safe(lambda: sh.Top)}
        if full or not is_data:
            if t not in opcache:
                ops = d.Operands
                opcache[t] = [(ops.Item(j).Name, safe(lambda: ops.Item(j).DefaultValue),
                               bool(safe(lambda: ops.Item(j).Array, False)))
                              for j in range(1, ops.Count + 1)]
            vals = {}
            for on, default, is_array in opcache[t]:
                v = getdata(md, on)
                if v not in (None, "") and (full or v != default or on == "Name"):
                    vals[on] = v
                if not is_array and v in (None, ""):
                    continue  # a single-value operand with no value has no rows to read
                r = 2
                while True:  # repeat-group rows, also when row 1 was left empty
                    v2 = getdata(md, f"{on}({r})")
                    if v2 is None:
                        break
                    if full or v2 != default:
                        vals[f"{on}({r})"] = v2
                    r += 1
            rec["ops"] = vals
        mods.append(rec)
    res = {"model": m.FullName, "modules": mods, "connections": [{"from": s, "to": d} for s, d in connection_list(m)],
           "run_setup": run_setup(m)}
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1, default=str)
        return {"wrote": os.path.abspath(out_path), "modules": len(mods), "connections": len(res["connections"])}
    return res


def run_setup(m):
    units = {v: k for k, v in TIME_UNITS.items()}
    g = lambda k: safe(lambda: getattr(m, k))  # noqa: E731
    return {"replications": g("NumberOfReplications"), "replication_length": g("ReplicationLength"),
            "replication_length_units": units.get(g("ReplicationLengthTimeUnits"), g("ReplicationLengthTimeUnits")),
            "warm_up": g("WarmUpPeriod"), "warm_up_units": units.get(g("WarmUpPeriodTimeUnits"),
                                                                     g("WarmUpPeriodTimeUnits")),
            "base_time_units": units.get(g("BaseTimeUnits"), g("BaseTimeUnits")),
            "hours_per_day": g("HoursPerDay"), "terminating_condition": g("TerminatingCondition")}


def apply_run_setup(m, setup, report):
    """setup: {"replications", "replication_length", "replication_length_units", "warm_up", "warm_up_units",
    "base_time_units", "hours_per_day", "terminating_condition"}; units are days|hours|minutes|seconds."""
    if setup and not isinstance(setup, dict):
        report["errors"].append(f"run setup must be an object of field: value, not {type(setup).__name__}")
        return
    for k, v in (setup or {}).items():
        if k not in SETUP_PROPS:
            report["errors"].append(f"run setup: unknown field {k!r} (valid: {sorted(SETUP_PROPS)})")
            continue
        if k.endswith("units"):
            if str(v).lower() not in TIME_UNITS:
                report["errors"].append(f"run setup: {k}={v!r}; use one of {sorted(TIME_UNITS)}")
                continue
            v = TIME_UNITS[str(v).lower()]
        try:
            setattr(m, SETUP_PROPS[k], v)
        except pythoncom.com_error as e:
            report["errors"].append(f"run setup: cannot set {k}={v!r} ({e})")


def run_setup_raw(m):
    """The Run Setup exactly as COM reports it, to be put back after a run that changed it in memory."""
    return {key: safe(lambda: getattr(m, prop)) for key, prop in SETUP_PROPS.items()}


# ---------------------------------------------------------------------------------------------------- editing

def module_key(ms, index):
    """How a spec entry is referred to: its id, else its Name (connections accept either)."""
    return ms.get("id") or (ms.get("data") or {}).get("Name") or f"#{index}"


def layout_options(spec, notes=None):
    """dx, dy, origin x/y and columns of the automatic layout. A spec's "layout" may hold text or a short list by
    mistake; that falls back to the default spacing with a warning instead of failing the whole build."""
    opts = dict(LAYOUT)
    if isinstance(spec.get("layout"), dict):
        opts.update(spec["layout"])
    try:
        dx, dy = float(opts["dx"]), float(opts["dy"])
        ox, oy = (float(v) for v in opts["origin"])
        columns = max(1, int(opts["columns"]))
        if dx <= 0 or dy <= 0 or not all(math.isfinite(v) for v in (dx, dy, ox, oy)):
            raise ValueError("spacing and origin must be finite")
    except (TypeError, ValueError, OverflowError):
        if notes is not None:
            notes.append("layout: dx, dy must be positive numbers, columns a whole number and origin two numbers; "
                         "the default 2600 x 1800, 12 columns was used")
        dx, dy = float(LAYOUT["dx"]), float(LAYOUT["dy"])
        ox, oy = (float(v) for v in LAYOUT["origin"])
        columns = int(LAYOUT["columns"])
    return dx, dy, ox, oy, columns


def plan_layout(spec, notes=None, skip=()):
    """Default positions for modules built without an explicit x/y. A module sits one column right of the module that
    feeds it, branches are stacked and centred around the main row, and long chains wrap into bands, so the flowchart
    reads left to right and modules never share a point. `skip` leaves modules out of the drawing (data modules, which
    belong beside the flow); `notes`, when given, collects a warning if the drawing had to be compressed to stay
    inside Arena's view."""
    dx, dy, ox, oy, columns_per_band = layout_options(spec, notes)
    keys = [module_key(ms, i) for i, ms in enumerate(spec.get("modules") or [])]
    keys = [k for k in keys if k not in skip]
    rank = {k: 0 for k in keys}
    edges = [(c.get("from"), c.get("to")) for c in spec.get("connections") or []]
    for _ in range(len(keys)):  # longest path in the graph; the loop stops early, and a cycle cannot run away
        changed = False
        for src, dst in edges:
            if src in rank and dst in rank and rank[dst] < rank[src] + 1:
                rank[dst] = rank[src] + 1
                changed = True
        if not changed:
            break
    columns = {}
    for k in keys:
        columns.setdefault(rank[k], []).append(k)
    pos, band_top, previous_half = {}, oy, 0
    order = sorted(columns)
    for start in range(0, len(order), columns_per_band):
        band = order[start:start + columns_per_band]
        tallest = max(len(columns[col]) for col in band)
        half = (tallest - 1) * dy / 2
        if start:
            band_top += previous_half + half + 2 * dy
        for i, col in enumerate(band):
            members = columns[col]
            for j, k in enumerate(members):
                pos[k] = (ox + i * dx, band_top + (j - (len(members) - 1) / 2) * dy)
        previous_half = half
    return fit_to_view(pos, dx, dy, notes)


def fit_to_view(pos, dx, dy, notes=None):
    """Keep the whole flow inside Arena's 16-bit view. A large model laid out at the requested spacing runs past
    +-32,000, where the GUI cannot scroll to it at all; the layout is moved and compressed to fit instead, and the
    caller reports that it was. Coordinates come back as whole model units."""
    if not pos:
        return pos
    limit = VIEW_LIMIT - 1500  # a module box and its label need room of their own
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x0, x1 = min(xs) - dx / 2, max(xs) + dx / 2
    y0, y1 = min(ys) - dy / 2, max(ys) + dy / 2
    sx = min(1.0, 2 * limit / max(x1 - x0, 1.0))
    sy = min(1.0, 2 * limit / max(y1 - y0, 1.0))
    if (sx < 1 or sy < 1) and notes is not None:
        notes.append(f"layout: compressed to {sx:.0%} across and {sy:.0%} down so the whole flow stays inside "
                     f"Arena's view (-32,000 to 32,000); split the model into named views or lower dx/dy for a "
                     f"clearer drawing")
    # 两个坐标轴分别平移或压缩，保留仍在画布内的原点和间距。
    tx = -limit - x0 * sx if sx < 1 else min(max(0, -limit - x0), limit - x1)
    ty = -limit - y0 * sy if sy < 1 else min(max(0, -limit - y0), limit - y1)
    if sx == sy == 1 and (tx or ty) and notes is not None:
        notes.append("layout: moved the automatic layout inside Arena's view (-32,000 to 32,000)")
    return {k: (round(x * sx + tx), round(y * sy + ty)) for k, (x, y) in pos.items()}


def data_module_keys(a, spec):
    """Keys of the spec's modules whose type is a data module (Resource, Queue, Variable, Entity ...). They belong
    beside the flow in the declaration grid, not in it: placed in the flow they read as modules of the process and
    shift the rows around them."""
    keys = set()
    for index, ms in enumerate(spec.get("modules") or []):
        try:
            panel = a.Panels.Attach(ms.get("panel", "BasicProcess"))
            d = mdef(panel, ms.get("type") or "")
        except (pythoncom.com_error, ArenaError):
            continue  # a missing or unknown type is reported by the build itself, with its module name
        if safe(lambda: d.DataModule, False):
            keys.add(module_key(ms, index))
    return keys


def existing_extent(m):
    """Bounding box (left, top, right, bottom) of the flowchart modules already in the model, or None if it has none."""
    box = None
    for i in range(1, m.Modules.Count + 1):
        md = m.Modules.Item(i)
        if safe(lambda: md.Definition.DataModule, True):
            continue
        shape = safe(lambda: md.shape)
        if shape is None:
            continue
        x0, x1 = sorted((shape.Left, shape.Right))
        y0, y1 = sorted((shape.Top, shape.Bottom))
        box = (min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1)) if box else (x0, y0, x1, y1)
    return box


def band_origin(box, dx, dy):
    """Where an extension band starts: below the existing flowchart, else to its right, else at the origin, so the
    new modules stay inside Arena's +-32,000 view instead of landing where no one can scroll."""
    left, top, right, bottom = box
    if bottom + dy <= VIEW_LIMIT:
        return [left, bottom + dy]
    if right + dx <= VIEW_LIMIT:
        return [right + dx, 0]
    return [0, 0]


def create_views(m, views, report):
    """Named views from a spec, so list_named_views reports the sections and screenshot_model exports each one:
    {"name": "MAIN", "modules": ["Arrive", "Exam"], "margin": 600} or {"name": "DETAIL", "region": [x0, y0, x1, y1]}."""
    boxes = {}
    for i in range(1, m.Modules.Count + 1):
        md = m.Modules.Item(i)
        if safe(lambda: md.Definition.DataModule, True):
            continue
        shape = safe(lambda: md.shape)
        if shape is not None:
            boxes.setdefault(getdata(md, "Name"), shape)
    made = []
    for v in views:
        name = safe(lambda: v.get("name"))
        if not name:
            report["errors"].append(f"view {v}: a name is required")
            continue
        try:
            region = v.get("region")
            if region is None:
                margin = float(v.get("margin", 600))
                wanted = list(v.get("modules") or [])
                missing = [n for n in wanted if n not in boxes]
                if missing:
                    report["errors"].append(f"view {name}: no module named {', '.join(missing)}")
                found = [boxes[n] for n in wanted if n in boxes]
                if not found:
                    report["errors"].append(f"view {name}: give region or names of existing modules")
                    continue
                xs = sorted([b.Left for b in found] + [b.Right for b in found])
                ys = sorted([b.Top for b in found] + [b.Bottom for b in found])
                region = [xs[0] - margin, ys[0] - margin, xs[-1] + margin, ys[-1] + margin]
            x0, y0, x1, y1 = (int(round(float(x))) for x in region)
            if not (x0 < x1 and y0 < y1):
                report["errors"].append(f"view {name}: region needs increasing bounds")
                continue
            m.NamedViews.Create(name, "V", x0, y0, x1, y1)
        except (pythoncom.com_error, AttributeError, TypeError, ValueError) as e:
            report["errors"].append(f"view {name}: {e}")
            continue
        made.append({"name": name, "region": [x0, y0, x1, y1]})
    return made


def build(a, spec):
    """Build a new model from a spec, or extend "base" and save it as "save_as". See the skill's spec reference."""
    if isinstance(spec, str):
        try:
            with open(spec, encoding="utf-8") as f:
                spec = json.load(f)
        except (OSError, ValueError) as e:
            return {"error": f"cannot read the spec file {spec}: {type(e).__name__}: {e}", "model_changed": False}
    report = {"errors": [], "warnings": [], "declared": []}
    declared = spec.get("declare") or {}
    for key in ("base", "save_as"):
        if key in spec and not isinstance(spec[key], str):
            return {"error": f"{key} must be a path string", "model_changed": False}
    # refuse SIMAN reserved words before touching any model (they compile with a warning, then the run stops at 0)
    names = set(declared.get("attributes") or []) | set(declared.get("variables") or [])
    for ms in (spec.get("modules") or []) + [dict(u, type=t) for u in (spec.get("updates") or [])
                                              if isinstance(u, dict) for t in ("Assign", "ReadWrite")]:
        at, va = used_names(expand(ms), ms.get("type", ""))[:2]
        names |= at | va
    clash = reserved_clashes(names, a)
    if clash and not spec.get("allow_reserved"):
        return {"error": "SIMAN reserved words used as attribute/variable names; rename them", "names": clash,
                "model_changed": False}
    if "base" in spec:
        if "save_as" in spec and (os.path.abspath(spec["save_as"]).lower()
                                  == os.path.abspath(spec["base"]).lower()):
            return {"error": "save_as is the base model itself; give a new path so the base file is never overwritten",
                    "model_changed": False}
        m = open_model(a, spec["base"])
        if "save_as" in spec:
            os.makedirs(os.path.dirname(os.path.abspath(spec["save_as"])), exist_ok=True)  # a new folder is fine
            m.SaveAs(os.path.abspath(spec["save_as"]))  # never touch the base file
    else:
        if "save_as" not in spec:
            return {"error": "a new model needs save_as (where to save the .doe)", "model_changed": False}
        os.makedirs(os.path.dirname(os.path.abspath(spec["save_as"])), exist_ok=True)
        m = a.Models.Add()
        m.SaveAs(os.path.abspath(spec["save_as"]))
    m.Activate()
    safe(lambda: setattr(m.ActiveView, "AutoConnect", False))
    apply_run_setup(m, spec.get("run_setup"), report)
    major = com_major_version(safe(lambda: a.Version))
    existing = modules_by_name(m)
    for u in spec.get("updates") or []:
        if not isinstance(u, dict):
            report["errors"].append(f"update: each update needs an object with name and data; got {u!r}")
            continue
        mod = existing.get(u.get("name"))
        if mod is None:
            report["errors"].append(f"update: no module named {u.get('name')!r}")
            continue
        try:
            items = _expand_for_definition(u, mod.Definition, major)
        except ArenaError as e:
            report["errors"].append(f"{u.get('name')}: {e}")
            continue
        apply_data(mod, items, report, str(u.get("name")))
    auto = []
    made, n_added = {}, 0
    found = {"attribute": set(declared.get("attributes") or []),
             "variable": set(declared.get("variables") or []), "queue": set(), "resource": set(),
             "entity": set()}

    def collect(items, t):
        for what, ns in zip(DECLARERS, used_names(items, t)):
            found[what] |= ns

    overrides = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    if "base" in spec and "origin" not in overrides:
        # extending a model: put the new modules in a clear band below (or beside) what the model already shows
        box = existing_extent(m)
        if box is not None:
            dx, dy, *_ = layout_options(spec)
            overrides = dict(overrides, origin=band_origin(box, dx, dy))
    layout_notes = []
    x0, y0 = declare_origin(spec.get("declare_at"))
    cursor = [x0, y0, 0]  # placement cursor [x0, y0, count] for data modules without an explicit position
    data_keys = data_module_keys(a, spec)  # they go beside the flow, so the drawing leaves them out
    # x/y of every module the spec does not place itself; the fit inside Arena's view is reported if it kicked in
    positions = plan_layout(dict(spec, layout=overrides), layout_notes, skip=data_keys)
    for index, ms in enumerate(spec.get("modules") or []):
        name = (ms.get("data") or {}).get("Name")
        if name and name in existing:
            report["errors"].append(f"module {name!r} already exists; use 'updates' to change it")
            continue
        try:
            panel = a.Panels.Attach(ms.get("panel", "BasicProcess"))
            module_type = ms.get("type")
            if not module_type:
                raise ArenaError("a module needs 'type' (list_module_types shows the names)")
            d = mdef(panel, module_type)
            items = _expand_for_definition(ms, d, major)
        except (pythoncom.com_error, ArenaError) as e:
            report["errors"].append(f"module {name or ms.get('id')}: {e}")
            continue
        n_before = m.Modules.Count
        key = module_key(ms, index)
        if key in data_keys and ms.get("x") is None and ms.get("y") is None:
            px, py = declare_position(cursor)  # beside the flow, in the declaration grid, with the auto-declared names
            cursor[2] += 1
        else:
            px, py = positions.get(key, (1000 + 1600 * (n_added % 12), 1000 + 1600 * (n_added // 12)))
        try:  # Arena's coordinates are whole model units; a null x/y means "place it from the graph"
            cx = int(round(float(ms.get("x") if ms.get("x") is not None else px)))
            cy = int(round(float(ms.get("y") if ms.get("y") is not None else py)))
        except (TypeError, ValueError):
            report["errors"].append(f"module {name or ms.get('id')}: x and y must be numbers")
            continue
        mod = m.Modules.Create(panel, d, cx, cy)
        own = safe(lambda: mod.shape.SerialNumber)
        sides = []
        for j in range(n_before + 1, m.Modules.Count + 1):  # data modules Arena adds by itself (Entity, File, Queue ...)
            side = m.Modules.Item(j)
            if safe(lambda: side.shape.SerialNumber) == own:  # the module just created (itself a data module)
                continue
            if safe(lambda: side.Definition.DataModule, False):
                sides.append(side)
        apply_data(mod, items, report, name or ms.get("id"))
        etype = dict(items).get("Entity Type") if module_type == "Create" else None
        for side in sides:
            # a Create adds "Entity 1"; when the spec names its own entity type, rename that one instead of leaving
            # an unused entity type that shows up in every report
            if etype and kind(side) == "Entity" and etype not in existing:
                old_name = getdata(side, "Name")
                setdata(side, "Name", etype)
                existing[etype] = side
                auto.append(f"Entity {old_name!r} renamed to {etype!r} (added with {name or ms.get('id')})")
            elif etype and kind(side) == "Entity" and getdata(side, "Name") != etype:
                stray = getdata(side, "Name")  # the entity type already exists: drop the unused default one
                sel = m.ActiveView.Selection
                sel.DeselectAll()
                sel.Add(side.shape)
                sel.Delete()
                auto.append(f"Entity {stray!r} removed, unused (added with {name or ms.get('id')})")
            else:
                auto.append(f"{kind(side)} {getdata(side, 'Name')!r} (added with {name or ms.get('id')})")
        made[ms.get("id", name)] = mod
        n_added += 1
        if name:
            existing[name] = mod
        collect(items, module_type)
    for u in spec.get("updates") or []:
        if not isinstance(u, dict):
            continue  # reported when the update was applied
        try:
            collect(expand(u), kind(existing[u.get("name")]) if u.get("name") in existing else "")
        except ArenaError:
            continue  # a row this template cannot take was already reported when the update was applied
    for what in DECLARERS:  # names nothing declared yet, from the same grid cursor
        declare(a, m, existing, found[what], what, cursor, report)
    for k, mod in existing.items():  # connections may name existing modules too
        made.setdefault(k, mod)
    for c in spec.get("connections") or []:
        if c.get("from") not in made or c.get("to") not in made:
            report["errors"].append(f"connect: unknown module {c.get('from')!r} or {c.get('to')!r}")
            continue
        connect(m, made, c, report)
    for sh in spec.get("shapes") or []:  # drawing only: titles over groups of modules
        try:
            # "level" keeps one type hierarchy across the drawing: title, section, note (or an explicit height)
            height = sh.get("height") or TEXT_LEVELS.get(sh.get("level", "section"), TEXT_LEVELS["section"])
            m.Shapes.CreateText(int(round(float(sh["x"]))), int(round(float(sh["y"]))), sh["text"],
                                sh.get("font", "Arial"), int(round(float(height))),
                                int(round(float(sh.get("angle", 0)))), bool(sh.get("bold", True)),
                                bool(sh.get("italic", False)), bool(sh.get("underline", False)))
        except (pythoncom.com_error, KeyError, TypeError, ValueError) as e:
            report["errors"].append(f"shape {sh}: {e}")
    if spec.get("delete"):
        report["deleted"] = []
        delete_modules(m, spec["delete"], report)
    report["warnings"].extend(layout_notes + layout_warnings(m))
    if spec.get("views"):  # named views make each section readable in list_named_views / screenshot_model
        report["views"] = create_views(m, spec["views"], report)
    if spec.get("check", True):  # compile after deletes, so dropped connections and errors show up now, not later
        report["check_errors"], report["dropped_connections"] = check_model(m)
        report["ok"] = not report["errors"] and not report["dropped_connections"] and not any(
            e.get("level") != "warning" for e in report["check_errors"])
    else:
        report["ok"] = not report["errors"]  # not compiled on request: only the build itself is reported
    m.Save()
    return {"model": m.FullName, "modules_added": n_added, "connections": m.Connections.Count, "auto_created": auto,
            **report}


def delete_modules(m, names, report):
    """Arena's object model has no Module.Delete; select the module's shape in the view and delete the selection."""
    byname = modules_by_name(m)
    sel = m.ActiveView.Selection
    for n in names:
        mod = byname.get(n)
        if mod is None:
            report["errors"].append(f"delete: no module named {n!r}")
            continue
        shape = safe(lambda: mod.shape)
        if shape is None:
            report["errors"].append(f"delete: {n!r} has no shape in the flowchart")
            continue
        sel.DeselectAll()
        sel.Add(shape)
        sel.Delete()
        report["deleted"].append(n)


def delete(a, model, names):
    m = open_model(a, model)
    m.Activate()  # Selection and Delete act on the model's active view, so it has to be the active one
    report = {"errors": [], "deleted": []}
    delete_modules(m, names, report)
    m.Save()
    return {"model": m.FullName, **report}


def close(a, models=None):
    """Close open models (saving nothing). With no paths, closes every model this Arena has open. Arena can crash
    on Close(); a crash is reported, and the next call starts a fresh Arena."""
    closed, failed = [], []
    want = [os.path.abspath(p).lower() for p in models or []]
    for i in range(a.Models.Count, 0, -1):
        m = a.Models.Item(i)
        full = safe(lambda: m.FullName) or ""
        if not want or full.lower() in want:
            name = full
            try:
                safe(lambda: setattr(m, "QuietMode", True))  # a missing property must not abort the close
                m.Close()
                closed.append(name)
            except pythoncom.com_error as e:
                failed.append({"model": name, "error": str(e)})
                break
    return {"closed": closed, "failed": failed}


def set_data(a, model, module, data, save=True):
    """Change operands of one module ({operand: value}), declare any names it now needs, and save."""
    m = open_model(a, model)
    mod = modules_by_name(m).get(module)
    if mod is None:
        return {"error": f"no module named {module!r}", "model_changed": False}
    report = {"errors": [], "warnings": [], "declared": []}
    if not isinstance(data, dict) or not data:
        return {"error": "give at least one operand to set, as {operand: value}", "model_changed": False}
    items = [(k, str(v)) for k, v in data.items()]
    used = used_names(items, kind(mod))
    at, va = used[:2]
    clash = reserved_clashes(at | va, a)
    if clash:
        return {"error": "SIMAN reserved words used as attribute/variable names; rename them", "names": clash,
                "model_changed": False}
    apply_data(mod, items, report, module)
    existing = modules_by_name(m)
    cursor = [0, -4000, 0]  # declared data modules are spread out, never stacked on one point
    for names, what in zip(used, DECLARERS):
        declare(a, m, existing, names, what, cursor, report)
    if save:
        m.Save()
    return {"model": m.FullName, "module": module, "saved": save, **report}


def setup(a, model, run_setup_fields, save=True):
    m = open_model(a, model)
    report = {"errors": []}
    apply_run_setup(m, run_setup_fields, report)
    if save:
        m.Save()
    return {"model": m.FullName, "run_setup": run_setup(m), "saved": save, **report}


# ---------------------------------------------------------------------------------------------------- check and run

def read_err(m):
    """Arena writes check/run errors to <model>.err as binary records: a 4-byte shape serial number 20 bytes before
    the source tag (MODVAL, ...), then the message. Returns [{"module", "type", "message"}]."""
    path = safe(lambda: m.FullName)
    if not path:  # an unsaved model has no report file
        return []
    p = os.path.splitext(path)[0] + ".err"
    if not os.path.exists(p) or not os.path.getsize(p):
        return []
    with open(p, "rb") as report:
        return parse_err(report.read(), module_serials(m))


def module_serials(m):
    serials = {}
    for i in range(1, m.Modules.Count + 1):
        md = m.Modules.Item(i)
        s = safe(lambda: md.shape.SerialNumber)
        if s is not None:
            serials[int(s)] = (getdata(md, "Name"), kind(md))
    return serials


def parse_err(raw, serials):
    tags = list(re.finditer(rb"\x00{8}([A-Z][A-Z0-9]{2,11})\x00", raw))
    res, seen = [], {}
    for k, tag in enumerate(tags):
        pos = tag.start(1)
        seg = raw[tag.end():tags[k + 1].start() if k + 1 < len(tags) else len(raw)]
        texts = [t.decode().strip() for t in re.findall(rb"[ -~\n\t]{4,}", seg)]
        text = max(texts, key=len) if texts else ""
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        msg = lines[-1].strip() if lines else ""
        rec = {"source": tag.group(1).decode(), "level": "warning" if msg.upper().startswith("WARNING") else "error",
               "message": msg}
        code = [ln for ln in lines[:-1] if set(ln.strip()) != {"^"}]
        if code:  # SIMAN compile records (GGTNAM) quote the generated SIMAN code the message refers to
            rec["code"] = code
        if rec["source"] == "MODVAL" and pos >= 20:  # shape serial of the faulty module, 20 bytes before the tag
            # (other record types have unrelated numbers there)
            s = int.from_bytes(raw[pos - 20:pos - 16], "little", signed=True)
            if s in serials:
                rec["module"], rec["type"] = serials[s]
        key = (rec["message"], rec.get("module"))
        if key in seen:  # the same message repeats once per use of the symbol: count it instead
            seen[key]["count"] = seen[key].get("count", 1) + 1
            continue
        seen[key] = rec
        res.append(rec)
    if not res and raw:  # unknown layout: fall back to the printable text
        txt = raw.decode("latin-1")
        res = [{"level": "error", "message": re.sub(r"\s+", " ", s).strip()} for s in re.findall(r"[ -~]{6,}", txt)]
    return res


def connection_list(m):
    res = []
    for i in range(1, m.Connections.Count + 1):
        c = m.Connections.Item(i)
        res.append((safe(lambda: c.Source().Data("Name")), safe(lambda: c.Destination().Data("Name"))))
    return res


def layout_warnings(model):
    """Read real module bounds so collisions are reported without moving explicitly placed objects."""
    bounds = []
    for i in range(1, model.Modules.Count + 1):
        module = model.Modules.Item(i)
        if safe(lambda: module.Definition.DataModule, True):
            continue
        shape = safe(lambda: module.shape)
        if shape is None:
            continue
        x0, x1 = sorted((shape.Left, shape.Right))
        y0, y1 = sorted((shape.Top, shape.Bottom))
        bounds.append((getdata(module, "Name"), x0, y0, x1, y1))
    warnings = [f"layout: {name!r} is outside the recommended canvas range (-32000 to 32000)"
                for name, *box in bounds if any(abs(value) > 32000 for value in box)]
    warnings.extend(f"layout: {a[0]!r} overlaps {b[0]!r}" for a, b in combinations(bounds, 2)
                    if min(a[3], b[3]) > max(a[1], b[1]) and min(a[4], b[4]) > max(a[2], b[2]))
    return warnings


def check_model(m):
    """Compile the model. Returns errors, plus any connections Check() deleted (it silently drops connections made to
    an invalid entry point)."""
    m.Activate()
    path = safe(lambda: m.FullName) or ""  # an unsaved model has no report file beside it
    p = os.path.splitext(path)[0] + ".err" if path else ""
    before = os.path.getmtime(p) if p and os.path.exists(p) else 0
    conns = connection_list(m)
    settings = {"QuietMode": True}
    if com_major_version(safe(lambda: m.Application.Version)) == ARENA_14:
        settings["DisableReportDatabase"] = True
    with temporary_settings(m, **settings):
        m.Check()
    after = connection_list(m)
    fresh = bool(p) and os.path.exists(p) and os.path.getmtime(p) > before
    errs = read_err(m) if fresh else []
    dropped = [f"{s} -> {d}" for s, d in conns if (s, d) not in after]
    return errs, dropped


def check(a, model):
    m = open_model(a, model)
    errs, dropped = check_model(m)
    res = {"model": m.FullName, "ok": not dropped and not any(e.get("level") != "warning" for e in errs),
           "errors": errs, "dropped_connections": dropped, "layout_warnings": layout_warnings(m)}
    if safe(lambda: m.AreDemoLimitsExceeded):
        res["edition_limit_exceeded"] = True
        res["ok"] = False
    return res


def evaluate(m, expr):
    """Value of an Arena expression in the current (ended) run: SIMAN.StringEvaluateFormula, falling back to
    SymbolNumber + VariableValue for a plain variable `Name` or `Name(i[, j])`."""
    s = m.SIMAN
    try:
        return float(s.StringEvaluateFormula(expr))
    except Exception:
        pass
    mt = re.fullmatch(r"\s*(\w+)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?\s*", expr)
    if mt:
        i1, i2 = int(mt.group(2) or 0), int(mt.group(3) or 0)
        try:
            return float(s.VariableValue(s.SymbolNumber(mt.group(1), 0, 0), i1, i2))
        except Exception as e:
            return f"ERR {e}"
    return "ERR cannot evaluate"


def end_other_runs(a):
    """Arena runs one model at a time: a model left in run mode (State != 0, e.g. 12 after an ended run whose End()
    failed) makes Go() on any other model wait ~2 min and return without running. End them all first."""
    ended = []
    for i in range(1, a.Models.Count + 1):
        other = a.Models.Item(i)
        if safe(lambda: other.State, 0) != 0:
            safe(lambda: other.End())
            ended.append(safe(lambda: other.Name) or "(unnamed model)")
    return ended


def _run_once(a, m, show, outputs, per_replication=False):
    ended_first = end_other_runs(a)
    m.Activate()
    if com_major_version(safe(lambda: a.Version)) != ARENA_14:
        return _run_batch(m, ended_first, show, outputs, per_replication)

    # Arena 14 can open a modal DAO/Jet error during COM startup. A batch run only needs SIMAN's text report.
    with temporary_settings(m, DisableReportDatabase=True, DisplayDefaultReport=1, BatchMode=True, QuietMode=True):
        try:
            return _run_batch(m, ended_first, show, outputs, per_replication, legacy=True)
        finally:
            if safe(lambda: m.State, 0):
                safe(lambda: m.End())


def _run_batch(m, ended_first, show, outputs, per_replication, legacy=False):
    # Batch settings belong to this run: a model left in batch mode afterwards behaves oddly in the Arena GUI.
    with temporary_settings(m, QuietMode=True, BatchMode=True):
        return _run_batch_locked(m, ended_first, show, outputs, per_replication, legacy)


def _run_batch_locked(m, ended_first, show, outputs, per_replication, legacy):
    t0 = time.time()
    model_path = safe(lambda: m.FullName) or ""  # an unsaved model has no report beside it
    if legacy:
        m.Go(2)  # smGoReturn keeps the COM apartment responsive on Arena 14.
        start_deadline = time.monotonic() + 15
        state = m.State
        while state not in (7, 8, 9, 10, 12, 16, 17, 18, 19, 20, 21):
            if state == 0 and time.monotonic() >= start_deadline:
                break
            pythoncom.PumpWaitingMessages()
            time.sleep(0.05)
            state = m.State
    else:
        m.Go(1)  # smGoWait: returns when the run ends
    res = {"seconds": round(time.time() - t0, 1), "end_time": safe(lambda: m.SIMAN.RunCurrentTime),
           "replications": safe(lambda: int(m.NumberOfReplications)), "errors": []}
    if ended_first:
        res["ended_first"] = ended_first
    if res["end_time"] is None:
        res["errors"].append({"level": "error", "message": "the run did not start (no SIMAN run state after Go)"})
    elif legacy and state != 12:  # smStateRunComplete
        res["errors"].append({"level": "error", "message": f"Arena 14 run stopped before completion (State {state})"})
    p = os.path.splitext(model_path)[0] + ".err" if model_path else ""  # an unsaved model has no report beside it
    fresh = lambda: bool(p) and os.path.exists(p) and os.path.getmtime(p) > t0  # noqa: E731
    if fresh():
        res["errors"].extend(read_err(m))
    if show:  # read before End(), which resets the run; a bad expression writes its own record to .err
        before_show = read_err(m) if fresh() else []
        res["values"] = {e: evaluate(m, e) for e in show}
        after_show = read_err(m) if fresh() else []
        if len(after_show) > len(before_show):  # only what evaluating the expressions added
            res["show_errors"] = after_show[len(before_show):]
    if safe(lambda: m.AreDemoLimitsExceeded):
        res["edition_limit_exceeded"] = True
        res["errors"].append({"level": "error", "message": edition.LIMIT_MESSAGE})
    safe(lambda: m.End())
    left_state = safe(lambda: m.State, 0)
    if left_state != 0:  # still in run mode: the next run of any model would stall
        res["errors"].append({"level": "error",
                              "message": f"model still in run mode after End() (State {left_state})"})
    if outputs:
        r = (resultsmod.read(model_path, t0, per_replication, text_only=legacy) if model_path
             else {"source": None, "statistics": {}, "note": "the model has no saved path, so no report was read"})
        res["results_source"] = r["source"]
        res["statistics"] = r["statistics"]
        if r.get("note"):
            res["results_note"] = r["note"]
        if legacy and r["source"] is None and not res["errors"]:
            res["errors"].append({"level": "error", "message": "Arena 14 did not write a fresh SIMAN text report with statistics"})
    lic = resultsmod.report_license(model_path) if model_path else None
    if lic:  # the text report names the license ("STUDENT"); remember it, it tells Student from lab licenses apart
        res["report_license"] = lic
        edition.remember_report_license(lic)
    if any(edition.is_limit_message(e.get("message", "")) for e in res["errors"]):
        res["edition_limit_exceeded"] = True
    res["ok"] = not any(e.get("level") != "warning" for e in res["errors"])
    return res


def _apply_in_memory(m, changes, report):
    """changes: {module: {operand: value}}. Returns the previous values, to restore after the run."""
    if changes and not isinstance(changes, dict):
        report["errors"].append({"level": "error",
                                 "message": f"changes must be {{module: {{operand: value}}}}, not {type(changes).__name__}"})
        return {}
    byname = modules_by_name(m)
    old = {}
    for module, data in (changes or {}).items():
        mod = byname.get(module)
        if mod is None:
            report["errors"].append({"level": "error", "message": f"set: no module named {module!r}"})
            continue
        if not isinstance(data, dict) or not data:
            report["errors"].append({"level": "error",
                                     "message": f"set: give at least one operand for {module!r}, as {{operand: value}}"})
            continue
        for k, v in data.items():
            old[(module, k)] = getdata(mod, k)
        apply_data(mod, [(k, str(v)) for k, v in data.items()], report, module)
    return old


def _restore(m, old):
    byname = modules_by_name(m)
    for (module, k), v in old.items():
        if v is not None:
            safe(lambda: setdata(byname[module], k, v))


def run(a, model, reps=None, setup_fields=None, changes=None, show=None, outputs=True, per_replication=False):
    """Run a model in batch mode and return the across-replication results.
    setup_fields: run setup to use for this run ({"replications", "replication_length", ...}).
    changes: {module: {operand: value}} applied in memory for this run only and restored afterwards (no save).
    show: Arena expressions evaluated at the end of the LAST replication (e.g. "NQ(Queue1.Queue)", "OVALUE(Stat)").
    per_replication: also return each statistic's value in every replication."""
    if setup_fields is not None and not isinstance(setup_fields, dict):
        raise ArenaError("run setup must be an object of field: value")
    if reps is not None:
        if type(reps) is not int or reps < 1:
            raise ArenaError("replications must be a positive integer")
        setup_fields = dict(setup_fields or {}, replications=reps)
    m = open_model(a, model)
    report = {"errors": [], "warnings": []}
    old_setup = run_setup_raw(m)
    old = {}
    try:
        apply_run_setup(m, setup_fields, report)
        if not report["errors"]:
            old = _apply_in_memory(m, changes, report)
        # 无效参数不启动求解，避免生成一份采用了部分新参数的报表。
        res = {"ok": False, "errors": []} if report["errors"] else _run_once(a, m, show, outputs, per_replication)
    finally:
        if old:
            _restore(m, old)
        if setup_fields:  # put back the exact COM values the model had before this run
            for key in setup_fields:
                value = old_setup.get(key)
                if key in SETUP_PROPS and value is not None:
                    safe(lambda: setattr(m, SETUP_PROPS[key], value))
    res["errors"] = [{"level": "error", "message": e} if isinstance(e, str) else e for e in report["errors"]] + res[
        "errors"]
    res["ok"] = res["ok"] and not report["errors"]
    return {"model": m.FullName, **({"warnings": report["warnings"]} if report["warnings"] else {}), **res}


def scenarios(a, model, cases, reps=None, setup_fields=None, show=None, measures=None):
    """Run several scenarios in one Arena session without saving: each case is {"name", "changes": {module:
    {operand: value}}, "reps", "run_setup"}. Returns one result per case; measures limits outputs to those
    statistics (substring match, case-insensitive)."""
    out = []
    if not isinstance(cases, list):
        raise ArenaError('cases must be a list of scenarios: [{"name": ..., "changes": {module: {operand: value}}}]')
    for c in cases:
        if not isinstance(c, dict):
            raise ArenaError(f"each scenario must be an object with name/changes; got {c!r}")
        if c.get("run_setup") is not None and not isinstance(c["run_setup"], dict):
            raise ArenaError("scenario run_setup must be an object of field: value")
    if setup_fields is not None and not isinstance(setup_fields, dict):
        raise ArenaError("run setup must be an object of field: value")
    for c in cases:
        r = run(a, model, reps=c.get("reps", reps),
                setup_fields={**(setup_fields or {}), **(c.get("run_setup") or {})},
                changes=c.get("changes"), show=c.get("show", show))
        if measures:
            r["statistics"] = {k: v for k, v in r.get("statistics", {}).items()
                               if any(q.lower() in k.lower() for q in measures)}
        out.append({"name": c.get("name", f"case {len(out) + 1}"), **r})
    return {"model": os.path.abspath(model), "scenarios": out}


def last_results(model, per_replication=False, include_empty=False):
    """Results of the model's last run, from its report database or text report (no new run, no Arena needed)."""
    path = os.path.abspath(model)
    return {"model": path, **resultsmod.read(path, 0, per_replication, include_empty)}
