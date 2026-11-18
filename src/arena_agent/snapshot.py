"""Screenshot of an Arena model's flowchart, so an agent (or its user) can see what was built.

The flowchart pane is zoomed tile by tile with IView.ZoomRegion; after each zoom the visible world extents are read
back (IView.Left/Top/Right/Bottom), so every tile is pasted at its exact place whatever zoom Arena actually chose.
Each tile is captured directly from the Arena flowchart window.
"""
import ctypes
import math
import os
import time

import win32con
import win32gui
from PIL import Image, ImageGrab

from .core import ArenaError, named_view_regions, open_model, safe


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
    """The flowchart pane of the model's MDI window (largest Afx:...:8: child that is not the spreadsheet)."""
    tops = []
    win32gui.EnumWindows(lambda h, _: tops.append(h) if win32gui.GetClassName(h) == "SMX:Arena"
                         and win32gui.IsWindowVisible(h) else None, None)
    for top in tops:
        kids = []
        win32gui.EnumChildWindows(top, lambda h, _: kids.append(h), None)
        for k in kids:
            if win32gui.GetClassName(k) == "SMX:Model" and win32gui.GetWindowText(k) == model_name:
                panes = []
                win32gui.EnumChildWindows(k, lambda h, _: panes.append(h), None)
                # the flowchart canvas is the "Afx:<module>:8:..." child; "AfxFrameOrView140" is the spreadsheet
                panes = [p for p in panes if win32gui.GetClassName(p).startswith("Afx:") and ":8:" in
                         win32gui.GetClassName(p) and win32gui.IsWindowVisible(p)]
                if panes:
                    return top, k, max(panes, key=lambda p: area(client_rect(p)))
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
    img = ImageGrab.grab(window=pane)
    x0, y0, x1, y1 = client
    if img.size == (x1 - x0, y1 - y0):
        return img
    # 某些窗口会连同滚动条和边框一起返回，按客户区实际边界裁切。
    left, top, right, bottom = win32gui.GetWindowRect(pane)
    if img.size == (right - left, bottom - top) and left <= x0 < x1 <= right and top <= y0 < y1 <= bottom:
        return img.crop((x0 - left, y0 - top, x1 - left, y1 - top))
    raise ArenaError("the Arena flowchart window changed size during capture; retry the export")


def snapshot(a, model, out_path, region=None, ppu=None, max_tiles=64, *, view_name=None):
    """Write a PNG of the flowchart by named view or rectangle (default: all flowchart modules).
    ppu: screen pixels per model unit; default fits the region into one screen (readable for small models)."""
    if view_name is not None and region is not None:
        raise ArenaError("choose view_name or region")
    _dpi_aware()
    m = open_model(a, model)
    if view_name is not None:
        views = named_view_regions(m)
        if view_name not in views:
            raise ArenaError(f"unknown named view {view_name!r}; available views: {', '.join(views) or '(none)'}")
        region = views[view_name]
    if region is not None:
        if len(region) != 4 or not all(math.isfinite(float(value)) for value in region):
            raise ArenaError("screenshot region must contain four finite coordinates")
        X0, Y0, X1, Y1 = (float(x) for x in region)
    else:
        X0, Y0, X1, Y1 = modules_bbox(m)
        mg = 400
        X0, Y0, X1, Y1 = X0 - mg, Y0 - mg, X1 + mg, Y1 + mg
    if not (X0 < X1 and Y0 < Y1):
        raise ArenaError("screenshot region must have increasing x and y bounds")
    if ppu is not None and (not math.isfinite(ppu) or ppu <= 0):
        raise ArenaError("screenshot pixels per unit must be positive and finite")
    a.Visible = True
    m.Activate()
    v = m.ActiveView
    # SplitScreen must stay True (False collapses the flowchart pane to zero height); 99% gives the flowchart nearly
    # the whole window, with the spreadsheet squeezed below it
    for prop, val in (("SplitScreen", True), ("SplitScreenPercent", 99), ("Grid", False), ("ShowRulers", False),
                      ("ShowGuides", False), ("ShowPageBreaks", False)):
        safe(lambda: setattr(v, prop, val))
    top, child, pane = model_window(m.Name)
    win32gui.ShowWindow(top, win32con.SW_MAXIMIZE)
    win32gui.ShowWindow(child, win32con.SW_MAXIMIZE)
    safe(lambda: win32gui.SetForegroundWindow(top))
    time.sleep(0.8)
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
    out_path = os.path.abspath(out_path)
    canvas.save(out_path)
    return {"out": out_path, "size": [W, H], "tiles": nx * ny, "region": [X0, Y0, X1, Y1],
            **({"view_name": view_name} if view_name is not None else {}),
            "capture": "arena_flowchart_window"}
