"""Tests that need no Arena: spec expansion, name declaration, report parsing, statistics, edition, install config."""
import json
import math
import os
from shutil import copyfile
from types import SimpleNamespace

import pytest

from arena_agent import cli, core, edition, install, paths, results, validation

DATA = os.path.join(os.path.dirname(__file__), "data")


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

    def run_batch(model, ended, show, outputs, per_replication, legacy=False):
        assert legacy == uses_text_reports
        assert model.DisableReportDatabase == uses_text_reports
        assert model.DisplayDefaultReport == int(uses_text_reports)
        return "ran"

    monkeypatch.setattr(core, "_run_batch", run_batch)
    assert core.info(app)["compatibility_mode"] == (
        "arena14" if uses_text_reports else "arena16" if version == 1620 else "default"
    )
    assert core.check_model(model) == ([], [])
    assert checked == [(True, uses_text_reports)]
    assert core._run_once(app, model, None, True) == "ran"
    assert not model.DisableReportDatabase and model.DisplayDefaultReport == 0


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


def test_read_db_matches_out_summary():
    res = results.read_db(os.path.join(DATA, "clinic.db"))
    out = results.parse_out(open(os.path.join(DATA, "clinic.out"), errors="replace").read())
    u = res["Doctor.ScheduledUtilization"]
    assert u["n"] == 10 and u["kind"] == "output"
    assert u["average"] == pytest.approx(out["Doctor.ScheduledUtilization"]["average"], abs=1e-4)
    assert u["half_width"] == pytest.approx(out["Doctor.ScheduledUtilization"]["half_width"], abs=1e-4)
    assert res["Examination.Queue.Waiting Time"]["kind"] == "tally"
    assert res["Doctor.Instantaneous Utilization"]["kind"] == "time-persistent"


def test_drop_empty():
    res = results.read_db(os.path.join(DATA, "clinic.db"))
    kept = results.drop_empty(res)
    assert all(v["n"] > 0 for v in kept.values())


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


def test_cli_parsing():
    assert cli.parse_changes(["Doctor:Capacity=4", "Doctor:Name=D", "V:Initial Value=2"]) == {
        "Doctor": {"Capacity": "4", "Name": "D"}, "V": {"Initial Value": "2"}}
    args = ["m.doe", "--reps", "5", "--show", "A", "B", "--per-rep"]
    assert cli.positional(args) == ["m.doe"]
    assert cli.opt_list(args, "--show") == ["A", "B"]


def test_guide_topics_present():
    topics = paths.guide_topics()
    for t in ("skill", "build-spec", "modules", "gotchas", "results", "discoveries"):
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
