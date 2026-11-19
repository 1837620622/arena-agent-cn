"""Tests that need no Arena: spec expansion, name declaration, report parsing, statistics, edition, install config."""
import csv
import json
import math
import os
from shutil import copyfile
import sqlite3
from types import SimpleNamespace

import pytest

from arena_agent import cli, core, edition, install, paths, results, validation

DATA = os.path.join(os.path.dirname(__file__), "data")


def test_temporary_settings_preserve_operation_error_and_restore_other_fields():
    class Model:
        BatchMode = False
        _quiet = False

        @property
        def QuietMode(self):
            return self._quiet

        @QuietMode.setter
        def QuietMode(self, value):
            if not value:
                raise ValueError("still in run mode")
            self._quiet = value

    model = Model()
    with pytest.raises(core.ArenaError, match="breakpoint failed.*QuietMode"):
        with core.temporary_settings(model, BatchMode=True, QuietMode=True):
            raise core.ArenaError("breakpoint failed")
    assert model.BatchMode is False


def test_expand_rows_cases_resources():
    ms = {"data": {"Name": "A"}, "rows": [{"Type": "Attribute", "AName": "x", "Value": 1}],
          "cases": [{"N If": "Expression"}], "resources": [{"Resource Name": "R"}, {"Resource Name": "S"}]}
    assert core.expand(ms) == [("Name", "A"), ("Type(1)", "Attribute"), ("AName(1)", "x"), ("Value(1)", "1"),
                               ("N If(1)", "Expression"), ("Resource Name(1)", "R"), ("Resource Name(2)", "S")]


def test_used_names_assign_rows_any_order():
    items = [("Type(7)", "Variable"), ("VName(7)", "Late"), ("Type(1)", "Attribute"), ("AName(1)", "T0")]
    at, va, qu, rs, en = core.used_names(items, "Assign")
    assert at == {"T0"} and va == {"Late"} and not qu and not rs and not en


def test_used_names_process_and_create():
    items = [("Resource Type(1)", "Resource"), ("Resource Name(1)", "Doctor"), ("Resource Name(2)", "Nurse")]
    assert core.used_names(items, "Process")[3] == {"Doctor", "Nurse"}
    assert core.used_names([("Entity Type", "Patient")], "Create")[4] == {"Patient"}
    assert core.used_names([("QName", "Q1")], "Hold")[2] == {"Q1"}


def test_reserved_clashes_without_arena(monkeypatch):
    monkeypatch.setattr(core, "reserved_words", lambda _: {"wait", "group"})
    assert core.reserved_clashes({"Wait", "RWait", "Group.Queue", "group"}) == ["Wait", "group"]


def test_running_arena_selects_its_own_reserved_words(tmp_path, monkeypatch):
    older = tmp_path / "older"
    newer = tmp_path / "newer"
    for folder, name in ((older, "OlderWord"), (newer, "NewerWord")):
        folder.mkdir()
        (folder / "Blocks.txt").write_text(f"Module: {name}\n", encoding="utf-8")
    monkeypatch.setenv("ARENA_DIR", str(older))
    names = {"OlderWord", "NewerWord"}
    assert core.reserved_clashes(names) == ["OlderWord"]
    assert core.reserved_clashes(names, SimpleNamespace(FullName=str(newer / "Arena.exe"))) == ["NewerWord"]


def test_named_view_bounds_are_ordered_for_export():
    view = SimpleNamespace(Name="Detail", Left=20, Right=30, Top=100, Bottom=60)
    model = SimpleNamespace(NamedViews=SimpleNamespace(Count=1, Item=lambda _: view))
    assert core.named_view_regions(model) == {"Detail": [20, 60, 30, 100]}


def test_plan_layout_follows_connections_and_stacks_branches():
    spec = {"modules": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}],
            "connections": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "a", "to": "d"}]}
    pos = core.plan_layout(spec)
    assert pos["a"][0] < pos["b"][0] < pos["c"][0]  # one column right of the module that feeds it
    assert pos["d"][0] == pos["b"][0]  # a branch shares the column of its sibling
    assert pos["d"][1] != pos["b"][1]  # and never lands on the same point


def test_plan_layout_wraps_long_chains_and_survives_a_cycle():
    chain = {"modules": [{"id": f"m{i}"} for i in range(20)],
             "connections": [{"from": f"m{i}", "to": f"m{i + 1}"} for i in range(19)]}
    pos = core.plan_layout(chain)
    assert all(abs(x) <= 32000 and abs(y) <= 32000 for x, y in pos.values())  # Arena's view is 16-bit
    cycle = {"modules": [{"id": "x"}, {"id": "y"}],
             "connections": [{"from": "x", "to": "y"}, {"from": "y", "to": "x"}]}
    assert set(core.plan_layout(cycle)) == {"x", "y"}


def test_declared_data_modules_are_spread_out():
    cursor = [0, -4000, 0]
    points = []
    for _ in range(8):
        points.append(core.declare_position(cursor))
        cursor[2] += 1
    assert len(set(points)) == 8  # no two declared modules on one point
    assert {y for _, y in points[:6]} == {-4000}  # a row of six
    assert points[6][1] < points[0][1]  # then a second row below it


def test_plan_layout_keeps_a_large_model_inside_the_arena_view():
    chain = {"modules": [{"id": f"m{i}"} for i in range(200)],
             "connections": [{"from": f"m{i}", "to": f"m{i + 1}"} for i in range(199)]}
    notes = []
    pos = core.plan_layout(chain, notes)
    assert len(pos) == 200
    assert all(abs(x) <= 32000 and abs(y) <= 32000 for x, y in pos.values())  # the GUI could not show it otherwise
    assert notes and "compressed" in notes[0]  # and the build result says so


def test_plan_layout_returns_whole_model_units():
    spec = {"modules": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            "connections": [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}],
            "layout": {"dy": 1755}}  # an odd step used to leave half units for the second branch
    assert all(isinstance(v, int) for point in core.plan_layout(spec).values() for v in point)


def test_plan_layout_keeps_a_tall_second_band_below_the_first():
    branches = [f"branch-{i}" for i in range(9)]
    spec = {"modules": [{"id": k} for k in ["start", *branches]],
            "connections": [{"from": "start", "to": k} for k in branches], "layout": {"columns": 1}}
    pos = core.plan_layout(spec)
    assert min(pos[k][1] for k in branches) >= pos["start"][1] + 2 * 1800


def test_fit_to_view_corrects_nonzero_origin_on_each_axis():
    pos = core.fit_to_view({"a": (1200, 40000), "b": (1200, 120000)}, 2600, 1800)
    assert {x for x, _ in pos.values()} == {1200}
    assert -32000 <= pos["a"][1] < pos["b"][1] <= 32000
    assert all(abs(v) <= 32000 for v in core.fit_to_view({"a": (33000, 36000)}, 2600, 1800)["a"])


def test_declare_origin_takes_numbers_and_falls_back():
    assert core.declare_origin([100, -200]) == [100, -200]
    assert core.declare_origin(["100", "-200.4"]) == [100, -200]
    assert core.declare_origin(None) == [0, -4000]
    assert core.declare_origin(["x", "y"]) == [0, -4000]  # a text value would reach COM as the wrong type


def test_expand_tolerates_null_repeat_groups():
    assert core.expand({"data": None, "rows": None, "cases": None, "resources": None}) == []


def test_expand_rejects_a_repeat_row_that_is_not_an_object():
    with pytest.raises(core.ArenaError, match="rows"):
        core.expand({"rows": ["Type=Attribute"]})  # a bare string used to raise AttributeError


def test_layout_options_fall_back_on_bad_values():
    notes = []
    assert core.layout_options({"layout": {"dx": 1000, "dy": 900, "origin": [10, 20]}}, notes) == (
        1000.0, 900.0, 10.0, 20.0, 12)
    assert not notes
    assert core.layout_options({"layout": {"dx": "wide"}}, notes) == (2600.0, 1800.0, 0.0, 0.0, 12)
    assert core.layout_options({"layout": {"dy": 0}}) == (2600.0, 1800.0, 0.0, 0.0, 12)  # a zero step stacks modules
    assert core.layout_options({"layout": {"origin": [1, 2, 3]}}) == (2600.0, 1800.0, 0.0, 0.0, 12)
    assert core.layout_options({"layout": {"origin": [math.inf, 0]}}) == (2600.0, 1800.0, 0.0, 0.0, 12)
    assert notes and "default" in notes[0]


def test_plan_layout_can_leave_out_data_modules():
    spec = {"modules": [{"id": "a"}, {"id": "r"}], "connections": []}
    assert set(core.plan_layout(spec, None, skip={"r"})) == {"a"}  # a data module does not take a place in the flow


def test_data_module_keys_ignores_an_unknown_type():
    defs = SimpleNamespace(Count=1, Find=lambda _search, name, _flag: 1 if name == "Resource" else 0,
                           Item=lambda _index: SimpleNamespace(DataModule=True, Name="Resource"))
    app = SimpleNamespace(Panels=SimpleNamespace(Attach=lambda name: SimpleNamespace(Name=name, ModuleDefinitions=defs)))
    spec = {"modules": [{"id": "r", "type": "Resource"}, {"id": "x", "type": "NoSuchType"}]}
    assert core.data_module_keys(app, spec) == {"r"}


def test_run_setup_rejects_a_non_object():
    report = {"errors": []}
    core.apply_run_setup(SimpleNamespace(), ["replications"], report)  # a list used to raise AttributeError
    assert "object" in report["errors"][0]


def test_scenarios_reject_a_case_list_that_is_not_a_list():
    with pytest.raises(core.ArenaError, match="list of scenarios"):
        core.scenarios(None, "m.doe", {"name": "one"})  # a single object in the cases file
    with pytest.raises(core.ArenaError, match="each scenario"):
        core.scenarios(None, "m.doe", ["one"])
    with pytest.raises(core.ArenaError, match="scenario run_setup"):
        core.scenarios(None, "m.doe", [{"name": "one"}, {"name": "two", "run_setup": ["replications"]}])
    with pytest.raises(core.ArenaError, match="must agree"):
        core.scenarios(None, "m.doe", [{"name": "one"}, {"reps": 5, "replications": 6}])


def test_failed_scenarios_retain_previous_csv(tmp_path, monkeypatch):
    path = tmp_path / "previous.csv"
    path.write_bytes(b"existing")

    def run(_app, _model, *, changes, **_options):
        if changes:
            return {"ok": True, "statistics": {"Done": {"value": 12}}}
        return {"ok": False, "errors": ["failed to compile"]}

    monkeypatch.setattr(core, "run", run)
    result = core.scenarios(None, "m.doe", [{"changes": {"A": {}}}, {"name": "failed"}], csv_path=path)
    assert not result["ok"] and "every scenario" in result["csv_error"]
    assert result["scenarios"][0]["statistics"]["Done"]["value"] == 12
    assert path.read_bytes() == b"existing"


def test_plan_layout_survives_a_bad_layout_block():
    spec = {"modules": [{"id": "a"}, {"id": "b"}], "connections": [{"from": "a", "to": "b"}],
            "layout": {"dx": "wide"}}
    notes = []
    pos = core.plan_layout(spec, notes)
    assert pos["b"][0] > pos["a"][0]  # the default spacing still lays the flow out left to right
    assert notes


def test_mirrored_view_is_ordered_and_turned_back():
    pytest.importorskip("win32gui")
    from arena_agent import snapshot
    from PIL import Image

    assert snapshot._ordered((100, 200, 10, 50)) == (10, 50, 100, 200)
    assert snapshot._ordered((0, 0, 10, 10)) == (0, 0, 10, 10)

    img = Image.new("RGB", (2, 1))
    img.putpixel((0, 0), (255, 0, 0))
    img.putpixel((1, 0), (0, 0, 255))
    assert snapshot._upright(img, (0, 0, 10, 10)) is img  # upright: the tile is used as captured
    mirrored = snapshot._upright(img, (100, 0, 10, 10))  # left > right: Arena draws the view flipped
    assert mirrored.getpixel((0, 0)) == (0, 0, 255) and mirrored.getpixel((1, 0)) == (255, 0, 0)


@pytest.mark.parametrize("bounds", [(10, 20, 110, 120), (110, 20, 10, 120),
                                   (10, 120, 110, 20), (110, 120, 10, 20)])
def test_paused_crop_tracks_a_nonzero_origin_on_mirrored_axes(monkeypatch, bounds):
    from arena_agent import snapshot
    from PIL import Image

    upright = Image.new("RGB", (100, 100))
    upright.putdata([(x, y, 100) for y in range(100) for x in range(100)])
    captured = snapshot._upright(upright, bounds)
    monkeypatch.setattr(snapshot, "client_rect", lambda _: (0, 0, 100, 100))
    monkeypatch.setattr(snapshot, "_capture_pane", lambda *_: captured)
    view = SimpleNamespace(**dict(zip(("Left", "Top", "Right", "Bottom"), bounds)))
    frame, _, _ = snapshot._capture_run_frame(view, 0, (30, 55, 62, 76), None)
    assert frame.size == (32, 21)
    assert frame.tobytes() == upright.crop((20, 35, 52, 56)).tobytes()


def test_animation_frame_keeps_existing_run_active():
    from arena_agent import snapshot

    model = SimpleNamespace(Name="live", State=13, End=lambda: pytest.fail("existing run was ended"))
    app = SimpleNamespace(Version=1400, Models=SimpleNamespace(Count=1, Item=lambda _: model))
    with pytest.raises(core.ArenaError, match="still active"):
        snapshot.capture_animation_frame(app, "other.doe", "frame.png", "Detail", 5)


def test_screenshot_defaults_to_the_native_screen_scale():
    pytest.importorskip("win32gui")
    from arena_agent import snapshot

    view = SimpleNamespace(Left=0, Top=0, Right=10000, Bottom=5000)
    assert snapshot._native_ppu(view, 1500, 750) == pytest.approx(0.15)
    assert snapshot._native_ppu(SimpleNamespace(Left=5, Top=5, Right=5, Bottom=5), 100, 100) is None


def test_export_region_includes_near_titles_and_ignores_stray_shapes():
    pytest.importorskip("win32gui")
    from arena_agent import snapshot

    def box(left, top, right, bottom):
        return SimpleNamespace(Left=left, Top=top, Right=right, Bottom=bottom)

    modules = [SimpleNamespace(Definition=SimpleNamespace(DataModule=False), shape=box(0, 0, 2000, 1000)),
               SimpleNamespace(Definition=SimpleNamespace(DataModule=False), shape=box(3000, 0, 5000, 1000))]
    shapes = [box(-500, -1500, 1500, -400), box(60000, 60000, 61000, 61000)]  # a title above, a stray frame far away
    model = SimpleNamespace(Modules=SimpleNamespace(Count=2, Item=lambda i: modules[i - 1]),
                            Shapes=SimpleNamespace(Count=2, Item=lambda i: shapes[i - 1]))
    assert snapshot.content_bbox(model) == (-500, -1500, 5000, 1000)  # the title is kept, the stray shape is not


def test_export_region_falls_back_to_data_modules():
    pytest.importorskip("win32gui")
    from arena_agent import snapshot

    resource = SimpleNamespace(Definition=SimpleNamespace(DataModule=True),
                               shape=SimpleNamespace(Left=0, Top=-4000, Right=1500, Bottom=-3000))
    model = SimpleNamespace(Modules=SimpleNamespace(Count=1, Item=lambda _: resource), Shapes=None)
    assert snapshot.content_bbox(model) == (0, -4000, 1500, -3000)  # only declarations: still exportable
    empty = SimpleNamespace(Modules=SimpleNamespace(Count=0), Shapes=None)
    with pytest.raises(core.ArenaError, match="no flowchart modules"):
        snapshot.content_bbox(empty)


def test_inspect_reads_repeat_rows_of_a_group_whose_first_row_is_empty(monkeypatch):
    operands = SimpleNamespace(Count=2, Item=lambda i: [
        SimpleNamespace(Name="Name", DefaultValue="", Array=False),
        SimpleNamespace(Name="Value", DefaultValue="", Array=True)][i - 1])
    # writing row 3 creates rows 1 and 2 with defaults: row 1 is empty, row 3 carries the value
    data = {"Name": "Late", "Value": "", "Value(2)": "", "Value(3)": "7"}
    module = SimpleNamespace(Definition=SimpleNamespace(Name="Assign", DataModule=False, Operands=operands),
                             shape=SimpleNamespace(Left=10, Top=20), Data=lambda operand: data.get(operand))
    model = SimpleNamespace(FullName="m.doe", Name="m", Modules=SimpleNamespace(Count=1, Item=lambda _: module))
    monkeypatch.setattr(core, "open_model", lambda *_: model)
    monkeypatch.setattr(core, "connection_list", lambda _: [])
    monkeypatch.setattr(core, "run_setup", lambda _: {})
    ops = core.inspect(None, "m.doe")["modules"][0]["ops"]
    assert ops["Value(3)"] == "7" and "Value" not in ops  # the row was silently lost before


def test_failed_check_does_not_run_or_export(tmp_path, monkeypatch):
    path = tmp_path / "invalid.doe"
    path.write_bytes(b"unchanged")
    app = SimpleNamespace(Models=SimpleNamespace(Count=0))
    monkeypatch.setattr(core, "open_model", lambda *_: object())
    monkeypatch.setattr(core, "info", lambda _: {"version": 1620, "compatibility_mode": "arena16"})
    monkeypatch.setattr(core, "check", lambda *_: {"ok": False, "errors": ["Invalid operand"]})
    monkeypatch.setattr(core, "run", lambda *_, **__: pytest.fail("invalid model should not run"))
    res = validation.validate(app, path, png_path=tmp_path / "invalid.png")
    assert not res["ok"] and res["model_file_unchanged"]
    assert res["check"]["errors"] == ["Invalid operand"]
    assert not (tmp_path / "invalid.png").exists()


@pytest.mark.parametrize(("version", "uses_text_reports"), [
    (1400, True), (1470, True), (1500, False), (1620, False), (None, False),
])
def test_active_version_selects_report_settings(version, uses_text_reports, monkeypatch):
    app = SimpleNamespace(Version=version, Models=SimpleNamespace(Count=0))
    model = SimpleNamespace(Application=app, FullName="model.doe", QuietMode=False, BatchMode=False,
                            DisableReportDatabase=False, DisplayDefaultReport=0, State=0, Activate=lambda: None)
    checked = []
    model.Check = lambda: checked.append((model.QuietMode, model.DisableReportDatabase))
    monkeypatch.setattr(core, "connection_list", lambda _: [])
    monkeypatch.setattr(core, "end_other_runs", lambda _: [])
    monkeypatch.setattr(core.edition, "describe", lambda _: {})

    def run_batch(model, ended, show, outputs, per_replication, legacy=False, timeout=None):
        assert legacy == uses_text_reports
        assert model.DisableReportDatabase == uses_text_reports
        assert model.DisplayDefaultReport == int(uses_text_reports)
        assert model.BatchMode and model.QuietMode
        return "ran"

    monkeypatch.setattr(core, "_run_batch", run_batch)
    assert core.info(app)["compatibility_mode"] == (
        "arena14" if uses_text_reports else "arena16" if version == 1620 else "default"
    )
    assert core.check_model(model) == ([], [])
    assert checked == [(True, uses_text_reports)]
    assert core._run_once(app, model, None, True) == "ran"
    assert not model.DisableReportDatabase and model.DisplayDefaultReport == 0
    assert not model.BatchMode and not model.QuietMode


def test_com_major_version_accepts_text_from_com():
    assert core.com_major_version(1400) == 14 and core.com_major_version("1620") == 16
    assert core.com_major_version(None) is None and core.com_major_version(99) is None


@pytest.mark.parametrize(("major", "record_array"), [(14, False), (14, True), (16, False), (16, True)])
def test_record_rows_follow_the_loaded_template(major, record_array):
    operands = SimpleNamespace(Count=1, Item=lambda _: SimpleNamespace(Array=record_array))
    definition = SimpleNamespace(Name="Record", Operands=operands)
    spec = {"rows": [{"Type": "Count"}]}
    operand = "Type(1)" if record_array else "Type"
    assert core._expand_for_definition(spec, definition, major) == [(operand, "Count")]
    spec["rows"].append({"Type": "Time Interval"})
    if record_array:
        assert core._expand_for_definition(spec, definition, major)[-1] == ("Type(2)", "Time Interval")
    else:
        with pytest.raises(core.ArenaError, match="takes one row"):
            core._expand_for_definition(spec, definition, major)


def test_parse_out_summary():
    txt = open(os.path.join(DATA, "clinic.out"), errors="replace").read()
    res = results.parse_out(txt)
    assert res["Doctor.ScheduledUtilization"]["average"] == pytest.approx(0.79324)
    assert res["Patient.NumberOut"]["half_width"] == pytest.approx(6.6629)
    assert res["Patient.NumberOut"]["reps"] == 10


def test_parse_out_summary_section_names():
    # an all-letters header row used to become the section name of every row below it
    txt = ("Output Summary for 2 Replications\n\nTALLY VARIABLES\n\n"
           "IDENTIFIER                        AVERAGE    HALF WIDTH   MINIMUM   MAXIMUM\n"
           "Wait                              5.0000     1.0000       3.0000    7.0000      2\n")
    row = results.parse_out(txt)["Wait"]
    assert row["section"] == "Tally Variables" and row["average"] == pytest.approx(5.0)


def test_parse_out_single_replication():
    txt = open(os.path.join(DATA, "clinic.out"), errors="replace").read()
    first = txt.split("Beginning replication 2")[0]
    res = results.parse_out(first)
    assert res["Patient.WaitTime"]["average"] == pytest.approx(5.9959)
    assert res["Patient.WaitTime"]["half_width"] == "(Insuf)"
    assert res["Examination.Queue.NumberInQueue"]["section"] == "Discrete-Change Variables"
    assert res["Patient.NumberIn"]["value"] == pytest.approx(82)


def test_text_fallback_uses_each_printed_replication(tmp_path):
    txt = open(os.path.join(DATA, "clinic.out"), errors="replace").read()
    out = tmp_path / "clinic.out"
    out.write_text(txt)
    stats = results.read(str(out.with_suffix(".doe")), per_replication=True)["statistics"]
    wait = stats["Examination.Queue.WaitingTime"]
    assert wait["n"] == 10
    assert wait["replications"][:3] == [5.9228, 32.970, 5.2357]
    assert wait["replication_ids"] == list(range(1, 11))
    assert stats["Doctor.ScheduledUtilization"]["average"] == pytest.approx(0.79324)
    assert len(stats["Doctor.ScheduledUtilization"]["replications"]) == 10


def test_latest_report_is_read_offline_and_database_is_preferred_during_standard_run(tmp_path):
    model = tmp_path / "clinic # 100%.doe"
    db, out = model.with_suffix(".db"), model.with_suffix(".out")
    copyfile(os.path.join(DATA, "clinic.db"), db)
    copyfile(os.path.join(DATA, "clinic.out"), out)
    os.utime(db, (1000, 1000))
    os.utime(out, (1100, 1100))

    latest = core.last_results(model, per_replication=True)
    assert latest["source"] == str(out)
    assert latest["statistics"]["Examination.Queue.WaitingTime"]["replications"][:3] == [5.9228, 32.970, 5.2357]
    assert results.read(model, since=900)["source"] == str(db)
    assert core.last_results(db)["source"] == str(db)  # exact report selection even with a newer sibling
    os.utime(out, (950, 950))
    assert core.last_results(out)["source"] == str(out)


def test_text_only_run_requires_current_siman_statistics(tmp_path):
    model = tmp_path / "clinic.doe"
    db, out = model.with_suffix(".db"), model.with_suffix(".out")
    copyfile(os.path.join(DATA, "clinic.db"), db)
    copyfile(os.path.join(DATA, "clinic.out"), out)
    os.utime(db, (1100, 1100))
    os.utime(out, (1000, 1000))
    assert results.read(model, since=900, text_only=True)["source"] == str(out)
    assert results.read(model, since=1050, text_only=True)["source"] is None
    out.write_bytes(b"")
    assert results.read(model, since=900, text_only=True)["source"] is None


def test_text_report_accepts_lowercase_exponent_and_correlation_flag():
    txt = "TALLY VARIABLES\nIdentifier  Average  Half Width  Minimum  Maximum\nWait  -1.2e-03  (Corr)  -.02  +.1\n"
    row = results.parse_out(txt)["Wait"]
    assert row["average"] == pytest.approx(-0.0012)
    assert row["half_width"] == "(Corr)"
    assert row["maximum"] == pytest.approx(0.1)


def test_text_report_keeps_counters_with_infinite_limit():
    # as Arena 16.20 prints a Record "Count" statistic in a one-replication report
    txt = ("                                              COUNTERS\n\n"
           "Identifier                                    Count   Limit\n"
           "_____________________________________________________________\n\n"
           "Done                                             81  Infinite\n"
           "Capped                                           12  50\n")
    res = results.parse_out(txt)
    assert res["Done"]["count"] == 81 and res["Done"]["limit"] == "Infinite"
    assert res["Capped"]["limit"] == 50
    stats = results.text_statistics(txt.replace("COUNTERS", "Summary for Replication 1 of 1\nCOUNTERS"), True)
    assert stats["Done"]["replications"] == [81]


def test_text_replication_ids_do_not_fabricate_missing_or_summary_only_reps():
    detail = ("Summary for Replication 1 of 3\nOUTPUTS\nIdentifier  Value\nDone  5\n"
              "Summary for Replication 3 of 3\nOUTPUTS\nIdentifier  Value\nDone  7\n")
    report = results.text_statistics(detail, per_replication=True)["Done"]
    assert report["replication_ids"] == [1, 3] and report["replications"] == [5, 7]
    summary = "Output Summary for 3 Replications\nOUTPUTS\nDone  4  1  2  7  3\n"
    report = results.text_statistics(summary, per_replication=True)["Done"]
    assert "replications" not in report and "replication_ids" not in report


def test_read_db_matches_out_summary():
    res = results.read_db(os.path.join(DATA, "clinic.db"))
    out = results.parse_out(open(os.path.join(DATA, "clinic.out"), errors="replace").read())
    u = res["Doctor.ScheduledUtilization"]
    assert u["n"] == 10 and u["kind"] == "output"
    assert u["average"] == pytest.approx(out["Doctor.ScheduledUtilization"]["average"], abs=1e-4)
    assert u["half_width"] == pytest.approx(out["Doctor.ScheduledUtilization"]["half_width"], abs=1e-4)
    assert res["Examination.Queue.Waiting Time"]["kind"] == "tally"
    assert res["Doctor.Instantaneous Utilization"]["kind"] == "time-persistent"


def test_sqlite_preserves_native_replication_ids_and_duplicate_metric_labels(tmp_path):
    db = tmp_path / "two.db"
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE OutputStatsQuery ([Project RunDateTime], Replication, Name, Type, Source, RecordedValue)')
        con.executemany(
            "INSERT INTO OutputStatsQuery VALUES (?,?,?,?,?,?)",
            [('run', 1, 'Done', 'Output', 'User', 3), ('run', 3, 'Done', 'Output', 'User', 9),
             ('run', 2, 'Done', 'Output', 'Entity', 21)],
        )
    con.close()
    rows = results.read_db(db, per_replication=True)
    assert len(rows) == 2 and all(k.startswith("Done [") for k in rows)
    by_source = {v["source"]: v for v in rows.values()}
    assert by_source["User"]["replication_ids"] == [1, 3] and by_source["User"]["replications"] == [3, 9]
    assert by_source["Entity"]["replication_ids"] == [2]


def test_offline_results_csv_uses_real_reps_and_excel_encoding(tmp_path, capsys):
    source = os.path.join(DATA, "clinic.out")
    path = tmp_path / "逐次结果.csv"
    assert cli.main(["results", source, "--per-rep", "--csv", str(path), "--measures", "NUMBEROUT"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] and res["csv"]["mode"] == "per_replication"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == res["csv"]["rows"]
    selected = [row for row in rows if row["statistic"] == "Patient.NumberOut"]
    assert [int(r["replication"]) for r in selected] == list(range(1, 11))
    assert [float(r["value"]) for r in selected] == res["statistics"]["Patient.NumberOut"]["replications"]
    assert all(os.path.abspath(source) == r["report"] for r in rows)


def test_csv_summary_preserves_text_and_keeps_previous_file_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "summary.csv"
    report = {"name": "=字样", "results_source": "model.out",
              "statistics": {"Done": {"section": "Counters", "count": 12, "limit": "Infinite"}}}
    assert results.write_csv(path, [report])["rows"] == 1
    previous = path.read_bytes()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["scenario"] == "'=字样" and row["count"] == "12" and row["limit"] == "Infinite"

    def locked(*_):
        raise PermissionError("destination in use")
    monkeypatch.setattr(results.os, "replace", locked)
    with pytest.raises(PermissionError, match="destination in use"):
        results.write_csv(path, [report])
    assert path.read_bytes() == previous and list(tmp_path.iterdir()) == [path]


def test_cli_missing_report_and_empty_csv_filter_return_failure(tmp_path, capsys):
    target = tmp_path / "existing.csv"
    target.write_bytes(b"previous")
    assert cli.main(["results", str(tmp_path / "none.doe"), "--csv", str(target)]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
    assert cli.main(["results", os.path.join(DATA, "clinic.out"), "--csv", str(target),
                     "--measures", "NoStatisticWithThisName"]) == 1
    assert "no matching" in json.loads(capsys.readouterr().out)["error"]
    assert target.read_bytes() == b"previous"


@pytest.mark.parametrize("with_run_index", [False, True])
def test_sqlite_uses_one_run_across_statistic_views(tmp_path, with_run_index):
    db = tmp_path / "history.db"
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE OutputStatsQuery ([Project RunDateTime], Replication, Name, Type, Source, RecordedValue)')
        con.execute('CREATE TABLE CounterStatsQuery ([Project RunDateTime], Replication, Name, Type, Source, RecordedValue)')
        con.executemany(
            'INSERT INTO OutputStatsQuery VALUES (?,?,?,?,?,?)',
            [('first', 1, 'Transit', 'Output', 'User', 5), ('second', 1, 'Transit', 'Output', 'User', 9)],
        )
        con.execute("INSERT INTO CounterStatsQuery VALUES ('first',1,'Old counter','Count','User',99)")
        if with_run_index:
            con.execute('CREATE TABLE RunOutput (OutputCreationDate)')
            con.execute("INSERT INTO RunOutput VALUES ('first'), ('second')")
    con.close()
    res = results.read_db(db, per_replication=True)
    assert set(res) == {'Transit'}
    assert res['Transit']['replications'] == [9]
    if with_run_index:
        with sqlite3.connect(db) as con:
            con.execute("INSERT INTO RunOutput VALUES ('third')")
        con.close()
        assert results.read_db(db) == {}


def test_sqlite_wal_report_is_current_without_a_checkpoint(tmp_path):
    model = tmp_path / "live.doe"
    db = model.with_suffix(".db")
    writer = sqlite3.connect(db)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute('CREATE TABLE OutputStatsQuery ([Project RunDateTime], Replication, Name, Type, Source, RecordedValue)')
        writer.execute("INSERT INTO OutputStatsQuery VALUES ('current',1,'Throughput','Output','User',42)")
        writer.commit()
        wal = db.with_name(db.name + "-wal")
        os.utime(db, (1000, 1000))
        os.utime(wal, (1100, 1100))
        report = results.read(model, since=1050, per_replication=True)
        assert report["source"] == str(db)
        assert report["statistics"]["Throughput"]["replications"] == [42]
    finally:
        writer.close()


def test_drop_empty():
    res = results.read_db(os.path.join(DATA, "clinic.db"))
    kept = results.drop_empty(res)
    assert all(v["n"] > 0 for v in kept.values())
    # records from the text report carry a section, not a kind: they must not raise KeyError
    text = {"Wait": {"section": "Tally Variables", "average": 5.0, "n": 2}}
    assert results.drop_empty(text) == text


def test_animation_frames_validate_before_touching_arena():
    pytest.importorskip("win32gui")
    from arena_agent import snapshot

    with pytest.raises(core.ArenaError, match="greater than zero"):
        snapshot.capture_animation_frames(None, "m.doe", "frames", "ANIMATION", [0, 20])
    with pytest.raises(core.ArenaError, match="prefix"):
        snapshot.capture_animation_frames(None, "m.doe", "frames", "ANIMATION", [5], prefix="a/b")
    with pytest.raises(core.ArenaError, match="distinct"):
        snapshot.capture_animation_frames(None, "m.doe", "frames", "ANIMATION", [5, 5])


def test_summarize_and_t():
    s = results.summarize([1.0, 2.0, 3.0, 4.0])
    assert s["average"] == 2.5 and s["n"] == 4
    assert s["half_width"] == pytest.approx(3.182 * math.sqrt(5 / 3) / 2)
    assert results.summarize([5.0])["half_width"] is None
    assert results.t975(100) == pytest.approx(1.984, abs=0.003)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_edition_education_unknown_then_student_report(cfg):
    assert edition.describe(5)["status"] == "unknown"
    edition.remember_report_license("STUDENT")
    d = edition.describe(5)
    assert d["status"] == "limited" and d["limited"] is True


def test_edition_user_override(cfg):
    edition.set_limited(False)
    assert edition.describe(5)["limited"] is False
    edition.set_limited(None)
    assert edition.describe(4)["status"] == "full"
    assert edition.describe(2)["status"] == "view-only"


def test_edition_accepts_a_text_license_type(cfg):
    assert edition.describe("4")["status"] == "full"  # some builds report the enum as text
    assert edition.describe(None)["status"] == "unknown"


def test_limit_message():
    assert edition.is_limit_message("You have exceeded 150 entities allowed in the student version")
    assert not edition.is_limit_message("Undefined identifier: Patient")


def test_codex_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    p = tmp_path / "config.toml"
    p.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "a"\n', encoding="utf-8")
    install._codex_mcp([r"C:\Users\A B\arena-agent.exe", "mcp"])
    install._codex_mcp([r"C:\Users\A B\arena-agent.exe", "mcp"])  # idempotent
    txt = p.read_text(encoding="utf-8")
    assert txt.count("[mcp_servers.arena-agent]") == 1 and "[mcp_servers.other]" in txt
    assert "command = 'C:\\Users\\A B\\arena-agent.exe'" in txt
    assert install.status("codex")["mcp"]
    install._codex_mcp(None, remove=True)
    txt = p.read_text(encoding="utf-8")
    assert "arena-agent" not in txt and "[mcp_servers.other]" in txt


def test_desktop_config(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfgp = tmp_path / "Claude" / "claude_desktop_config.json"
    cfgp.parent.mkdir()
    cfgp.write_text(json.dumps({"mcpServers": {"x": {"command": "y"}}, "other": 1}), encoding="utf-8")
    install._desktop_mcp(["arena-agent.exe", "mcp"])
    cfg = json.loads(cfgp.read_text(encoding="utf-8"))
    assert cfg["mcpServers"]["arena-agent"]["args"] == ["mcp"] and cfg["mcpServers"]["x"] and cfg["other"] == 1


def test_installer_leaves_managed_skill_link_and_shared_contents_alone(tmp_path, monkeypatch):
    target = tmp_path / "arena-modeling"
    target.mkdir()
    retained = target / "SKILL.md"
    retained.write_bytes(b"externally managed")
    monkeypatch.setattr(install, "skill_target", lambda _: str(target))
    monkeypatch.setattr(install, "_linked_skill", lambda path: path == str(target))
    monkeypatch.setitem(install.MCP, "codex", lambda _command, remove=False: "mock config")
    installed = install.install(["codex"])["installed"][0]
    removed = install.uninstall(["codex"])["uninstalled"][0]
    assert installed["skill"] == str(target) and installed["skill_managed_externally"]
    assert removed["skill_managed_externally"] and "skill_removed" not in removed
    assert retained.read_bytes() == b"externally managed"


def test_cli_parsing():
    assert cli.parse_changes(["Doctor:Capacity=4", "Doctor:Name=D", "V:Initial Value=2"]) == {
        "Doctor": {"Capacity": "4", "Name": "D"}, "V": {"Initial Value": "2"}}
    args = ["m.doe", "--reps", "5", "--show", "A", "B", "--per-rep"]
    assert cli.positional(args) == ["m.doe"]
    assert cli.opt_list(args, "--show") == ["A", "B"]


def test_positional_finds_files_around_options():
    assert cli.positional(["--reps", "5", "m.doe"]) == ["m.doe"]  # an option may stand before the file names
    assert cli.positional(["m.doe", "out.png", "--view", "ANIMATION", "--time", "0", "20"]) == ["m.doe", "out.png"]
    assert cli.positional(["--region", "-10", "-10", "20", "20", "m.doe", "o.png"]) == ["m.doe", "o.png"]


def test_cli_asks_for_missing_arguments_instead_of_crashing(capsys):
    assert cli.main(["run", "--reps", "5"]) == 2  # was an IndexError traceback
    reported = json.loads(capsys.readouterr().out)
    assert "needs at least" in reported["error"] and reported["usage"]


def test_cli_rejects_a_bad_set_expression_as_json(capsys):
    assert cli.main(["run", "m.doe", "--set", "Doctor"]) == 2  # was a dict-update traceback
    reported = json.loads(capsys.readouterr().out)
    assert "Module:Operand=Value" in reported["error"]


def test_cli_signals_a_failed_scenario(tmp_path, monkeypatch, capsys):
    cases = tmp_path / "cases.json"
    cases.write_text('[{"name":"bad"}]', encoding="utf-8")
    monkeypatch.setattr(cli, "com", lambda *_, **__: {"ok": False, "scenarios": [{"name": "bad", "ok": False}]})
    assert cli.main(["scenarios", "m.doe", str(cases)]) == 1
    assert not json.loads(capsys.readouterr().out)["scenarios"][0]["ok"]


def test_cli_reports_failed_module_edit_even_when_tool_only_returns_errors(monkeypatch, capsys):
    monkeypatch.setattr(cli, "com", lambda *_, **__: {"errors": ["invalid operand"]})
    assert cli.main(["set", "m.doe", "Process", "Unknown=1"]) == 1
    assert json.loads(capsys.readouterr().out)["errors"] == ["invalid operand"]


def test_run_setup_is_read_and_restored_with_raw_com_values():
    model = SimpleNamespace(NumberOfReplications=5, ReplicationLength=480, ReplicationLengthTimeUnits=2,
                            WarmUpPeriod=0, WarmUpPeriodTimeUnits=2, BaseTimeUnits=2, HoursPerDay=24,
                            TerminatingCondition="")
    raw = core.run_setup_raw(model)
    assert raw["replications"] == 5 and raw["replication_length_units"] == 2  # raw enum, not a translated name
    report = {"errors": []}
    previous = core.apply_run_setup(model, {"replications": 10, "replication_length_units": "hours"}, report)
    assert not report["errors"] and (model.NumberOfReplications, model.ReplicationLengthTimeUnits) == (10, 1)
    core.restore_run_setup(model, previous, report)
    assert (model.NumberOfReplications, model.ReplicationLengthTimeUnits) == (5, 2)


def test_setup_rolls_back_after_a_property_setter_failure(monkeypatch):
    class Model:
        FullName = "m.doe"
        NumberOfReplications = 5
        length = 480

        @property
        def ReplicationLength(self):
            return self.length

        @ReplicationLength.setter
        def ReplicationLength(self, value):
            self.length = value
            if value == "bad":
                raise ValueError("invalid replication length")

        def Save(self):
            pytest.fail("invalid settings were saved")

    m = Model()
    monkeypatch.setattr(core, "open_model", lambda *_: m)
    result = core.setup(None, "m.doe", {"replications": 8, "replication_length": "bad"})
    assert not result["ok"] and not result["saved"]
    assert m.NumberOfReplications == 5 and m.ReplicationLength == 480


def test_build_rejects_a_hardlinked_save_as_alias(tmp_path, monkeypatch):
    base, alias = tmp_path / "base.doe", tmp_path / "alias.doe"
    base.write_bytes(b"model")
    os.link(base, alias)
    monkeypatch.setattr(core, "reserved_clashes", lambda *_: [])
    monkeypatch.setattr(core, "open_model", lambda *_: pytest.fail("attempted to open base"))
    result = core.build(None, {"base": str(base), "save_as": str(alias)})
    assert result["model_changed"] is False and "base model itself" in result["error"]
    assert alias.read_bytes() == base.read_bytes() == b"model"


def test_invalid_run_setup_does_not_start_or_leave_partial_changes(monkeypatch):
    model = SimpleNamespace(FullName="m.doe", NumberOfReplications=5)
    app = SimpleNamespace(Models=SimpleNamespace(Count=0))
    monkeypatch.setattr(core, "open_model", lambda *_: model)
    monkeypatch.setattr(core, "_run_once", lambda *_, **__: pytest.fail("invalid run started"))
    result = core.run(app, "m.doe", setup_fields={"replications": 4, "not_a_field": 5})
    assert not result["ok"] and "unknown field" in result["errors"][0]["message"]
    assert model.NumberOfReplications == 5


def test_batch_timeout_is_forwarded_before_a_model_path(monkeypatch, capsys):
    def com(fn, model, *args, timeout):
        assert fn is core.run and model == "m.doe" and timeout == 18.5
        return {"timed_out": True, "ok": False}
    monkeypatch.setattr(cli, "com", com)
    assert cli.main(["run", "--timeout", "18.5", "m.doe"]) == 1
    assert json.loads(capsys.readouterr().out)["timed_out"]


def test_batch_checks_every_model_before_ending_completed_run():
    finished = SimpleNamespace(State=12, Name="finished", End=lambda: pytest.fail("finished run was changed"))
    paused = SimpleNamespace(State=16, Name="paused", End=lambda: pytest.fail("paused run was ended"))
    app = SimpleNamespace(Models=SimpleNamespace(Count=2, Item=lambda i: [finished, paused][i-1]))
    with pytest.raises(core.ArenaError, match="paused"):
        core.end_other_runs(app)


def test_extension_band_stays_inside_the_arena_view():
    assert core.band_origin((-5000, -5000, 5000, 9000), 2600, 1800) == [-5000, 10800]
    assert core.band_origin((0, 0, 20000, 31000), 2600, 1800) == [22600, 0]  # below would be out of view
    assert core.band_origin((0, 0, 31000, 31000), 2600, 1800) == [0, 0]


def test_connect_reports_a_malformed_case_exit():
    report = {"errors": []}
    core.connect(None, {"a": object(), "b": object()}, {"from": "a", "to": "b", "exit": "case:"}, report)
    assert "case:N" in report["errors"][0]  # no ValueError traceback


def test_guide_topics_present():
    topics = paths.guide_topics()
    for t in ("skill", "build-spec", "modules", "gotchas", "results", "delivery", "discoveries"):
        assert t in topics and os.path.exists(topics[t]), t


def test_examples_are_valid_specs():
    for f in ("clinic.json", "bank.json"):
        spec = json.load(open(os.path.join(paths.examples_dir(), f), encoding="utf-8"))
        ids = {m["id"] for m in spec["modules"]}
        assert all(c["from"] in ids and c["to"] in ids for c in spec["connections"])


@pytest.mark.parametrize("second_zoom_works", [True, False])
def test_screenshot_retries_misplaced_arena14_viewport(monkeypatch, second_zoom_works):
    pytest.importorskip("win32gui")
    from arena_agent import snapshot

    class View:
        calls = 0

        def ZoomRegion(self, *region):
            self.calls += 1
            factor = 1 if self.calls == 2 and second_zoom_works else 2
            self.Left, self.Top, self.Right, self.Bottom = [int(value * factor) for value in region]

    monkeypatch.setattr(snapshot.time, "sleep", lambda _: None)
    view = View()
    region = (-15400, -18100, -7613, -14154)
    needed = (-15400, -18100, -8236, -14470)
    if second_zoom_works:
        assert snapshot._zoom_for_tile(view, region, needed) == region
    else:
        with pytest.raises(core.ArenaError, match="could not display screenshot tile"):
            snapshot._zoom_for_tile(view, region, needed)
    assert view.calls == 2


def test_screenshot_uses_only_the_arena_pane_and_crops_its_border(monkeypatch):
    pytest.importorskip("win32gui")
    from arena_agent import snapshot
    from PIL import Image

    captured = Image.new("RGB", (34, 24), "black")
    captured.paste("white", (2, 2, 32, 22))
    handles = []

    def grab(*, window):
        handles.append(window)
        return captured

    monkeypatch.setattr(snapshot.ImageGrab, "grab", grab)
    monkeypatch.setattr(snapshot.win32gui, "GetWindowRect", lambda pane: (100, 200, 134, 224))
    rendered = snapshot._capture_pane(17, (102, 202, 132, 222))
    assert handles == [17]
    assert rendered.size == (30, 20) and rendered.getextrema() == ((255, 255),) * 3
    black = captured.point(lambda _value: 0)
    responses = iter((black, captured))
    redraws = []
    monkeypatch.setattr(snapshot.ImageGrab, "grab", lambda **kwargs: next(responses))
    monkeypatch.setattr(snapshot.win32gui, "RedrawWindow", lambda handle, *args: redraws.append(handle))
    assert snapshot._capture_pane(17, (102, 202, 132, 222)).getbbox() is not None
    assert redraws == [17]
    monkeypatch.setattr(snapshot.ImageGrab, "grab", lambda **kwargs: black)
    with pytest.raises(core.ArenaError, match="black capture"):
        snapshot._capture_pane(17, (102, 202, 132, 222))
