# SPDX-License-Identifier: LGPL-2.1-or-later AND MIT
# The event filter (_Filter) and the projection helpers (_vv, _squeeze) are
# adapted from LiveSectionAnalyzer, Copyright (c) 2026 Sedat Kuran, MIT licence:
# see LICENSES/MIT-LiveSectionAnalyzer.txt. The rest is LGPL-2.1-or-later.
"""On-canvas handle of the active section plane, after the cut tool of
PrusaSlicer and OrcaSlicer: a yellow stem along the plane normal (it points to
the removed side) ends in a ball, with a red and a green pair of arrows beside
the ball.

* ball on the stem: drag to move the plane along its normal (snaps to vertices,
  edge midpoints and circle centres under the cursor, Shift for whole
  millimetres, Ctrl for no snapping); click to flip
* red arrows: drag to tilt the plane about its u axis, green arrows about v (the
  ball goes where the arrows point). A dial shows the angle. Steps: 1 degree,
  5 with the cursor on the dial's scale, 45 inside its inner ring, Shift 15,
  Ctrl free
* small white ball on the plane: drag to move the handle; it snaps like the
  ball on the stem, and the plane then passes through the snapped point
* while dragging, a small field beside the cursor shows the value. A number key
  during the drag moves the typing into the field (the plane then stays put)
  and Enter sets the value; after the drag, a click into the field does the
  same. Escape cancels a drag

Mouse events reach the handle through a Qt event filter on the 3D view's
viewport, before FreeCAD's navigation and selection see them, so a drag on the
handle neither turns the camera nor selects anything. Keys go to the
QGraphicsView around the viewport (its focus proxy), so the filter sits there
too; during a drag it also takes the number keys from FreeCAD's shortcuts
(0 to 6 choose standard views). That approach, and the
correction for Coin's portrait viewport mapping, come from the
LiveSectionAnalyzer macro by sedatkrn (MIT; see the header).
"""

import math

import numpy as np
from pivy import coin
from PySide import QtCore, QtGui

import FreeCAD as App

from . import geom, prefs

# Geometry in screen pixels. The stem is the "Handle length" preference; balls
# and arrows are drawn for BALL_R and scaled to the "Ball size" preference. The
# arrows' proportions to the ball are PrusaSlicer's (GLGizmoCut3D).
BALL_R = 13.0                   # ball on the stem
CONE_GAP = 1.25 * BALL_R        # ball centre to the base of an arrow
CONE_LEN = 1.8 * BALL_R
CONE_R = 0.75 * BALL_R
BASE_R = 7.0                    # ball on the plane
INACTIVE_R = 8.5                # balls of the other planes
STEM_R = 1.6
DIAL = 0.85                     # dial radius / stem length (PrusaSlicer)
DIAL_FINE = 1.15                # cursor between DIAL and this (x stem length): 5 degree steps
DIAL_STEP = 5.0                 # degrees between ticks; a long tick every third
ARC_W = 3.0                     # width of the angle arc while tilting
PICK_PX = 6.0                   # around the stem
GRAB_PX = 5.0                   # extra radius around balls and arrows for a hit
SNAP_PX = 12.0                  # these two follow the cursor, not the handle size
CLICK_PX = 3.0
TYPED_CHARS = "0123456789.,+-"  # during a drag, these keys go to the value field

COL_STEM = (1.0, 0.9, 0.1)      # PrusaSlicer's yellow grabber colour
COL_BALL = (0.55, 0.55, 0.55)
COL_HOT = COL_STEM              # a ball under the cursor
COL_TILT = {"u": (1.0, 0.235, 0.357),        # OrcaSlicer's axis red and green
            "v": (0.392, 0.784, 0.094)}
COL_BASE = (0.95, 0.95, 0.95)
COL_DIAL = (0.92, 0.92, 0.92)
COL_INACTIVE = (0.6, 0.6, 0.6)

_ALL, _NONE = coin.SO_SWITCH_ALL, coin.SO_SWITCH_NONE


def _vec3(a):
    return coin.SbVec3f(float(a[0]), float(a[1]), float(a[2]))


def _rotation(u, v, n):
    """SbRotation taking local x, y, z to u, v, n."""
    m = App.Matrix()
    m.A11, m.A21, m.A31 = float(u[0]), float(u[1]), float(u[2])
    m.A12, m.A22, m.A32 = float(v[0]), float(v[1]), float(v[2])
    m.A13, m.A23, m.A33 = float(n[0]), float(n[1]), float(n[2])
    q = App.Rotation(m).Q
    return coin.SbRotation(q[0], q[1], q[2], q[3])


def _material(color):
    m = coin.SoMaterial()
    _paint(m, color)
    return m


def _paint(mat, color):
    mat.diffuseColor.setValue(*color)
    mat.emissiveColor.setValue(*(c * 0.35 for c in color))


def _lighter(color, t=0.3):
    return tuple(c + (1.0 - c) * t for c in color)


def _swing(p, axis):
    """Where the ball goes when tilting about axis 'u' or 'v' (the arrows point there)."""
    return p.v if axis == "u" else p.u


def _depth(function, near, far, test=True):
    d = coin.SoDepthBuffer()
    d.test = test
    d.write = test
    d.function = function
    d.range.setValue(near, far)
    return d


def _on_top(group):
    """Draw group over everything drawn before it, with its own parts still hiding
    each other. The first pass sets the depth under the group's pixels to the far
    plane (its colours are overdrawn by the second pass: the same pixels); the
    second pass draws the group with the depth test."""
    sep = coin.SoSeparator()
    clear = coin.SoSeparator()
    clear.addChild(_depth(coin.SoDepthBuffer.ALWAYS, 1.0, 1.0))
    clear.addChild(group)
    sep.addChild(clear)
    draw = coin.SoSeparator()
    draw.addChild(_depth(coin.SoDepthBuffer.LEQUAL, 0.0, 1.0))
    draw.addChild(group)
    sep.addChild(draw)
    return sep


def dial_lines(length):
    """Lines of the angle dial in pixels, in the dial's plane (x: the normal when
    the tilt started, y: where the ball goes): the circle, ticks every DIAL_STEP
    degrees (every third one long), the 45-degree snap ring and the start
    direction. Returns points (N, 3) and the vertex count of each line."""
    R = DIAL * length
    a = np.radians(np.arange(0.0, 360.0 + DIAL_STEP / 2, DIAL_STEP))
    cs = np.column_stack((np.cos(a), np.sin(a), np.zeros_like(a)))
    pts, counts = [R * cs], [len(a)]
    for i, d in enumerate(cs[:-1]):
        pts.append(np.array((R * d, R * (1.10 if i % 3 == 0 else 1.05) * d)))
        counts.append(2)
    for d in cs[:-1:int(round(45.0 / DIAL_STEP))]:
        pts.append(np.array((R / 3 * d, 2 * R / 3 * d)))
        counts.append(2)
    pts.append(np.array(((0.0, 0.0, 0.0), (length, 0.0, 0.0))))
    counts.append(2)
    return np.vstack(pts), counts


def dial_step(r, length):
    """Tilt step in degrees for a cursor r pixels from the dial's centre:
    45 inside the inner ring, 5 on the scale, else 1 (PrusaSlicer's snap zones)."""
    R = DIAL * length
    if R / 3 <= r <= 2 * R / 3:
        return 45.0
    if R <= r <= DIAL_FINE * length:
        return 5.0
    return 1.0


def _arc_strip(length, deg, width):
    """Triangle strip of the angle arc from 0 to deg at radius length (pixels)."""
    t = np.radians(np.linspace(0.0, deg, max(2, int(abs(deg) / 3) + 2)))
    d = np.column_stack((np.cos(t), np.sin(t), np.zeros_like(t)))
    out = np.empty((2 * len(t), 3))
    out[0::2] = (length - width / 2) * d
    out[1::2] = (length + width / 2) * d
    return out


def _set_points(coords, pts):
    pts = np.asarray(pts, dtype=float)
    coords.point.setNum(len(pts))
    coords.point.setValues(0, len(pts), pts.tolist())


class _Filter(QtCore.QObject):
    def __init__(self, gizmo):
        super().__init__()
        self.gizmo = gizmo

    def eventFilter(self, obj, ev):
        try:
            return bool(self.gizmo.handle_event(obj, ev))
        except Exception as e:                      # never break the 3D view
            App.Console.PrintError("CutPlane handles: %s\n" % e)
            return False


class ValueField(QtGui.QLineEdit):
    """Small input over the 3D view showing the dragged value."""

    def __init__(self, parent, on_enter, on_escape):
        super().__init__(parent)
        self.on_enter = on_enter
        self.on_escape = on_escape
        self.kind = None
        self.setFixedWidth(92)
        self.setAlignment(QtCore.Qt.AlignRight)
        self.setStyleSheet("QLineEdit { background: rgba(255,255,255,235); color: #222;"
                           " border: 1px solid #d9b300; border-radius: 3px; padding: 1px 4px; }")
        self.returnPressed.connect(self._enter)
        self.hide()

    def keyPressEvent(self, ev):
        if ev.key() == QtCore.Qt.Key_Escape:
            self.dismiss()
            self.on_escape()
            return
        super().keyPressEvent(ev)

    def dismiss(self):
        """Hide, giving the keyboard back to the 3D view (Qt would pass it on to
        the next widget in the window, such as the panel)."""
        if self.hasFocus():
            self.parentWidget().setFocus()
        self.hide()

    def _enter(self):
        txt = self.text().replace(",", ".")
        num = "".join(ch for ch in txt if ch in "+-.0123456789eE")
        try:
            val = float(num)
        except ValueError:
            return
        self.dismiss()
        self.on_enter(self.kind, val)

    def show_value(self, kind, text, x, y):
        """Show the value up and to the right of the cursor at (x, y)."""
        self.kind = kind
        if not self.hasFocus():
            self.setText(text)
        pw, ph = self.parentWidget().width(), self.parentWidget().height()
        px = int(min(max(x + 18, 2), pw - self.width() - 2))
        py = int(min(max(y - 30, 2), ph - self.height() - 2))
        self.move(px, py)
        self.show()
        self.raise_()


class Gizmo:
    def __init__(self, session, view, parent_node):
        self.s = session
        self.view = view
        self.viewer = view.getViewer()
        self.widget = view.graphicsView().viewport()
        self.key_widget = view.graphicsView()           # has the keyboard focus, for the viewport
        self.k = 1.0                    # world units per pixel at the handle origin
        self.length = float(prefs.handle_px())          # stem, px
        self.g = prefs.ball_px() / (2.0 * BALL_R)       # scale of balls and arrows
        self.visible = True
        self.drag = None
        self.hot = None
        self._removed = False
        self._swallow = False           # after Escape: the rest of that button press is ours
        self._build(parent_node)
        self.field = ValueField(self.widget, self._field_enter, self._field_escape)
        self._filter = _Filter(self)
        self.widget.installEventFilter(self._filter)
        self.key_widget.installEventFilter(self._filter)
        self._cam = None                # camera orientation at the last frame

    def set_size(self, px):
        """Stem length on screen in pixels (Session calls it when the preference changes)."""
        if abs(px - self.length) < 1e-9 or self._removed:
            return
        self.length = float(px)
        self.refresh()

    def set_ball(self, px):
        """Ball diameter on screen in pixels; the arrows and the small balls follow."""
        g = px / (2.0 * BALL_R)
        if abs(g - self.g) < 1e-9 or self._removed:
            return
        self.g = g
        self.refresh()

    # -- scene -------------------------------------------------------------
    def _build(self, parent):
        self.parent = parent
        self.root = coin.SoAnnotation()          # drawn after the scene, transparent parts too
        parent.addChild(self.root)
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        self.root.addChild(pick)
        cb = coin.SoCallback()
        cb.setCallback(self._on_render)
        self.root.addChild(cb)
        cx = coin.SoComplexity()
        cx.value = 0.7                           # rounder balls and arrows at large sizes
        self.root.addChild(cx)
        self.switch = coin.SoSwitch()
        self.switch.whichChild = _ALL
        self.root.addChild(self.switch)

        # the dial, while a pair of tilt arrows is under the cursor or dragged
        self.dial_sw = coin.SoSwitch()
        self.dial_sw.whichChild = _NONE
        self.switch.addChild(self.dial_sw)
        dial = coin.SoSeparator()
        self.dial_sw.addChild(dial)
        dial.addChild(_depth(coin.SoDepthBuffer.ALWAYS, 0.0, 1.0, test=False))
        lm = coin.SoLightModel()
        lm.model = coin.SoLightModel.BASE_COLOR
        dial.addChild(lm)
        self.dial_tf = coin.SoTransform()
        dial.addChild(self.dial_tf)
        lines = coin.SoSeparator()
        bc = coin.SoBaseColor()
        bc.rgb.setValue(*COL_DIAL)
        lines.addChild(bc)
        self.dial_coords = coin.SoCoordinate3()
        lines.addChild(self.dial_coords)
        self.dial_lines = coin.SoLineSet()
        lines.addChild(self.dial_lines)
        dial.addChild(lines)
        self.arc_sw = coin.SoSwitch()
        self.arc_sw.whichChild = _NONE
        arc = coin.SoSeparator()
        self.arc_color = coin.SoBaseColor()
        arc.addChild(self.arc_color)
        self.arc_coords = coin.SoCoordinate3()
        arc.addChild(self.arc_coords)
        arc.addChild(coin.SoTriangleStripSet())           # a band: lines are 1 px on some drivers
        self.arc_sw.addChild(arc)
        dial.addChild(self.arc_sw)
        self._dial_len = None                             # stem length the dial was built for

        # stem, ball and arrows: over the scene, hiding each other
        geo = coin.SoGroup()
        active = coin.SoSeparator()
        geo.addChild(active)
        self.tf = coin.SoTransform()                      # origin, (u, v, n), world per pixel
        active.addChild(self.tf)
        self.gscale = coin.SoScale()                      # ball size; shared with the base ball

        stem = coin.SoSeparator()
        lm = coin.SoLightModel()
        lm.model = coin.SoLightModel.BASE_COLOR
        stem.addChild(lm)
        self.stem_color = coin.SoBaseColor()
        self.stem_color.rgb.setValue(*COL_STEM)
        stem.addChild(self.stem_color)
        rx = coin.SoRotationXYZ()
        rx.axis = coin.SoRotationXYZ.X
        rx.angle = math.pi / 2                            # Coin's cylinder axis y -> local z (normal)
        stem.addChild(rx)
        self.stem_t = coin.SoTranslation()
        stem.addChild(self.stem_t)
        self.stem_cyl = coin.SoCylinder()
        stem.addChild(self.stem_cyl)
        active.addChild(stem)

        head = coin.SoSeparator()
        self.head_t = coin.SoTranslation()
        head.addChild(self.head_t)
        head.addChild(self.gscale)
        self.ball_mat = _material(COL_BALL)
        head.addChild(self.ball_mat)
        sph = coin.SoSphere()
        sph.radius = BALL_R
        head.addChild(sph)
        self.cone_sw, self.cone_mats = {}, {}
        for axis in ("u", "v"):
            sw = coin.SoSwitch()
            sw.whichChild = _ALL
            sep = coin.SoSeparator()
            sw.addChild(sep)
            mat = _material(COL_TILT[axis])
            sep.addChild(mat)
            # the arrows point where the ball goes: along v for a tilt about u
            for dx, dy in (((0, 1), (0, -1)) if axis == "u" else ((1, 0), (-1, 0))):
                c = coin.SoSeparator()
                t = coin.SoTranslation()
                d = CONE_GAP + CONE_LEN / 2
                t.translation.setValue(dx * d, dy * d, 0.0)
                c.addChild(t)
                r = coin.SoRotationXYZ()
                r.axis = coin.SoRotationXYZ.Z
                r.angle = math.atan2(-dx, dy)              # Coin's cone axis y -> (dx, dy)
                c.addChild(r)
                cone = coin.SoCone()
                cone.bottomRadius = CONE_R
                cone.height = CONE_LEN
                c.addChild(cone)
                sep.addChild(c)
            head.addChild(sw)
            self.cone_sw[axis] = sw
            self.cone_mats[axis] = mat
        active.addChild(head)

        self.inactive = coin.SoSeparator()
        geo.addChild(self.inactive)
        self._inactive_tfs = []
        self.switch.addChild(_on_top(geo))

        # the ball on the plane last, so it stays visible when the stem points at the viewer
        base = coin.SoSeparator()
        base.addChild(self.tf)
        base.addChild(self.gscale)
        self.base_mat = _material(COL_BASE)
        base.addChild(self.base_mat)
        bs = coin.SoSphere()
        bs.radius = BASE_R
        base.addChild(bs)
        self.switch.addChild(_on_top(base))

    def remove(self):
        if self._removed:
            return
        self._removed = True
        for w in (self.widget, self.key_widget):
            try:
                w.removeEventFilter(self._filter)
            except Exception:
                pass
        try:
            self.field.dismiss()
            self.field.deleteLater()
        except Exception:
            pass
        try:
            self.parent.removeChild(self.root)
        except Exception:
            pass

    def set_visible(self, flag):
        self.visible = flag
        self.switch.whichChild = _ALL if flag else _NONE
        if not flag:
            self.field.dismiss()

    # -- pose --------------------------------------------------------------
    def refresh(self):
        """Follow the session's planes (call after any plane change)."""
        planes, act = self.s.planes, self.s.active
        if not planes:
            self.switch.whichChild = _NONE
            return
        if self.visible:
            self.switch.whichChild = _ALL
        p = planes[act]
        k, L, g = self.k, self.length, self.g
        self.tf.translation.setValue(_vec3(p.origin))
        self.tf.rotation.setValue(_rotation(p.u, p.v, p.n))
        self.tf.scaleFactor.setValue(k, k, k)
        self.gscale.scaleFactor.setValue(g, g, g)
        self.stem_t.translation.setValue(0.0, L / 2, 0.0)
        self.stem_cyl.height = L
        self.stem_cyl.radius = STEM_R * g
        self.head_t.translation.setValue(0.0, 0.0, L)
        others = [q for i, q in enumerate(planes) if i != act]
        if len(others) != len(self._inactive_tfs):
            self.inactive.removeAllChildren()
            self._inactive_tfs = []
            for _ in others:
                sep = coin.SoSeparator()
                tf = coin.SoTransform()
                sep.addChild(tf)
                sep.addChild(_material(COL_INACTIVE))
                sph = coin.SoSphere()
                sph.radius = INACTIVE_R
                sep.addChild(sph)
                self.inactive.addChild(sep)
                self._inactive_tfs.append(tf)
        for tf, q in zip(self._inactive_tfs, others):
            tf.translation.setValue(_vec3(q.origin))
            tf.scaleFactor.setValue(k * g, k * g, k * g)
        self._update_dial()

    def _part(self):
        """The part being dragged, else the one under the cursor."""
        if self.drag is not None:
            return self.drag["part"]
        return self.hot[0] if self.hot else None

    def _set_hot(self, hot):
        if hot == self.hot:
            return
        self.hot = hot
        self._update_look()

    def _update_look(self):
        """Colours and visibility as in PrusaSlicer: over the ball the arrows hide;
        over a pair of arrows the other pair hides, stem and ball take its colour,
        and the dial appears."""
        part = self._part()
        tilt = part[-1] if part in ("tilt_u", "tilt_v") else None
        moving = part == "head" or (part == "ball" and self.drag is not None)
        for axis in ("u", "v"):
            show = tilt == axis or (tilt is None and not moving)
            self.cone_sw[axis].whichChild = _ALL if show else _NONE
            _paint(self.cone_mats[axis], _lighter(COL_TILT[axis]) if tilt == axis else COL_TILT[axis])
        _paint(self.ball_mat, COL_HOT if part == "head" else COL_TILT[tilt] if tilt else COL_BALL)
        self.stem_color.rgb.setValue(*(COL_TILT[tilt] if tilt else COL_STEM))
        _paint(self.base_mat, COL_HOT if part == "ball" else COL_BASE)
        self._update_dial()

    def _update_dial(self):
        part = self._part()
        tilt = part[-1] if part in ("tilt_u", "tilt_v") else None
        if tilt is None or not self.s.planes:
            self.dial_sw.whichChild = _NONE
            return
        dr = self.drag
        if dr is not None:                          # the dial stays where the tilt started
            o, e1, e2 = dr["center"], dr["e1"], dr["e2"]
        else:
            p = self.s.planes[self.s.active]
            o, e1, e2 = p.origin, p.n, _swing(p, tilt)
        k, L = self.k, self.length
        self.dial_tf.translation.setValue(_vec3(o))
        self.dial_tf.rotation.setValue(_rotation(e1, e2, np.cross(e1, e2)))
        self.dial_tf.scaleFactor.setValue(k, k, k)
        if self._dial_len != L:
            pts, counts = dial_lines(L)
            _set_points(self.dial_coords, pts)
            self.dial_lines.numVertices.setNum(len(counts))
            self.dial_lines.numVertices.setValues(0, len(counts), counts)
            self._dial_len = L
        deg = dr.get("deg") if dr is not None and dr["moved"] else None
        if deg:
            _set_points(self.arc_coords, _arc_strip(L, deg, ARC_W * self.g))
            self.arc_color.rgb.setValue(*COL_TILT[tilt])
            self.arc_sw.whichChild = _ALL
        else:
            self.arc_sw.whichChild = _NONE
        self.dial_sw.whichChild = _ALL

    # -- projection --------------------------------------------------------
    def _vv(self):
        rm = self.viewer.getSoRenderManager()
        vp = rm.getViewportRegion()
        return rm.getCamera().getViewVolume(vp.getViewportAspectRatio())

    def _squeeze(self):
        """Coin shrinks the picture of a portrait viewport by width/height (ADJUST_CAMERA),
        which the view volume does not include."""
        vp = self.viewer.getSoRenderManager().getViewportRegion()
        return max(min(vp.getViewportAspectRatio(), 1.0), 1e-3)

    def to_screen(self, p, vv=None):
        """World point -> widget pixels (origin top left)."""
        vv = vv or self._vv()
        s = vv.projectToScreen(_vec3(p)).getValue()
        a = self._squeeze()
        return np.array(((0.5 + (s[0] - 0.5) * a) * self.widget.width(),
                         (1.0 - (0.5 + (s[1] - 0.5) * a)) * self.widget.height()))

    def ray(self, x, y, vv=None):
        """Widget pixel -> (point, unit direction) of the pick ray in world space."""
        vv = vv or self._vv()
        a = self._squeeze()
        nx = x / max(self.widget.width(), 1)
        ny = 1.0 - y / max(self.widget.height(), 1)
        p0, p1 = vv.projectPointToLine(coin.SbVec2f(0.5 + (nx - 0.5) / a, 0.5 + (ny - 0.5) / a))
        q0 = np.array(p0.getValue(), dtype=float)
        d = np.array(p1.getValue(), dtype=float) - q0
        return q0, geom.unit(d)

    def world_per_px(self, p, vv=None):
        vv = vv or self._vv()
        cam_right = np.array(self.view.getCameraOrientation().multVec(App.Vector(1, 0, 0)), dtype=float)
        L = 1.0
        a, b = self.to_screen(p, vv), self.to_screen(np.asarray(p) + cam_right * L, vv)
        px = float(np.linalg.norm(b - a))
        return L / px if px > 1e-9 else self.k

    def _on_render(self, _data, action):
        """Keep the handle a constant size on screen (computed in the frame being drawn),
        and tell the session when the zoom or the view direction changed (hatch spacing)."""
        if self._removed or not action.isOfType(coin.SoGLRenderAction.getClassTypeId()):
            return
        if not self.s.planes:
            return
        try:
            k = self.world_per_px(self.s.planes[self.s.active].origin)
            cam = tuple(round(c, 4) for c in self.view.getCameraOrientation().Q)
        except Exception:
            return
        changed = cam != self._cam
        self._cam = cam
        if k > 0 and abs(k - self.k) > 1e-3 * self.k:
            self.k = k
            self.refresh()
            changed = True
        if changed:
            self.s.on_scale_changed()

    def plane_px_per_mm(self, origin, u, v, vv=None):
        """2x2 map from millimetres along the plane's (u, v) at origin to screen pixels."""
        vv = vv or self._vv()
        L = 20.0 * self.world_per_px(origin, vv)        # about 20 px: local, yet well above noise
        o = self.to_screen(origin, vv)
        cu = (self.to_screen(np.asarray(origin) + L * np.asarray(u), vv) - o) / L
        cv = (self.to_screen(np.asarray(origin) + L * np.asarray(v), vv) - o) / L
        return np.column_stack((cu, cv))

    # -- hit testing ---------------------------------------------------------
    @staticmethod
    def _seg_dist(p, a, b):
        ab = b - a
        L2 = float(ab @ ab)
        t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, float((p - a) @ ab) / L2))
        return float(np.linalg.norm(p - (a + t * ab)))

    def head_point(self, plane=None, k=None):
        """World position of the ball on the stem."""
        p = plane or self.s.planes[self.s.active]
        k = self.world_per_px(p.origin) if k is None else k
        return p.origin + p.n * (self.length * k)

    def arrow_points(self, axis, sign, plane=None, k=None):
        """World base and tip of a tilt arrow ('u' or 'v' pair, sign +1 or -1)."""
        p = plane or self.s.planes[self.s.active]
        k = self.world_per_px(p.origin) if k is None else k
        h, d = self.head_point(p, k), sign * _swing(p, axis) * (self.g * k)
        return h + d * CONE_GAP, h + d * (CONE_GAP + CONE_LEN)

    def hit(self, x, y):
        if not self.visible or not self.s.planes:
            return None
        vv = self._vv()
        m = np.array((x, y), dtype=float)
        act = self.s.active
        p = self.s.planes[act]
        g = self.g
        k = self.world_per_px(p.origin, vv)
        o = self.to_screen(p.origin, vv)
        h = self.to_screen(self.head_point(p, k), vv)
        d_o, d_h = float(np.linalg.norm(m - o)), float(np.linalg.norm(m - h))
        if d_o <= BASE_R * g:                      # drawn on top of everything
            return ("ball", act)
        if d_h <= BALL_R * g:
            return ("head", act)
        best = None
        for axis in ("u", "v"):
            for sign in (1.0, -1.0):
                a, b = (self.to_screen(q, vv) for q in self.arrow_points(axis, sign, p, k))
                d = self._seg_dist(m, a, b)
                if d <= 0.5 * CONE_R * g + GRAB_PX and (best is None or d < best[0]):
                    best = (d, ("tilt_" + axis, act))
        if best:
            return best[1]
        if d_h <= BALL_R * g + GRAB_PX:
            return ("head", act)
        if d_o <= BASE_R * g + GRAB_PX:
            return ("ball", act)
        if self._seg_dist(m, o, h) <= PICK_PX:
            return ("head", act)
        for i, q in enumerate(self.s.planes):
            if i != act and np.linalg.norm(m - self.to_screen(q.origin, vv)) <= INACTIVE_R * g + GRAB_PX:
                return ("select", i)
        return None

    # -- drag math -----------------------------------------------------------
    def _axis_param(self, x, y, anchor, n):
        """t of the point anchor + t*n closest to the mouse ray (None when looking along n)."""
        q0, d = self.ray(x, y)
        w0 = anchor - q0
        b = float(n @ d)
        denom = 1.0 - b * b
        if denom < 1e-6:
            return None
        return (b * float(d @ w0) - float(n @ w0)) / denom

    def _plane_hit(self, x, y, point, normal):
        q0, d = self.ray(x, y)
        den = float(d @ normal)
        if abs(den) < 1e-9:
            return None
        return q0 + d * (float((point - q0) @ normal) / den)

    def _tilt_point(self, x, y):
        """Cursor on the dial's plane: (angle from the start normal toward the swing
        direction, distance from the centre in pixels), or None when the dial is
        seen edge-on."""
        dr = self.drag
        q0, d = self.ray(x, y)
        a = dr["axis_vec"]
        if abs(float(d @ a)) < 0.2:
            return None
        hp = self._plane_hit(x, y, dr["center"], a)
        if hp is None:
            return None
        w = hp - dr["center"]
        r = float(np.linalg.norm(w)) / max(self.world_per_px(dr["center"]), 1e-12)
        return math.atan2(float(w @ dr["e2"]), float(w @ dr["e1"])), r

    # -- Qt events -------------------------------------------------------------
    @staticmethod
    def _pos(ev):
        p = ev.position() if hasattr(ev, "position") else ev.pos()
        return float(p.x()), float(p.y())

    def handle_event(self, obj, ev):
        if self._removed:
            return False
        et = ev.type()
        if et in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride):
            return self._key(ev)
        if obj is not self.widget:      # mouse events: only the viewport's own
            return False
        if et == QtCore.QEvent.MouseButtonPress:
            self._swallow = False
            if self.field.isVisible() and not self.field.geometry().contains(ev.pos()):
                self.field.dismiss()
            if ev.button() != QtCore.Qt.LeftButton:
                return False
            x, y = self._pos(ev)
            h = self.hit(x, y)
            if h is None:
                return False
            self._start(h, x, y, ev.modifiers())
            return True
        if et == QtCore.QEvent.MouseButtonDblClick:
            x, y = self._pos(ev)
            return self.hit(x, y) is not None
        if self._swallow:               # FreeCAD would select on the release
            if et == QtCore.QEvent.MouseButtonRelease and ev.button() == QtCore.Qt.LeftButton:
                self._swallow = False
                return True
            if et == QtCore.QEvent.MouseMove:
                if ev.buttons() & QtCore.Qt.LeftButton:
                    return True
                self._swallow = False   # moving without the button: the release went elsewhere
        if et == QtCore.QEvent.MouseMove:
            x, y = self._pos(ev)
            if self.drag is not None:
                self._move(x, y, ev.modifiers())
                return True
            if not ev.buttons():
                self._set_hot(self.hit(x, y))
            return False
        if et == QtCore.QEvent.MouseButtonRelease and self.drag is not None:
            if ev.button() == QtCore.Qt.LeftButton:
                x, y = self._pos(ev)
                self._end(x, y)
                return True
        return False

    def _key(self, ev):
        """Keys during a drag. Escape cancels it. A number key starts typing in the
        value field (grey ball and arrows), which then keeps the keyboard until
        Enter or Escape."""
        dr = self.drag
        if dr is None:
            return False
        key, text = ev.key(), ev.text()
        plain = not (ev.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier
                                       | QtCore.Qt.MetaModifier))
        typing = (plain and dr["part"] != "ball"
                  and ((len(text) == 1 and text in TYPED_CHARS) or key == QtCore.Qt.Key_Backspace))
        if key != QtCore.Qt.Key_Escape and not typing:
            return False
        if ev.type() == QtCore.QEvent.ShortcutOverride:
            ev.accept()                 # deliver it as a key press, not as FreeCAD's shortcut
            return True
        if key == QtCore.Qt.Key_Escape:
            self._cancel()
        else:
            self._start_typing("" if key == QtCore.Qt.Key_Backspace else text)
        return True

    def _start_typing(self, text):
        dr = self.drag
        dr["typed"] = True              # the plane stops following the mouse
        if not self.field.hasFocus():
            kind = "offset" if dr["part"] == "head" else "angle"
            self.field.show_value(kind, "", *dr["xy"])
            self.field.setFocus()
        if text:
            self.field.insert(text)
        else:
            self.field.backspace()

    def _start(self, h, x, y, mods):
        part, idx = h
        if part == "select":
            self.s.set_active(idx)
            return
        p = self.s.planes[idx]
        self.drag = {"part": part, "x0": x, "y0": y, "xy": (x, y), "moved": False, "start": p.copy()}
        dr = self.drag
        if part == "head":
            dr["t0"] = self._axis_param(x, y, p.origin, p.n)
        elif part.startswith("tilt_"):
            axis = part[-1]
            dr.update(axis=axis, axis_vec=(p.u if axis == "u" else p.v).copy(), e1=p.n.copy(),
                      e2=_swing(p, axis).copy(), center=p.origin.copy())
            tp = self._tilt_point(x, y)
            dr["a0"] = tp[0] if tp else None
            if tp is None:                            # edge-on: drag along the ball's path on screen
                k = self.world_per_px(p.origin) * self.length
                q = lambda t: p.origin + k * (math.sin(t) * dr["e2"] + math.cos(t) * dr["e1"])
                sa, sb = self.to_screen(q(-0.05)), self.to_screen(q(0.05))
                tan = sb - sa
                L = float(np.linalg.norm(tan))
                dr["tan"] = (tan / L if L > 1e-6 else np.array((1.0, 0)), max(L / 0.1, 1e-3))
        self.hot = h
        self._update_look()
        self.s.drag_started()

    def _move(self, x, y, mods):
        dr = self.drag
        if dr.get("typed"):             # a typed value replaces the mouse
            return
        dr["xy"] = (x, y)
        if not dr["moved"] and math.hypot(x - dr["x0"], y - dr["y0"]) < CLICK_PX:
            return
        dr["moved"] = True
        p = self.s.planes[self.s.active]
        start = dr["start"]
        ctrl = bool(mods & QtCore.Qt.ControlModifier)
        shift = bool(mods & QtCore.Qt.ShiftModifier)
        part = dr["part"]
        if part == "head":
            if dr["t0"] is None:
                return
            t = self._axis_param(x, y, start.origin, start.n)
            if t is None:
                return
            delta = t - dr["t0"]
            snap = None if ctrl else self.s.inference_point(x, y, self.widget)
            if snap is not None:
                delta = float(start.n @ (snap - start.origin))
            elif shift:
                off = start.offset() + delta
                delta = round(off) - start.offset()
            p.origin = start.origin + start.n * delta
            self.field.show_value("offset", self.s.offset_text(p), x, y)
        elif part == "ball":
            snap = None if ctrl else self.s.inference_point(x, y, self.widget)
            if snap is not None:
                p.origin = snap
            else:
                hp = self._plane_hit(x, y, p.origin, p.n)
                if hp is None:
                    return
                p.origin = hp
        elif part.startswith("tilt_"):
            r = None
            if "tan" in dr:
                tan, px_per_rad = dr["tan"]
                total = float(np.dot((x - dr["x0"], y - dr["y0"]), tan)) / px_per_rad
            else:
                tp = self._tilt_point(x, y)
                if tp is None or dr["a0"] is None:
                    return
                total, r = tp[0] - dr["a0"], tp[1]
                total = (total + math.pi) % (2 * math.pi) - math.pi
            deg = math.degrees(total)
            if not ctrl:
                step = 15.0 if shift else dial_step(r, self.length) if r is not None else 1.0
                deg = round(deg / step) * step
            self._apply_rotation(deg)
            self.field.show_value("angle", "%.1f°" % deg, x, y)
        self.s.plane_changed(quick=True)

    def _apply_rotation(self, deg):
        dr = self.drag
        start = dr["start"]
        p = self.s.planes[self.s.active]
        # the normal turns toward e2 (where the ball goes) by deg
        axis = dr["axis_vec"]
        sign = -1.0 if dr["axis"] == "u" else 1.0     # u x n = -v, v x n = +u
        phi = math.radians(deg) * sign
        p.n = geom.unit(geom.rotate(start.n, axis, phi))
        p.u = geom.unit(geom.rotate(start.u, axis, phi))
        p.u = geom.unit(p.u - p.n * float(p.u @ p.n))
        p.origin = start.origin.copy()
        dr["deg"] = deg

    def _end(self, x, y):
        dr = self.drag
        self.drag = None
        if not dr["moved"] and not dr.get("typed"):
            self._update_look()
            if dr["part"] == "head":
                self.s.flip_active()
            self.s.drag_finished()
            return
        self.hot = self.hit(x, y)
        self._update_look()
        if dr["part"] == "head" or dr["part"].startswith("tilt_"):
            self._field_ctx = dr            # a typed value then replaces this drag
        self.s.drag_finished()

    def _cancel(self):
        """Escape during a drag: the plane goes back. The button is still down, so
        its release must not reach FreeCAD, which would select what is under the
        cursor and so place the plane there."""
        dr = self.drag
        self.drag = None
        self._swallow = True
        self.s.planes[self.s.active].assign(dr["start"])
        self.field.dismiss()
        self._update_look()
        self.s.drag_finished()

    def _field_enter(self, kind, val):
        """A typed value: an offset, or an angle from where the last tilt drag started.
        With the mouse button still down, it ends the drag's effect; the release
        then only finishes it."""
        p = self.s.planes[self.s.active]
        live = self.drag
        if kind == "offset":
            p.set_offset(val)
        elif kind == "angle":
            ctx = live or getattr(self, "_field_ctx", None)
            if ctx is None or not ctx["part"].startswith("tilt_"):
                return
            self.drag = ctx
            self._apply_rotation(val)
            self.drag = live
        if live is not None:
            self.s.plane_changed(quick=False)
            return
        self.s.drag_finished()

    def _field_escape(self):
        if self.drag is not None:
            self._cancel()
