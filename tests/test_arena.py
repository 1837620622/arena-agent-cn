"""End-to-end tests against a local Arena: pytest -m arena"""
import json
import os
from pathlib import Path
from shutil import copyfile
import time

import pytest
from PIL import Image

from arena_agent import core, paths, validation, worker
from arena_agent.snapshot import _view_bounds, capture_animation_frame, snapshot

pytestmark = pytest.mark.arena


def close_example(model):
    # pytest 的 DOE 是一次性副本，允许显式保存后关闭，防止测试之间留下文件锁。
    worker.call(core.close, [model], save_changes=True)


def build_example(name, tmp_path):
    spec = json.load(open(os.path.join(paths.examples_dir(), name), encoding="utf-8"))
    spec["save_as"] = str(tmp_path / spec["save_as"])
    return spec["save_as"], worker.call(core.build, spec)


def test_clinic_builds_runs_and_matches_theory(tmp_path):
    model, res = build_example("clinic.json", tmp_path)
    assert res["ok"], res
    run = worker.call(core.run, model)
    assert run["ok"] and run["end_time"] == 480
    stats = run["statistics"]
    util = stats.get("Doctor.Instantaneous Utilization") or stats["Doctor.Utilization"]
    # arrival rate 1/5 per minute, mean exam 12 minutes, 3 doctors: utilization 0.8
    assert abs(util["average"] - 0.8) < 2 * util["half_width"] + 0.05
    assert {"Examination.Queue.Waiting Time", "Examination.Queue.WaitingTime"} & stats.keys()
    close_example(model)


def test_validate_runs_native_model_without_saving_it(tmp_path):
    model, built = build_example("clinic.json", tmp_path)
    assert built["ok"], built
    try:
        def create_view(app):
            m = core.open_model(app, model)
            m.NamedViews.Create("Clinic detail", "V", -500, -500, 7000, 2500)
            m.Save()

        worker.call(create_view)
        saved = (tmp_path / "clinic.doe").read_bytes()
        views = worker.call(core.list_named_views, model)["views"]
        assert views["Clinic detail"] == [-500, -500, 7000, 2500]
        result = worker.call(validation.validate, model, 2)
        assert result["ok"] and result["model_file_unchanged"], result
        assert result["run"]["replications"] == 2 and result["run"]["end_time"] == 480
        assert result["run"]["statistics_count"] > 0
        assert (tmp_path / "clinic.doe").read_bytes() == saved
    finally:
        close_example(model)


def test_export_restores_the_editor_view_and_captures_a_paused_frame(tmp_path):
    model, built = build_example("bank.json", tmp_path)
    assert built["ok"], built

    def capture(app):
        m = core.open_model(app, model)
        m.Activate()
        v = m.ActiveView
        detail = m.NamedViews.Create("Detail", "V", -500, -500, 7000, 2500)
        v.ZoomView(detail)
        old_bounds = _view_bounds(v)
        old_split = v.SplitScreenPercent
        old_grid = v.Grid
        still = snapshot(app, model, str(tmp_path / "detail.png"), view_name="Detail")
        assert still["tiles"] == 1
        assert _view_bounds(v) == pytest.approx(old_bounds, abs=2)
        assert v.SplitScreenPercent == old_split and v.Grid == old_grid

        if core.com_major_version(app.Version) != 14:
            return
        before_animation = Path(model).read_bytes()
        auto = capture_animation_frame(app, model, str(tmp_path / "timed.png"), "Detail", 4)
        assert auto["simulation_time"] == pytest.approx(4) and auto["tiles"] == 1
        assert m.State == 0 and _view_bounds(v) == pytest.approx(old_bounds, abs=2)
        assert Path(model).read_bytes() == before_animation
        with core.temporary_settings(m, BatchMode=False, DisableReportDatabase=True, QuietMode=True, RunSpeed=0.1):
            try:
                m.Go(2)
                deadline = time.monotonic() + 10
                while m.State != 13 and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert m.State == 13
                with pytest.raises(core.ArenaError, match="pause"):
                    snapshot(app, model, str(tmp_path / "moving.png"))
                m.BreakOnTime(4)
                deadline = time.monotonic() + 10
                while m.State == 13 and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert m.State == 16 and m.SIMAN.RunCurrentTime == pytest.approx(4)
                with pytest.raises(core.ArenaError, match="is active"):
                    core.run(app, model)
                assert m.State == 16 and m.SIMAN.RunCurrentTime == pytest.approx(4)
                old_bounds = _view_bounds(v)
                live = snapshot(app, model, str(tmp_path / "paused.png"))
                assert live["tiles"] == 1
                with Image.open(live["out"]) as img:
                    assert img.size == tuple(live["size"])
                    assert any(lo < hi for lo, hi in img.convert("RGB").getextrema())
                with pytest.raises(core.ArenaError, match="outside"):
                    snapshot(app, model, str(tmp_path / "offscreen.png"), region=(100000, 0, 100500, 500))
                assert _view_bounds(v) == old_bounds
                assert m.State == 16 and m.SIMAN.RunCurrentTime == pytest.approx(4)
            finally:
                if m.State:
                    m.End()

    try:
        worker.call(capture)
    finally:
        close_example(model)


def test_batch_restores_native_rename_and_can_run_again_after_timeout(tmp_path):
    model, built = build_example("bank.json", tmp_path)
    assert built["ok"], built
    original = Path(model).read_bytes()
    try:
        def exercise(app):
            m = core.open_model(app, model)
            before = core.run_setup_raw(m)
            rename = {"Customers Leave": {"Name": "Temporary Departure"}}
            bad = core.run(app, model, changes={**rename, "Missing": {"Capacity": "3"}})
            assert not bad["ok"] and "Missing" in bad["errors"][0]["message"]
            assert "Customers Leave" in core.modules_by_name(m)
            if core.com_major_version(app.Version) == 14:
                timeout = core.run(app, model, setup_fields={"replication_length": "5000000"},
                                   changes=rename, timeout=0.02)
                assert timeout["timed_out"] and not timeout["ok"], timeout
                assert not timeout["statistics"] and timeout["results_source"] is None
                assert m.State == 0 and core.run_setup_raw(m) == before
                assert "Customers Leave" in core.modules_by_name(m)
            replay = core.run(app, model, reps=2, changes=rename)
            assert replay["ok"] and replay["replications"] == 2, replay
            assert "Customers Leave" in core.modules_by_name(m)
            assert m.State == 0 and core.run_setup_raw(m) == before
            assert Path(model).read_bytes() == original
            # Arena 将回退后的文档标记为已修改，默认关闭须保留它并保持磁盘内容。
            assert not m.Saved
            close = core.close(app, [model])
            assert not close["ok"] and not close["closed"] and "unsaved changes" in close["failed"][0]["error"]
            assert Path(model).read_bytes() == original

        worker.call(exercise)
    finally:
        close_example(model)


def test_bank_scenarios_more_tellers_wait_less(tmp_path):
    model, res = build_example("bank.json", tmp_path)
    assert res["ok"], res
    saved_model = (tmp_path / "bank.doe").read_bytes()
    original_report_settings = worker.call(report_settings, model)
    if original_report_settings is not None:
        # 先放入与本轮无关的数据库，确保 Arena 14 批跑只读自己写出的文本报告。
        previous_db = tmp_path / "bank.db"
        copyfile(Path(__file__).parent / "data" / "clinic.db", previous_db)
        future = time.time() + 3600
        os.utime(previous_db, (future, future))
    out = worker.call(core.scenarios, model, [{"name": "2", "changes": {"Teller": {"Capacity": "2"}}},
                                              {"name": "3", "changes": {"Teller": {"Capacity": "3"}}}], 10)
    assert all(s["ok"] for s in out["scenarios"]), out
    if original_report_settings is not None:
        assert all(s["results_source"].lower().endswith(".out") for s in out["scenarios"])
    w = [(s["statistics"].get("Teller Service.Queue.Waiting Time") or
          s["statistics"]["Teller Service.Queue.WaitingTime"])["average"] for s in out["scenarios"]]
    assert w[1] < w[0]
    # the saved model is unchanged by scenario runs
    ins = worker.call(core.inspect, model, True)
    teller = next(m for m in ins["modules"] if m["name"] == "Teller")
    assert teller["ops"]["Capacity"] == "2"
    assert worker.call(report_settings, model) == original_report_settings
    assert (tmp_path / "bank.doe").read_bytes() == saved_model
    close_example(model)


def report_settings(app, model):
    m = core.open_model(app, model)
    if core.com_major_version(app.Version) == 14:
        return m.DisableReportDatabase, m.DisplayDefaultReport
    return None


def test_overlap_is_reported_at_native_module_bounds(tmp_path):
    spec = {"save_as": str(tmp_path / "layout.doe"), "check": False, "modules": [
        {"id": "a", "type": "Create", "x": 2000, "y": 2000, "data": {"Name": "Arrivals"}},
        {"id": "b", "type": "Dispose", "x": 2000, "y": 2000, "data": {"Name": "Departures"}},
        {"id": "c", "type": "Dispose", "data": {"Name": "Automatic Position"}},
    ]}
    res = worker.call(core.build, spec)
    assert "layout: 'Arrivals' overlaps 'Departures'" in res["warnings"]
    close_example(spec["save_as"])


def test_check_maps_error_to_module(tmp_path):
    spec = {"save_as": str(tmp_path / "bad.doe"), "modules": [
        {"id": "c", "type": "Create", "data": {"Name": "Arr"}},
        {"id": "p", "type": "Process", "x": 2000, "data": {"Name": "Work", "Action": "Seize Delay Release",
                                                          "DelayType": "Expression", "Expression": "EXPO(NoSuchVar)"},
         "resources": [{"Resource Name": "R"}]},
        {"id": "d", "type": "Dispose", "x": 4000, "data": {"Name": "Out"}}],
        "connections": [{"from": "c", "to": "p"}, {"from": "p", "to": "d"}]}
    res = worker.call(core.build, spec)
    assert not res["ok"]
    assert any("NoSuchVar" in (e.get("message", "") + " ".join(e.get("code", []))) for e in res["check_errors"])
    close_example(spec["save_as"])


def test_reserved_word_refused(tmp_path):
    spec = {"save_as": str(tmp_path / "rw.doe"), "modules": [
        {"id": "a", "type": "Assign", "data": {"Name": "A"}, "rows": [{"Type": "Variable", "VName": "Wait",
                                                                       "Value": "1"}]}]}
    res = worker.call(core.build, spec)
    assert res.get("model_changed") is False and res["names"] == ["Wait"]
