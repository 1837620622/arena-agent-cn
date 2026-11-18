"""通过原生 Arena 依次检查、求解和导图，返回结构化验收结果。"""

from hashlib import sha256
import math
from pathlib import Path

from . import core


def validate(a, model, replications=1, png_path=None, view_name=None, pixels_per_unit=None):
    if type(replications) is not int or replications < 1:
        raise core.ArenaError("replications must be a positive integer")
    if png_path is None and (view_name is not None or pixels_per_unit is not None):
        raise core.ArenaError("provide png_path to use export options")
    if png_path is not None and Path(png_path).suffix.lower() != ".png":
        raise core.ArenaError("png_path must end in .png")
    if pixels_per_unit is not None and (not math.isfinite(pixels_per_unit) or pixels_per_unit <= 0):
        raise core.ArenaError("pixels_per_unit must be positive and finite")
    path = Path(model).resolve()
    if not path.is_file():
        raise core.ArenaError(f"no such model file: {path}")
    if any(core.safe(lambda: a.Models.Item(i).State, 0) for i in range(1, a.Models.Count + 1)):
        raise core.ArenaError("end the active Arena run before validating a model")

    m = core.open_model(a, str(path))
    if view_name is not None and view_name not in core.named_view_regions(m):
        raise core.ArenaError(f"unknown named view {view_name!r}; list the model views first")
    initial_hash = sha256(path.read_bytes()).digest()
    status = core.info(a)
    result = {"ok": False, "model": str(path), "arena_version": status["version"],
              "compatibility_mode": status["compatibility_mode"]}
    checked = result["check"] = core.check(a, str(path))
    if checked["ok"]:
        # 新版批跑也可能改变视图所属模型的运行选项，验收完恢复原值。
        with core.temporary_settings(m, QuietMode=True, BatchMode=True):
            replay = core.run(a, str(path), reps=replications)
        run_ok = (replay["ok"] and replay["replications"] == replications
                  and bool(replay.get("results_source")) and bool(replay.get("statistics")))
        result["run"] = {
            "ok": run_ok,
            "replications": replay["replications"],
            "end_time": replay["end_time"],
            "results_source": replay.get("results_source"),
            "statistics_count": len(replay.get("statistics", {})),
            "errors": replay["errors"],
            "results_note": replay.get("results_note"),
        }
        result["ok"] = run_ok and not checked["layout_warnings"]
        if run_ok and png_path is not None:
            from .snapshot import snapshot
            try:
                result["screenshot"] = snapshot(a, str(path), png_path, ppu=pixels_per_unit, view_name=view_name)
            except (core.ArenaError, OSError) as exc:
                result["screenshot"] = {"error": str(exc)}
                result["ok"] = False
    result["model_file_unchanged"] = sha256(path.read_bytes()).digest() == initial_hash
    result["ok"] = result["ok"] and result["model_file_unchanged"]
    return result
