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

from .core import ARENA_14, ArenaError, com_major_version, named_view_regions, open_model, safe, temporary_settings

VIEW_SETTINGS = {"SplitScreen": True, "SplitScreenPercent": 99, "Grid": False, "ShowRulers": False,
                 "ShowGuides": False, "ShowPageBreaks": False}
MOVING_STATES = {13, 14, 15}  # smStateRunning, smStateStepping, smStateFastForward
RUN_FRAME_STATES = {8, 9, 10, 11, 12, 16, 17, 18, 19, 20, 21}
ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x00000002


def _dpi_aware():
    safe(lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2))  # real pixels on scaled displays


def client_rect(h):
    l, t, r, b = win32gui.GetClientRect(h)
    x0, y0 = win32gui.ClientToScreen(h, (l, t))
    x1, y1 = win32gui.ClientToScreen(h, (r, b))
    return x0, y0, x1, y1


def area(r):
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def model_window(model_name):
    """定位 m.Activate() 选中的 MDI 文档，兼容动画运行时的窗口标题。"""
    tops = []
    win32gui.EnumWindows(lambda h, _: tops.append(h) if win32gui.GetClassName(h) == "SMX:Arena"
                         and win32gui.IsWindowVisible(h) else None, None)
    for top in tops:
        mdi = win32gui.FindWindowEx(top, 0, "MDIClient", None)
        if not mdi:
            continue
        child = win32gui.SendMessage(mdi, win32con.WM_MDIGETACTIVE, 0, 0)
        if not child or win32gui.GetClassName(child) != "SMX:Model":
            continue
        if win32gui.GetWindowText(child) not in (model_name, f"{model_name} - Run Mode"):
            continue
        panes = []
        win32gui.EnumChildWindows(child, lambda h, _: panes.append(h), None)
        # 绘图区使用 Afx:…:8:…，与表格视图的 AfxFrameOrView140 不同。
        panes = [p for p in panes if win32gui.GetClassName(p).startswith("Afx:") and ":8:" in
                 win32gui.GetClassName(p) and win32gui.IsWindowVisible(p)]
        if panes:
            return top, child, max(panes, key=lambda p: area(client_rect(p)))
    raise ArenaError(f"no visible Arena window for model {model_name!r}")


def modules_bbox(m):
    xs0, ys0, xs1, ys1 = [], [], [], []
    for i in range(1, m.Modules.Count + 1):
        md = m.Modules.Item(i)
        if safe(lambda: md.Definition.DataModule, True):
            continue
        sh = safe(lambda: md.shape)
        if sh is None:
            continue
        xs0.append(sh.Left), ys0.append(sh.Top), xs1.append(sh.Right), ys1.append(sh.Bottom)
    if not xs0:
        raise ArenaError("the model has no flowchart modules to show")
    return min(xs0), min(min(ys0), min(ys1)), max(xs1), max(max(ys0), max(ys1))


def _zoom_for_tile(view, requested, needed):
    """Read back the viewport before capturing. Arena 14 can double its first ZoomRegion coordinates."""
    x0, y0, x1, y1 = needed
    for _ in range(2):
        view.ZoomRegion(*requested)
        time.sleep(0.5)
        left, top, right, bottom = view.Left, view.Top, view.Right, view.Bottom
        # View bounds are rounded to integer model units.
        if (left <= x0 + 2 and right >= x1 - 2
                and min(top, bottom) <= y0 + 2 and max(top, bottom) >= y1 - 2):
            return left, top, right, bottom
    raise ArenaError(f"Arena could not display screenshot tile {needed}; reduce the zoom or select a smaller region")


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
        left, top_y, right, bottom = bounds
        # 先恢复窗口和表格的尺寸，再还原原动画或逻辑视野。Arena 的边界读数为整数。
        for _ in range(2):
            current = safe(lambda: _view_bounds(view))
            if current is None or all(abs(old - now) <= 2 for old, now in zip(bounds, current)):
                break
            safe(lambda: view.ZoomRegion(left, min(top_y, bottom), right, max(top_y, bottom)))


def snapshot(a, model, out_path, region=None, ppu=None, max_tiles=64, *, view_name=None):
    """Write a PNG of the flowchart by named view or rectangle (default: all flowchart modules).
    ppu: screen pixels per model unit; default fits the region into one screen (readable for small models)."""
    if view_name is not None and region is not None:
        raise ArenaError("choose view_name or region")
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
    if ppu is not None and (not math.isfinite(ppu) or ppu <= 0):
        raise ArenaError("screenshot pixels per unit must be positive and finite")
    if region is not None:
        if len(region) != 4 or not all(math.isfinite(float(value)) for value in region):
            raise ArenaError("screenshot region must contain four finite coordinates")
        region = tuple(float(x) for x in region)
        if not (region[0] < region[2] and region[1] < region[3]):
            raise ArenaError("screenshot region must have increasing x and y bounds")
    elif state not in RUN_FRAME_STATES:
        X0, Y0, X1, Y1 = modules_bbox(m)
        mg = 400
        region = (X0 - mg, Y0 - mg, X1 + mg, Y1 + mg)
    a.Visible = True
    m.Activate()
    v = m.ActiveView
    top, child, pane = model_window(m.Name)
    if state in RUN_FRAME_STATES:
        canvas, region = _capture_run_frame(v, pane, region, ppu)
        tiles = 1
    else:
        with _export_view(v, top, child):
            canvas, tiles = _capture_tiles(v, pane, region, ppu, max_tiles)
    out_path = os.path.abspath(out_path)
    canvas.save(out_path)
    return {"out": out_path, "size": list(canvas.size), "tiles": tiles, "region": list(region),
            **({"view_name": view_name} if view_name is not None else {}),
            "capture": "arena_flowchart_window"}


def _capture_run_frame(view, pane, region, ppu):
    # Arena 14 在暂停时用 ZoomRegion 会放大或缩小实际绘制结果，即使 COM 边界读回正常。
    # 直接截取当前帧，视野外的区域需要先在 Arena 内切到对应的命名视图。
    left, top, right, bottom = _view_bounds(view)
    client = client_rect(pane)
    width, height = client[2] - client[0], client[3] - client[1]
    if right <= left or bottom == top or width <= 0 or height <= 0:
        raise ArenaError("the Arena animation pane is not visible")
    view_region = (left, min(top, bottom), right, max(top, bottom))
    if region is None:
        region = view_region
    x0, y0, x1, y1 = region
    min_y, max_y = view_region[1], view_region[3]
    if x0 < left - 2 or x1 > right + 2 or y0 < min_y - 2 or y1 > max_y + 2:
        raise ArenaError("the screenshot region is outside the current paused view; show the whole region in Arena first")
    scale_x, scale_y = width / (right - left), height / abs(bottom - top)
    crop_y0, crop_y1 = (y0 - top, y1 - top) if bottom > top else (top - y1, top - y0)
    box = (max(0, round((x0 - left) * scale_x)), max(0, round(crop_y0 * scale_y)),
           min(width, round((x1 - left) * scale_x)), min(height, round(crop_y1 * scale_y)))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ArenaError("the screenshot region is smaller than one screen pixel")
    image = _capture_pane(pane, client).crop(box)
    if ppu is not None:
        size = (max(1, round((x1 - x0) * ppu)), max(1, round((y1 - y0) * ppu)))
        if size[0] > image.width or size[1] > image.height:
            raise ArenaError("the paused animation can only be exported at its on-screen resolution or smaller")
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
    return image, region


def _capture_tiles(v, pane, region, ppu, max_tiles):
    X0, Y0, X1, Y1 = region
    pr = client_rect(pane)
    pw, ph = pr[2] - pr[0], pr[3] - pr[1]
    if pw <= 0 or ph <= 0:
        raise ArenaError("the Arena flowchart pane is not visible")
    if ppu is None:
        ppu = min(pw / (X1 - X0), ph / (Y1 - Y0)) * 0.9  # one tile
    tw, th = pw / ppu * 0.92, ph / ppu * 0.92  # tile step in world units (8% overlap)
    nx, ny = max(1, math.ceil((X1 - X0) / tw)), max(1, math.ceil((Y1 - Y0) / th))
    if nx * ny > max_tiles:
        raise ArenaError(f"{nx * ny} tiles needed at ppu={ppu}; lower ppu or pick a smaller region")
    W = max(1, round((X1 - X0) * ppu))
    H = max(1, round((Y1 - Y0) * ppu))
    canvas = Image.new("RGB", (W, H), "white")
    for j in range(ny):
        for i in range(nx):
            x, y = X0 + i * tw, Y0 + j * th
            requested = (x, y, x + pw / ppu, y + ph / ppu)
            needed = (x, y, min(x + tw, X1), min(y + th, Y1))
            left, top, right, bottom = _zoom_for_tile(v, requested, needed)
            px, right_px = round((left - X0) * ppu), round((right - X0) * ppu)
            if bottom >= top:
                py, bottom_px = round((top - Y0) * ppu), round((bottom - Y0) * ppu)
            else:
                py, bottom_px = round((Y1 - top) * ppu), round((Y1 - bottom) * ppu)
            img = _capture_pane(pane, pr)
            size = (max(1, right_px - px), max(1, bottom_px - py))
            if size != img.size:
                img = img.resize(size, Image.Resampling.BICUBIC)
            canvas.paste(img, (px, py))
    return canvas, nx * ny


def capture_animation_frame(a, model, out_path, view_name, simulation_time, *, speed=0.5, timeout=120):
    """在 Arena 14 中播放至指定仿真时刻，导出二维动画原生帧。"""
    for label, value in (("simulation time", simulation_time), ("speed", speed), ("timeout", timeout)):
        if not math.isfinite(value) or value <= 0:
            raise ArenaError(f"{label} must be positive and finite")
    if os.path.splitext(out_path)[1].lower() != ".png":
        raise ArenaError("animation frame output must end in .png")
    if com_major_version(a.Version) != ARENA_14:
        raise ArenaError("automated animation playback currently supports Arena 14")
    if any(a.Models.Item(i).State for i in range(1, a.Models.Count + 1)):
        raise ArenaError("end the active Arena run before starting animation playback")

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
            if state != 13 or m.SIMAN.RunCurrentTime >= simulation_time:
                raise ArenaError(f"Arena could not set the animation breakpoint at {simulation_time:g} (state {state})")
            m.BreakOnTime(simulation_time)
            state = m.State
            while state == 13 and time.monotonic() < deadline:
                time.sleep(0.05)
                state = m.State
            current_time = safe(lambda: m.SIMAN.RunCurrentTime)
            if state != 16 or current_time is None or not math.isclose(current_time, simulation_time, abs_tol=1e-7):
                raise ArenaError(f"Arena stopped at simulation time {current_time} (state {state}), "
                                 f"expected {simulation_time:g}; check the model or increase the timeout")
            result = snapshot(a, model, out_path, view_name=view_name)
            return {**result, "simulation_time": current_time}
        finally:
            if m.State:
                m.End()
