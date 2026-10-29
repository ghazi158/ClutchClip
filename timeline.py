"""Timeline widgets: the whole-recording AI lane (green keep / red cut) and the edit timeline (V1 + music)."""
import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QMenu, QWidget

import auto_editor as ae

BG, LANE, TEXT = QColor("#121417"), QColor("#181b1f"), QColor("#8b919b")
ACCENT, GREEN, RED, MUSIC = QColor("#8b5cf6"), QColor("#22c55e"), QColor("#ef4444"), QColor("#3b82f6")
RULER, EDGE, SNAP_PX = 20, 6, 8


def alpha(c, a):
    c = QColor(c)
    c.setAlpha(a)
    return c


def edge_part(x, x0, x1):
    """'in'/'out' grab zones sit inside the clip (so touching clips keep separate handles), else 'body'."""
    if not x0 <= x <= x1:
        return None
    e = min(EDGE, (x1 - x0) / 4)
    return "in" if x - x0 <= e else "out" if x1 - x <= e else "body"


def clip_tip(c):
    if not c:
        return ""
    status = {"suggested": "AI suggestion (not reviewed)", "accepted": "Accepted", "manual": "Added by you"}[c["status"]]
    why = f"{c['why']}  ·  " if c.get("why") else ""
    return f"{why}{status}  ·  {ts(c['in'])} - {ts(c['out'])} ({c['out'] - c['in']:.1f}s)"


def ts(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


class TimeView(QWidget):
    """Shared zoom/scroll/ruler/playhead for both timelines. Subclasses define length() and playhead()."""

    def __init__(self, editor, height):
        super().__init__()
        self.ed, self.pps, self.offset, self.drag = editor, 10.0, 0.0, None
        self.setFixedHeight(height)
        self.setMouseTracking(True)

    def t2x(self, t):
        return (t - self.offset) * self.pps

    def x2t(self, x):
        return max(0.0, x / self.pps + self.offset)

    def fit(self):
        if self.length() > 0:
            self.pps, self.offset = max(self.width() - 24, 100) / self.length(), 0.0
        self.update()

    def zoom(self, f, anchor=None):
        anchor = self.playhead() if anchor is None else anchor
        x = self.t2x(anchor)
        self.pps = min(max(self.pps * f, 0.02), 400)
        self.offset = max(0.0, anchor - x / self.pps)
        self.update()

    def follow(self):
        x = self.t2x(self.playhead())
        if not 0 <= x <= self.width() - 30:
            self.offset = max(0.0, self.playhead() - 0.1 * self.width() / self.pps)
        self.update()

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            self.zoom(1.25 if d > 0 else 0.8, self.x2t(e.position().x()))
        else:
            self.offset = max(0.0, self.offset - d / self.pps * 0.5)
            self.update()

    def snap(self, t, points):
        if not self.ed.snapping:
            return t
        best = min(points, key=lambda p: abs(p - t), default=None)
        return best if best is not None and abs(best - t) * self.pps <= SNAP_PX else t

    def paint_ruler(self, p):
        p.fillRect(0, 0, self.width(), RULER, BG)
        step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
                     if s * self.pps >= 70), 3600)
        t = math.floor(self.offset / step) * step
        p.setPen(TEXT)
        while (x := self.t2x(t)) < self.width():
            p.drawLine(int(x), RULER - 6, int(x), RULER)
            p.drawText(int(x) + 3, RULER - 7, ts(t))
            t += step

    def paint_playhead(self, p):
        x = self.t2x(self.playhead())
        p.setPen(QPen(ACCENT, 2))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setBrush(ACCENT)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 8)]))

    def clip_rect(self, p, x0, x1, y, h, fill, status, selected):
        r = QRectF(x0, y, max(x1 - x0, 1), h)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 3, 3)
        if status == "suggested":  # hatched until reviewed
            p.setBrush(QBrush(alpha(QColor("white"), 40), Qt.BDiagPattern))
            p.drawRoundedRect(r, 3, 3)
        if selected:
            p.setPen(QPen(ACCENT, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        return r


class RecordingLane(TimeView):
    """The full raw recording. Green = in the edit, red = cut. Drag edges, drag across red to add."""

    def __init__(self, editor):
        super().__init__(editor, RULER + 58)

    def length(self):
        return self.ed.duration

    def playhead(self):
        return self.ed.src_t

    def clips(self):
        return self.ed.project["clips"] if self.ed.project else []

    def hit(self, x):
        for c in self.clips():
            part = edge_part(x, self.t2x(c["in"]), self.t2x(c["out"]))
            if part:
                return c, part
        return None, None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), LANE)
        self.paint_ruler(p)
        if not self.ed.project:
            return p.end()
        y, h = RULER + 4, self.height() - RULER - 8
        p.fillRect(QRectF(self.t2x(0), y, self.ed.duration * self.pps, h), alpha(RED, 70))
        for c in self.clips():
            fill = alpha(GREEN, 95 if c["status"] == "suggested" else 150)
            self.clip_rect(p, self.t2x(c["in"]), self.t2x(c["out"]), y, h, fill, c["status"],
                           c["id"] == self.ed.selected)
        if self.ed.score is not None:  # faint AI score curve
            s, pts = self.ed.score, []
            for x in range(0, self.width(), 2):
                i = int(self.x2t(x))
                if i < len(s):
                    pts.append(QPointF(x, y + h - min(max(s[i], 0), 1) * h))
            p.setPen(QPen(alpha(QColor("white"), 110), 1.2))
            p.drawPolyline(QPolygonF(pts))
        if isinstance(self.drag, tuple) and self.drag[0] == "create":
            a, b = sorted((self.drag[1], self.drag[2]))
            p.fillRect(QRectF(self.t2x(a), y, (b - a) * self.pps, h), alpha(ACCENT, 110))
        for m, label in zip(self.ed.marks, "IO"):
            if m is not None:
                p.setPen(QPen(QColor("#facc15"), 1.5))
                p.drawLine(QPointF(self.t2x(m), RULER), QPointF(self.t2x(m), self.height()))
                p.drawText(QPointF(self.t2x(m) + 3, RULER + 14), label)
        if self.ed.analyzing is not None:
            p.setPen(QColor("white"))
            p.drawText(self.rect().adjusted(0, RULER, 0, 0), Qt.AlignCenter,
                       f"AI is analysing the recording... {self.ed.analyzing:.0%}")
        self.paint_playhead(p)
        p.end()

    def mousePressEvent(self, e):
        if not self.ed.project:
            return
        x, t = e.position().x(), min(self.x2t(e.position().x()), self.ed.duration)
        clip, part = self.hit(x)
        if e.button() == Qt.RightButton:
            if clip:
                self.ed.selected = clip["id"]
                menu = QMenu(self)
                menu.addAction("Accept", lambda: self.ed.set_status(clip, "accepted"))
                menu.addAction("Remove from edit", lambda: self.ed.remove_clip(clip))
                menu.exec(e.globalPosition().toPoint())
            return
        if e.position().y() < RULER or part == "body":
            if clip:
                self.ed.selected = clip["id"]
            self.drag = "scrub"
            self.ed.seek_source(t)
        elif part in ("in", "out"):
            self.ed.selected = clip["id"]
            self.ed.begin_edit()
            self.drag = ("edge", clip, part)
        else:
            self.drag = ("create", t, t)
            self.ed.seek_source(t)
        self.ed.refresh()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        t = min(self.x2t(x), self.ed.duration)
        if not self.drag:
            c, part = self.hit(x) if self.ed.project else (None, None)
            self.setCursor(Qt.SizeHorCursor if part in ("in", "out") else Qt.ArrowCursor)
            self.setToolTip(clip_tip(c))
        elif self.drag == "scrub":
            self.ed.seek_source(t)
        elif self.drag[0] == "edge":
            _, c, side = self.drag
            t = self.snap(t, [self.ed.src_t] + [o[s] for o in self.clips() if o is not c for s in ("in", "out")])
            c[side] = min(t, c["out"] - 0.5) if side == "in" else max(t, c["in"] + 0.5)
            c["status"] = "accepted" if c["status"] == "suggested" else c["status"]
            self.ed.seek_source(c[side])  # show the frame at the cut while dragging
            self.ed.refresh()
        elif self.drag[0] == "create":
            self.drag = ("create", self.drag[1], t)
            self.update()

    def mouseReleaseEvent(self, e):
        d, self.drag = self.drag, None
        if isinstance(d, tuple) and d[0] == "edge":
            self.ed.end_edit()
        elif isinstance(d, tuple) and d[0] == "create" and abs(d[2] - d[1]) >= 0.5:
            self.ed.add_clip(*sorted((d[1], d[2])))
        self.update()


class SequenceView(TimeView):
    """The edit: V1 gameplay clips back to back, A2 music clips placed freely."""

    def __init__(self, editor):
        super().__init__(editor, RULER + 6 + 56 + 8 + 40 + 6)
        self.v_y, self.v_h = RULER + 6, 56
        self.a_y, self.a_h = self.v_y + self.v_h + 8, 40

    def clips(self):
        return self.ed.project["clips"] if self.ed.project else []

    def music(self):
        return self.ed.project["music"] if self.ed.project else []

    def length(self):
        ends = [m["start"] + m["out"] - m["in"] for m in self.music()]
        return max([ae.seq_length(self.clips())] + ends) + 10

    def playhead(self):
        return self.ed.seq_t

    def snap_points(self, exclude=None):
        pts = [self.ed.seq_t] + ae.starts(self.clips()) + [ae.seq_length(self.clips())]
        return pts + [m["start"] + d for m in self.music() if m is not exclude for d in (0, m["out"] - m["in"])]

    def hit(self, x, y):
        if self.v_y <= y <= self.v_y + self.v_h:
            for i, (c, s) in enumerate(zip(self.clips(), ae.starts(self.clips()))):
                part = edge_part(x, self.t2x(s), self.t2x(s + c["out"] - c["in"]))
                if part:
                    return "clip", i, part
        if self.a_y <= y <= self.a_y + self.a_h:
            for m in reversed(self.music()):
                x0, x1 = self.t2x(m["start"]), self.t2x(m["start"] + m["out"] - m["in"])
                fi, fo = x0 + m["fade_in"] * self.pps, x1 - m["fade_out"] * self.pps
                if y < self.a_y + 10 and abs(x - fi) <= EDGE:
                    return "music", m, "fade_in"
                if y < self.a_y + 10 and abs(x - fo) <= EDGE:
                    return "music", m, "fade_out"
                if abs(x - x0) <= EDGE:
                    return "music", m, "in"
                if abs(x - x1) <= EDGE:
                    return "music", m, "out"
                if x0 < x < x1:
                    return "music", m, "body"
        return None, None, None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), LANE)
        self.paint_ruler(p)
        p.fillRect(QRectF(0, self.v_y, self.width(), self.v_h), BG)
        p.fillRect(QRectF(0, self.a_y, self.width(), self.a_h), BG)
        p.setPen(alpha(TEXT, 120))
        p.drawText(QPointF(6, self.v_y + self.v_h - 6), "V1")
        p.drawText(QPointF(6, self.a_y + self.a_h - 6), "MUSIC")
        for i, (c, s) in enumerate(zip(self.clips(), ae.starts(self.clips()))):
            d = c["out"] - c["in"]
            fill = alpha(GREEN, 110 if c["status"] == "suggested" else 175)
            r = self.clip_rect(p, self.t2x(s) + 1, self.t2x(s + d) - 1, self.v_y + 2, self.v_h - 4, fill,
                               c["status"], c["id"] == self.ed.selected)
            if r.width() > 34:
                p.save()
                p.setClipRect(r)
                p.setPen(QColor("white"))
                tag = {"suggested": "AI", "accepted": "✓", "manual": "+"}[c["status"]]
                p.drawText(r.adjusted(6, 4, -4, -4), Qt.AlignLeft | Qt.AlignTop, f"{i + 1}  {tag}  {c.get('why', '')}")
                p.setPen(alpha(QColor("white"), 170))
                p.drawText(r.adjusted(6, 4, -4, -4), Qt.AlignLeft | Qt.AlignBottom, f"{d:.1f}s")
                p.restore()
        for m in self.music():
            x0, x1 = self.t2x(m["start"]), self.t2x(m["start"] + m["out"] - m["in"])
            r = self.clip_rect(p, x0, x1, self.a_y + 2, self.a_h - 4, alpha(MUSIC, 150), "music",
                               m["id"] == self.ed.selected)
            p.setPen(Qt.NoPen)
            p.setBrush(alpha(QColor("black"), 110))
            fi, fo = m["fade_in"] * self.pps, m["fade_out"] * self.pps
            p.drawPolygon(QPolygonF([r.bottomLeft(), r.topLeft(), QPointF(x0 + fi, r.top())]))
            p.drawPolygon(QPolygonF([r.bottomRight(), r.topRight(), QPointF(x1 - fo, r.top())]))
            p.setBrush(QColor("white"))
            p.drawEllipse(QPointF(x0 + fi, r.top() + 4), 3.5, 3.5)
            p.drawEllipse(QPointF(x1 - fo, r.top() + 4), 3.5, 3.5)
            p.setPen(QColor("white"))
            p.drawText(r.adjusted(8 + fi, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       f"{os.path.basename(m['path'])}  {m['volume']:.0%}")
        if isinstance(self.drag, tuple) and self.drag[0] == "move" and self.drag[3]:
            x = self.t2x(self.drop_time(self.drag[4]))
            p.setPen(QPen(ACCENT, 3))
            p.drawLine(QPointF(x, self.v_y - 2), QPointF(x, self.v_y + self.v_h + 2))
        self.paint_playhead(p)
        p.end()

    def drop_index(self, t):
        for j, (c, s) in enumerate(zip(self.clips(), ae.starts(self.clips()))):
            if t < s + (c["out"] - c["in"]) / 2:
                return j
        return len(self.clips())

    def drop_time(self, t):
        j, st = self.drop_index(t), ae.starts(self.clips())
        return st[j] if j < len(st) else ae.seq_length(self.clips())

    def mousePressEvent(self, e):
        if not self.ed.project:
            return
        x, y = e.position().x(), e.position().y()
        t = self.x2t(x)
        kind, item, part = self.hit(x, y)
        if e.button() == Qt.RightButton:
            self.context_menu(kind, item, e.globalPosition().toPoint())
            return
        if kind == "clip":
            c = self.clips()[item]
            self.ed.selected = c["id"]
            if part == "body":
                self.drag = ("move", item, x, False, t)
                self.ed.seek_seq(t)
            else:
                self.ed.begin_edit()
                self.drag = ("trim", c, part, t, c["in"], c["out"])
        elif kind == "music":
            self.ed.selected = item["id"]
            self.ed.begin_edit()
            self.drag = ("music", item, part, t, dict(item))
        else:
            self.ed.selected = None
            self.drag = "scrub"
            self.ed.seek_seq(t)
        self.ed.refresh()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        t = self.x2t(x)
        if not self.drag:
            kind, item, part = self.hit(x, y) if self.ed.project else (None, None, None)
            self.setCursor(Qt.SizeHorCursor if part in ("in", "out", "fade_in", "fade_out")
                           else Qt.OpenHandCursor if part == "body" else Qt.ArrowCursor)
            self.setToolTip(clip_tip(self.clips()[item]) if kind == "clip" else "")
            return
        if self.drag == "scrub":
            self.ed.seek_seq(t)
            return
        kind = self.drag[0]
        if kind == "move":
            _, i, x0, moving, _ = self.drag
            self.drag = ("move", i, x0, moving or abs(x - x0) > 5, t)
            self.update()
        elif kind == "trim":
            _, c, side, t0, a, b = self.drag
            dt = t - t0
            if side == "in":  # ripple: the clip's start stays, later clips shift
                c["in"] = min(max(a + dt, 0.0), c["out"] - 0.5)
            else:
                st = ae.starts(self.clips())[self.clips().index(c)]
                end = self.snap(st + b - a + dt, [p for p in self.snap_points() if p != st + b - a])
                c["out"] = min(max(a + end - st, c["in"] + 0.5), self.ed.duration)
            c["status"] = "accepted" if c["status"] == "suggested" else c["status"]
            self.ed.seek_source(c[side], keep_mode=True)
            self.ed.refresh()
        elif kind == "music":
            _, m, part, t0, o = self.drag
            dt = t - t0
            length = o["out"] - o["in"]
            if part == "body":
                start = max(0.0, o["start"] + dt)
                snapped = self.snap(start, self.snap_points(m))
                end_snapped = self.snap(start + length, self.snap_points(m)) - length
                m["start"] = snapped if snapped != start else max(0.0, end_snapped)
            elif part == "in":
                d = min(max(dt, -o["in"], -o["start"]), length - 0.5)
                m["in"], m["start"] = o["in"] + d, o["start"] + d
            elif part == "out":
                m["out"] = min(max(o["out"] + dt, o["in"] + 0.5), self.ed.music_length(m["path"]))
            elif part == "fade_in":
                m["fade_in"] = min(max(o["fade_in"] + dt, 0.0), length / 2)
            else:
                m["fade_out"] = min(max(o["fade_out"] - dt, 0.0), length / 2)
            self.ed.refresh()

    def mouseReleaseEvent(self, e):
        d, self.drag = self.drag, None
        if isinstance(d, tuple) and d[0] == "move" and d[3]:
            j = self.drop_index(d[4])
            self.ed.move_clip(d[1], j - 1 if j > d[1] else j)
        elif isinstance(d, tuple) and d[0] in ("trim", "music"):
            self.ed.end_edit()
        self.update()

    def context_menu(self, kind, item, pos):
        menu = QMenu(self)
        if kind == "clip":
            c = self.clips()[item]
            self.ed.selected = c["id"]
            menu.addAction("Accept", lambda: self.ed.set_status(c, "accepted"))
            menu.addAction("Split at playhead", self.ed.split)
            menu.addAction("Volume...", lambda: self.ed.ask_volume(c))
            menu.addAction("Delete", lambda: self.ed.remove_clip(c))
        elif kind == "music":
            self.ed.selected = item["id"]
            menu.addAction("Volume...", lambda: self.ed.ask_volume(item))
            menu.addAction("Delete", lambda: self.ed.remove_music(item))
        else:
            menu.addAction("Add music...", self.ed.add_music_dialog)
        self.ed.refresh()
        menu.exec(pos)
