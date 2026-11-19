"""Screenshot of an Arena model's flowchart, so an agent (or its user) can see what was built.

The flowchart pane is zoomed tile by tile with IView.ZoomRegion; after each zoom the visible world extents are read
back (IView.Left/Top/Right/Bottom), so every tile is pasted at its exact place whatever zoom Arena actually chose.
Each tile is captured directly from the Arena flowchart window.
"""
from contextlib import contextmanager
import ctypes
import math
import os
import time

import win32con
import win32gui
from PIL import Image, ImageGrab

from .core import (ARENA_14, ArenaError, com_major_version, end_completed_runs, named_view_regions, open_model, safe,
                   temporary_settings)

VIEW_SETTINGS = {"SplitScreen": True, "SplitScreenPercent": 99, "Grid": False, "ShowRulers": False,
                 "ShowGuides": False, "ShowPageBreaks": False}
MOVING_STATES = {13, 14, 15}  # smStateRunning, smStateStepping, smStateFastForward
RUN_FRAME_STATES = {8, 9, 10, 11, 12, 16, 17, 18, 19, 20, 21}
ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x00000002
MAX_PIXELS = 120_000_000  # one PNG: keeps a wrong pixels_per_unit from exhausting memory
MAX_SIDE = 30_000
SHAPE_REACH = 5000  # a text shape further than this from the flow is not part of the exported region


def _dpi_aware():
    safe(lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2))  # real pixels on scaled displays


def client_rect(h):
    l, t, r, b = win32gui.GetClientRect(h)
    x0, y0 = win32gui.ClientToScreen(h, (l, t))
    x1, y1 = win32gui.ClientToScreen(h, (r, b))
    return x0, y0, x1, y1


def area(r):
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def _class_name(h):
    return safe(lambda: win32gui.GetClassName(h), "")


def _title_matches(title, model_name):
    """Arena appends " - Run Mode" while the animation runs and " *" to a model with unsaved changes (both at once
    while a changed model is playing)."""
    text = (title or "").strip()
    stripped = True
    while stripped:
        stripped = False
        for suffix in (" - Run Mode", " *"):
            if text.endswith(suffix):
                text = text[: -len(suffix)].rstrip()
                stripped = True
    return text == model_name


def model_window(model_name):
    """定位 m.Activate() 选中的 MDI 文档，兼容动画运行时的窗口标题。"""
    tops = []
    win32gui.EnumWindows(lambda h, _: tops.append(h) if _class_name(h) == "SMX:Arena"
                         and win32gui.IsWindowVisible(h) else None, None)
    for top in tops:
        mdi = win32gui.FindWindowEx(top, 0, "MDIClient", None)
        if not mdi:
            continue
        child = win32gui.SendMessage(mdi, win32con.WM_MDIGETACTIVE, 0, 0)
        if not child or _class_name(child) != "SMX:Model":
            continue
        if not _title_matches(win32gui.GetWindowText(child), model_name):
            continue
        panes = []
        win32gui.EnumChildWindows(child, lambda h, _: panes.append(h), None)
        panes = [p for p in panes if _class_name(p).startswith("Afx:") and win32gui.IsWindowVisible(p)]
        # 绘图区使用 Afx:…:8:…，与表格视图的 AfxFrameOrView140 不同；个别版本编号不同，按类名排除表格视图。
        flowchart = [p for p in panes if ":8:" in _class_name(p)] or [
            p for p in panes if "AfxFrameOrView" not in _class_name(p)]
        if flowchart:
            return top, child, max(flowchart, key=lambda p: area(client_rect(p)))
    raise ArenaError(f"no visible Arena window for model {model_name!r}")


def _shape_box(shape):
    """(x0, y0, x1, y1) of a module or text shape, or None when the object reports no bounds."""
    left, right = safe(lambda: shape.Left), safe(lambda: shape.Right)
    top, bottom = safe(lambda: shape.Top), safe(lambda: shape.Bottom)
    if None in (left, right, top, bottom):
        return None
    return min(left, right), min(top, bottom), max(left, right), max(top, bottom)


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def content_bbox(m):
    """Bounds of what the flowchart shows: the modules together with the text shapes near them, so the default export
    does not cut off a section title. A shape far from the flow (a stray frame) is ignored, and a model that only
    declares names falls back to its data modules."""
    flowchart, data = [], []
    for i in range(1, m.Modules.Count + 1):
        md = m.Modules.Item(i)
        box = _shape_box(safe(lambda: md.shape))
        if box is None:
            continue
        (data if safe(lambda: md.Definition.DataModule, True) else flowchart).append(box)
    if not flowchart:
        flowchart = data
    if not flowchart:
        raise ArenaError("the model has no flowchart modules to show")
    x0, y0, x1, y1 = _union(flowchart)
    titles = [_shape_box(safe(lambda: m.Shapes.Item(i)))
              for i in range(1, (safe(lambda: m.Shapes.Count, 0) or 0) + 1)]
    for box in titles:
        if box and box[0] >= x0 - SHAPE_REACH and box[2] <= x1 + SHAPE_REACH \
                and box[1] >= y0 - SHAPE_REACH and box[3] <= y1 + SHAPE_REACH:
            flowchart.append(box)
    return _union(flowchart)


def _ordered(bounds):
    """View bounds as (x_lo, y_lo, x_hi, y_hi). Arena reports a mirrored view with left > right or top > bottom, and
    every comparison and paste position below has to work in model coordinates, not in raw report order."""
    left, top, right, bottom = bounds
    x_lo, x_hi = sorted((left, right))
    y_lo, y_hi = sorted((top, bottom))
    return x_lo, y_lo, x_hi, y_hi


def _zoom_for_tile(view, requested, needed):
    """Read back the viewport before capturing. Arena 14 can double its first ZoomRegion coordinates."""
    x0, y0, x1, y1 = needed
    for _ in range(2):
        view.ZoomRegion(*requested)
        time.sleep(0.5)
        bounds = _view_bounds(view)
        left, top, right, bottom = bounds
        x_lo, y_lo, x_hi, y_hi = _ordered(bounds)
        # View bounds are rounded to integer model units.
        if x_lo <= x0 + 2 and x_hi >= x1 - 2 and y_lo <= y0 + 2 and y_hi >= y1 - 2:
            return left, top, right, bottom
    raise ArenaError(f"Arena could not display screenshot tile {needed}; reduce the zoom or select a smaller region")


def _upright(img, bounds):
    """A mirrored view draws the model flipped: a captured tile has to be turned back before it is pasted at its
    place on the canvas, or the joined image shows mirrored modules."""
    left, top, right, bottom = bounds
    if left > right:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if top > bottom:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return img


def _capture_pane(pane, client):
    x0, y0, x1, y1 = client
    for attempt in range(2):
        img = ImageGrab.grab(window=pane)
        if img.size != (x1 - x0, y1 - y0):
            # 某些窗口会连同滚动条和边框一起返回，按客户区实际边界裁切。
            left, top, right, bottom = win32gui.GetWindowRect(pane)
            fits = left <= x0 < x1 <= right and top <= y0 < y1 <= bottom
            if img.size != (right - left, bottom - top) or not fits:
                raise ArenaError("the Arena flowchart window changed size during capture; retry the export")
            img = img.crop((x0 - left, y0 - top, x1 - left, y1 - top))
        if img.getbbox() is not None:
            return img
        if attempt == 0:
            safe(lambda: win32gui.RedrawWindow(pane, None, 0, win32con.RDW_INVALIDATE | win32con.RDW_UPDATENOW))
            time.sleep(0.1)
    raise ArenaError("Arena returned a black capture; restore the visible interactive desktop and retry")


def _view_bounds(view):
    return view.Left, view.Top, view.Right, view.Bottom


def _native_ppu(view, pw, ph):
    """屏幕像素/模型单位：Arena 当前缩放下的原生比例。按它出图不缩放，文字和连线与屏幕一样清晰。"""
    left, top, right, bottom = _view_bounds(view)
    width, height = abs(right - left), abs(bottom - top)
    if width <= 0 or height <= 0 or pw <= 0 or ph <= 0:
        return None
    return min(pw / width, ph / height)


def _resample(img, size):
    """缩小用 LANCZOS 保住笔画，放大用 BICUBIC 避免锯齿；尺寸相同就不动图。"""
    if img.size == size:
        return img
    shrinking = size[0] <= img.width and size[1] <= img.height
    return img.resize(size, Image.Resampling.LANCZOS if shrinking else Image.Resampling.BICUBIC)


@contextmanager
def _export_view(view, top, child):
    settings = {prop: safe(lambda: getattr(view, prop)) for prop in VIEW_SETTINGS}
    bounds = _view_bounds(view)
    placements = {hwnd: win32gui.GetWindowPlacement(hwnd) for hwnd in (top, child)}
    set_execution_state = ctypes.windll.kernel32.SetThreadExecutionState
    previous_state = safe(lambda: set_execution_state(ES_CONTINUOUS | ES_DISPLAY_REQUIRED))
    try:
        # 99% 将表格压在底部，保留绘图区。
        for prop, value in VIEW_SETTINGS.items():
            safe(lambda: setattr(view, prop, value))
        for hwnd in (top, child):
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        safe(lambda: win32gui.SetForegroundWindow(top))
        time.sleep(0.8)
        yield
    finally:
        if previous_state:
            safe(lambda: set_execution_state(previous_state))
        for hwnd, placement in placements.items():
            if safe(lambda: win32gui.GetWindowPlacement(hwnd)) != placement:
                safe(lambda: win32gui.SetWindowPlacement(hwnd, placement))
        for prop, value in settings.items():
            if value is not None and safe(lambda: getattr(view, prop)) != value:
                safe(lambda: setattr(view, prop, value))
        # 先恢复窗口和表格的尺寸，再还原原动画或逻辑视野。Arena 的边界读数为整数。
        restore = _ordered(bounds)
        for _ in range(2):
            current = safe(lambda: _view_bounds(view))
            if current is None or all(abs(old - now) <= 2 for old, now in zip(bounds, current)):
                break
            safe(lambda: view.ZoomRegion(*restore))


def snapshot(a, model, out_path, region=None, ppu=None, max_tiles=64, *, view_name=None):
    """Write a PNG of the flowchart by named view or rectangle (default: all flowchart modules).
    ppu: screen pixels per model unit. Default: never below Arena's current zoom — a region that fits one pane at
    that scale is one image, a larger one is tiled up to max_tiles, and only a region too big even for that many
    tiles is exported at a lower resolution."""
    if view_name is not None and region is not None:
        raise ArenaError("choose view_name or region")
    out_path = os.path.abspath(out_path)
    if os.path.splitext(out_path)[1].lower() != ".png":
        raise ArenaError("screenshot output must end in .png")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)  # a new export folder should not fail the export
    _dpi_aware()
    m = open_model(a, model)
    state = safe(lambda: m.State)
    if state in MOVING_STATES:
        raise ArenaError("pause the Arena animation before exporting so all tiles show the same simulation time")
    if view_name is not None:
        views = named_view_regions(m)
        if view_name not in views:
            raise ArenaError(f"unknown named view {view_name!r}; available views: {', '.join(views) or '(none)'}")
        region = views[view_name]
    try:
        ppu = None if ppu is None else float(ppu)
    except (TypeError, ValueError):
        raise ArenaError("screenshot pixels per unit must be a number")
    if ppu is not None and (not math.isfinite(ppu) or ppu <= 0):
        raise ArenaError("screenshot pixels per unit must be positive and finite")
    try:
        max_tiles = int(max_tiles)
    except (TypeError, ValueError):
        raise ArenaError("screenshot max_tiles must be a whole number")
    if max_tiles < 1:
        raise ArenaError("screenshot max_tiles must be at least 1")
    if region is not None:
        try:
            region = tuple(float(value) for value in region)
        except (TypeError, ValueError):
            raise ArenaError("screenshot region must be four numbers: X0 Y0 X1 Y1")
        if len(region) != 4:
            raise ArenaError("screenshot region must contain four coordinates: X0 Y0 X1 Y1")
        if not all(math.isfinite(value) for value in region):
            raise ArenaError("screenshot region must contain four finite coordinates")
        if not (region[0] < region[2] and region[1] < region[3]):
            raise ArenaError("screenshot region must have increasing x and y bounds")
    elif state not in RUN_FRAME_STATES:
        X0, Y0, X1, Y1 = content_bbox(m)
        mg = 400
        region = (X0 - mg, Y0 - mg, X1 + mg, Y1 + mg)
    a.Visible = True
    m.Activate()
    v = m.ActiveView
    top, child, pane = model_window(m.Name)
    if state in RUN_FRAME_STATES:
        canvas, region, used_ppu = _capture_run_frame(v, pane, region, ppu)
        tiles = 1
    else:
        with _export_view(v, top, child):
            canvas, tiles, used_ppu = _capture_tiles(v, pane, region, ppu, max_tiles)
    canvas.save(out_path)
    return {"out": out_path, "size": list(canvas.size), "tiles": tiles, "region": list(region),
            "pixels_per_unit": round(used_ppu, 4) if used_ppu else None,
            **({"view_name": view_name} if view_name is not None else {}),
            "capture": "arena_flowchart_window"}


def _capture_run_frame(view, pane, region, ppu):
    # Arena 14 在暂停时用 ZoomRegion 会放大或缩小实际绘制结果，即使 COM 边界读回正常。
    # 直接截取当前帧，视野外的区域需要先在 Arena 内切到对应的命名视图。
    bounds = _view_bounds(view)
    left, top, right, bottom = bounds
    x_lo, y_lo, x_hi, y_hi = _ordered(bounds)
    client = client_rect(pane)
    width, height = client[2] - client[0], client[3] - client[1]
    if x_hi <= x_lo or y_hi <= y_lo or width <= 0 or height <= 0:
        raise ArenaError("the Arena animation pane is not visible")
    view_region = (x_lo, y_lo, x_hi, y_hi)
    if region is None:
        region = view_region
    x0, y0, x1, y1 = region
    if x0 < x_lo - 2 or x1 > x_hi + 2 or y0 < y_lo - 2 or y1 > y_hi + 2:
        raise ArenaError("the screenshot region is outside the current paused view; show the whole region in Arena first")
    scale_x, scale_y = width / (x_hi - x_lo), height / (y_hi - y_lo)
    # 视野左右或上下颠倒时，屏幕坐标从大值往小值走，裁剪框要按同一方向换算。
    col0, col1 = (x0 - x_lo, x1 - x_lo) if left <= right else (x_hi - x1, x_hi - x0)
    row0, row1 = (y0 - y_lo, y1 - y_lo) if top <= bottom else (y_hi - y1, y_hi - y0)
    box = (max(0, round(col0 * scale_x)), max(0, round(row0 * scale_y)),
           min(width, round(col1 * scale_x)), min(height, round(row1 * scale_y)))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ArenaError("the screenshot region is smaller than one screen pixel")
    image = _upright(_capture_pane(pane, client).crop(box), bounds)
    if ppu is not None:
        size = (max(1, round((x1 - x0) * ppu)), max(1, round((y1 - y0) * ppu)))
        if size[0] > image.width or size[1] > image.height:
            raise ArenaError("the paused animation can only be exported at its on-screen resolution or smaller")
        image = _resample(image, size)
    # 暂停帧只能按屏幕分辨率裁剪，把实际得到的比例报回去，才知道这张图够不够清晰。
    return image, region, image.width / max(x1 - x0, 1e-9)


def _capture_tiles(v, pane, region, ppu, max_tiles):
    X0, Y0, X1, Y1 = region
    pr = client_rect(pane)
    pw, ph = pr[2] - pr[0], pr[3] - pr[1]
    if pw <= 0 or ph <= 0:
        raise ArenaError("the Arena flowchart pane is not visible")
    if ppu is not None and not (math.isfinite(ppu) and ppu > 0):
        raise ArenaError("screenshot pixels per unit must be positive and finite")
    span_x, span_y = max(X1 - X0, 1.0), max(Y1 - Y0, 1.0)
    auto_scale = ppu is None
    if ppu is None:
        # 区域能在一整块绘图区内以接近当前缩放的比例显示时用这个比例（正好拼成一块）；否则按当前缩放分块拼接。
        # 缺省比例在块数和内存上限内自适应，显式给的比例则严格照办，超出就报错。
        native = _native_ppu(v, pw, ph)
        fit = min(pw / span_x, ph / span_y) * 0.9  # *0.9 让整个区域落在一块绘图区内
        ppu = fit if native is None or fit >= native * 0.9 else native
    if not (math.isfinite(ppu) and ppu > 0):
        raise ArenaError("the Arena flowchart pane reports no scale; pass pixels_per_unit")
    if auto_scale:
        ppu = min(ppu, math.sqrt(MAX_PIXELS / (span_x * span_y)), MAX_SIDE / max(span_x, span_y))  # 内存上限内取最大
    for _ in range(3):
        tw, th = pw / ppu * 0.92, ph / ppu * 0.92  # tile step in world units (8% overlap)
        nx, ny = max(1, math.ceil(span_x / tw)), max(1, math.ceil(span_y / th))
        W, H = max(1, round(span_x * ppu)), max(1, round(span_y * ppu))
        if W * H > MAX_PIXELS or max(W, H) > MAX_SIDE:
            raise ArenaError(f"{W} x {H} px is too large for one PNG; lower pixels_per_unit or pick a smaller region")
        if nx * ny <= max_tiles:
            break
        if not auto_scale:
            raise ArenaError(f"{nx * ny} tiles are needed at ppu={ppu:g}; raise max_tiles or lower pixels_per_unit")
        ppu *= math.sqrt(max_tiles / (nx * ny)) * 0.98  # 块数超上限：一个像素代表更多模型单位
    else:
        raise ArenaError(f"{nx * ny} tiles are needed even at ppu={ppu:.3g}; raise max_tiles or lower pixels_per_unit")
    canvas = Image.new("RGB", (W, H), "white")
    for j in range(ny):
        for i in range(nx):
            x, y = X0 + i * tw, Y0 + j * th
            requested = (x, y, x + pw / ppu, y + ph / ppu)
            needed = (x, y, min(x + tw, X1), min(y + th, Y1))
            left, top, right, bottom = _zoom_for_tile(v, requested, needed)
            x_lo, y_lo, x_hi, y_hi = _ordered((left, top, right, bottom))
            px, right_px = round((x_lo - X0) * ppu), round((x_hi - X0) * ppu)
            py, bottom_px = round((y_lo - Y0) * ppu), round((y_hi - Y0) * ppu)
            size = (max(1, right_px - px), max(1, bottom_px - py))
            tile = _upright(_capture_pane(pane, pr), (left, top, right, bottom))
            canvas.paste(_resample(tile, size), (px, py))
    return canvas, nx * ny, ppu


def capture_animation_frame(a, model, out_path, view_name, simulation_time, *, speed=0.5, timeout=120):
    """在 Arena 14 中播放至指定仿真时刻，导出二维动画原生帧。"""
    try:
        simulation_time, speed, timeout = float(simulation_time), float(speed), float(timeout)
    except (TypeError, ValueError):
        raise ArenaError("simulation time, speed and timeout must be numbers")
    for label, value in (("simulation time", simulation_time), ("speed", speed), ("timeout", timeout)):
        if not math.isfinite(value) or value <= 0:
            raise ArenaError(f"{label} must be positive and finite")
    if os.path.splitext(out_path)[1].lower() != ".png":
        raise ArenaError("animation frame output must end in .png")
    if com_major_version(a.Version) != ARENA_14:
        raise ArenaError("automated animation playback currently supports Arena 14")
    # 上一次运行可能停在运行模式（State 12/21，End() 未复位）：先结束这些已完成的运行，正在播放或暂停的模型保持原状。
    end_completed_runs(a)
    if any(safe(lambda: a.Models.Item(i).State, 0) for i in range(1, a.Models.Count + 1)):
        raise ArenaError("an Arena run is still active; end it in Arena before starting animation playback")

    _dpi_aware()
    m = open_model(a, model)
    views = m.NamedViews
    named = next((views.Item(i) for i in range(1, views.Count + 1) if views.Item(i).Name == view_name), None)
    if named is None:
        raise ArenaError(f"unknown named view {view_name!r}; list the model views first")
    a.Visible = True
    m.Activate()
    view = m.ActiveView
    top, child, _ = model_window(m.Name)
    with _export_view(view, top, child), temporary_settings(
        m, BatchMode=False, QuietMode=True, DisableReportDatabase=True, DisplayDefaultReport=1,
        NumberOfReplications=1, RunSpeed=speed,
    ):
        view.ZoomView(named)
        deadline = time.monotonic() + timeout
        try:
            m.Go(2)  # smGoReturn：Arena 保持正常播放，MCP 可以请求暂停时间。
            state = m.State
            while state in (0, 3, 4, 5, 6) and time.monotonic() < deadline:
                time.sleep(0.02)
                state = m.State
            now = safe(lambda: m.SIMAN.RunCurrentTime)  # no SIMAN run state: the run never really started
            if state != 13 or now is None or now >= simulation_time:
                hint = ("; the run had already finished, so lower speed and retry" if state in (11, 12)
                        else "; lower speed so the tool has time to pause, or pick a time the run reaches")
                raise ArenaError(f"Arena could not set the animation breakpoint at {simulation_time:g} "
                                 f"(state {state}){hint}")
            m.BreakOnTime(simulation_time)
            state = m.State
            while state == 13 and time.monotonic() < deadline:
                time.sleep(0.05)
                state = m.State
            current_time = safe(lambda: m.SIMAN.RunCurrentTime)
            if state != 16 or current_time is None or not math.isclose(current_time, simulation_time, rel_tol=1e-9,
                                                                      abs_tol=1e-9):
                raise ArenaError(f"Arena stopped at simulation time {current_time} (state {state}), "
                                 f"expected {simulation_time:g}; check the model or increase the timeout")
            result = snapshot(a, model, out_path, view_name=view_name)
            return {**result, "simulation_time": current_time}
        finally:
            if safe(lambda: m.State, 0):
                safe(lambda: m.End())


def capture_animation_frames(a, model, out_dir, view_name, times, *, speed=0.5, timeout=120, prefix="frame"):
    """按递增的仿真时刻导出一组二维动画 PNG，用来逐帧核对运动、时钟和排队是否连贯。

    每帧都从零重新播放一次（Arena 14 的暂停不能用 COM 续播），所以帧数保持在少量：
    5 到 8 个关键时刻比一次抓几十帧更能看出运动方向和停顿。timeout 是每一帧的墙钟上限。
    """
    times = list(times)
    try:
        values = sorted({float(t) for t in times})
    except (TypeError, ValueError):
        raise ArenaError("simulation times must be numbers")
    if not values:
        raise ArenaError("give at least one simulation time")
    if len(values) != len(times):
        raise ArenaError("simulation times must be distinct")
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise ArenaError("simulation times must be greater than zero: Arena cannot break at time 0, so start at a "
                         "small positive time")
    prefix = str(prefix or "frame")
    if not prefix.strip() or any(ch in prefix for ch in '\\/:*?"<>|'):
        raise ArenaError('prefix must be a plain file name, without path separators or \\ / : * ? " < > |')
    os.makedirs(out_dir, exist_ok=True)
    frames, used = [], set()
    for t in values:
        name = f"{prefix}_{t:g}.png"
        if name in used:  # two times that print alike: keep both frames instead of overwriting one
            name = f"{prefix}_{t:.17g}.png"
        used.add(name)
        out_path = os.path.join(out_dir, name)
        res = capture_animation_frame(a, model, out_path, view_name, t, speed=speed, timeout=timeout)
        frames.append({"out": res["out"], "size": res["size"], "simulation_time": res["simulation_time"]})
    return {"model": os.path.abspath(model), "view_name": view_name, "frames": frames, "count": len(frames),
            "budget_seconds": timeout * len(frames)}
