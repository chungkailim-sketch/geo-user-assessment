"""Vector charts for the PDF (ReportLab graphics): score gauge and horizontal score bars."""

from reportlab.graphics.shapes import Circle, Drawing, Rect, String, Wedge
from reportlab.lib.colors import HexColor

GREEN = HexColor("#27933a")
GREEN_DEEP = HexColor("#1b7531")
LIME = HexColor("#aaca43")
YELLOW = HexColor("#e8a200")
ORANGE = HexColor("#ec835a")
RED = HexColor("#d03b3b")
TRACK = HexColor("#eeeeee")
INK = HexColor("#1a1a1a")
MUTED = HexColor("#5a5a5a")
LEADER = HexColor("#e8a200")
WHITE = HexColor("#ffffff")


def score_color(score: float):
    """Status colours keyed to the GEO bands (75 good / 60 fair / 40 weak)."""
    return GREEN if score >= 75 else LIME if score >= 60 else YELLOW if score >= 40 else RED


def gauge(score: float, label: str, font: str, bold: str, size: float = 200) -> Drawing:
    """Half-ring gauge: pie wedges for track and value, a white disc punches out the ring's centre."""
    drawing = Drawing(size, size * 0.66)
    drawing.hAlign = "CENTER"
    cx, cy, radius = size / 2, size * 0.16, size * 0.46
    drawing.add(Wedge(cx, cy, radius, 0, 180, fillColor=TRACK, strokeColor=None))
    value = max(0.0, min(score, 100.0))
    if value > 0:
        drawing.add(Wedge(cx, cy, radius, 180 - 180 * value / 100, 180, fillColor=score_color(score), strokeColor=None))
    drawing.add(Circle(cx, cy, radius * 0.72, fillColor=WHITE, strokeColor=None))
    drawing.add(Rect(0, 0, size, cy, fillColor=WHITE, strokeColor=None))
    drawing.add(String(cx, cy + radius * 0.12, f"{score:.0f}", fontName=bold, fontSize=size * 0.2,
                       fillColor=INK, textAnchor="middle"))
    drawing.add(String(cx, cy - size * 0.1, label, fontName=font, fontSize=size * 0.06,
                       fillColor=MUTED, textAnchor="middle"))
    return drawing


def score_bars(rows: list[tuple[str, float]], font: str, width: float = 440, label_width: float = 150,
               compare: list[float] | None = None) -> Drawing:
    """One horizontal bar per (label, score 0–100); `compare` adds a thinner second bar per row (benchmark)."""
    row_h = 30 if compare else 22
    drawing = Drawing(width, row_h * len(rows) + 4)
    track_w = width - label_width - 40
    for index, (label, score) in enumerate(rows):
        y = drawing.height - (index + 1) * row_h
        drawing.add(String(0, y + row_h / 2 - 4, label, fontName=font, fontSize=9, fillColor=INK))
        bar_y = y + (row_h / 2 if compare else 5)
        bar_h = 9 if compare else 12
        drawing.add(Rect(label_width, bar_y, track_w, bar_h, fillColor=TRACK, strokeColor=None, rx=3, ry=3))
        fill = track_w * max(0.0, min(score, 100.0)) / 100
        if fill:
            drawing.add(Rect(label_width, bar_y, fill, bar_h, fillColor=GREEN if compare else score_color(score),
                             strokeColor=None, rx=3, ry=3))
        drawing.add(String(label_width + track_w + 6, bar_y + 2, f"{score:.0f}", fontName=font, fontSize=9, fillColor=INK))
        if compare:
            other = compare[index]
            other_y = bar_y - 11
            drawing.add(Rect(label_width, other_y, track_w, 9, fillColor=TRACK, strokeColor=None, rx=3, ry=3))
            if other:
                drawing.add(Rect(label_width, other_y, track_w * min(other, 100.0) / 100, 9, fillColor=LEADER,
                                 strokeColor=None, rx=3, ry=3))
            drawing.add(String(label_width + track_w + 6, other_y + 1, f"{other:.0f}", fontName=font, fontSize=9,
                               fillColor=MUTED))
    return drawing
