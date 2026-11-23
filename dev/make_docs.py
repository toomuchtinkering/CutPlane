# SPDX-License-Identifier: LGPL-2.1-or-later
"""Raw pictures for the README (run with tests/run_gui.sh dev/make_docs.py, or dev/make_docs.sh).

Builds a demo assembly from Part primitives (a pillow block: housing, two ball
bearings, a stepped shaft, a V-belt pulley, bolts and nuts), drives CutPlane the
way a user would, and writes raw screenshots and animation frames to
dev/out/docs_raw/ with meta.json (crop boxes, label anchors, cursor positions).
dev/compose_docs.py turns them into docs/images/. Runs with the default CutPlane
settings and puts the user's back at the end.
"""
import json
import math
import os
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, "dev", "out", "docs_raw")
import freecad  # noqa: E402
if os.path.join(ROOT, "freecad") not in freecad.__path__:
    freecad.__path__.append(os.path.join(ROOT, "freecad"))

ONLY = set(a for a in os.environ.get("DOCS_ONLY", "").split(",") if a)   # e.g. DOCS_ONLY=hero,tilt
meta = {}
_restore = []


def out(*a):
    os.write(1, (" ".join(str(x) for x in a) + "\n").encode())


def pump(n=6):
    from PySide import QtGui
    for _ in range(n):
        QtGui.QApplication.processEvents()
        time.sleep(0.01)


# --------------------------------------------------------------------------------------
# the demo assembly
# --------------------------------------------------------------------------------------
AX = 40.0                       # shaft axis: along x, at y = 0, z = AX


def build(doc):
    import FreeCAD as App
    import Part
    V = App.Vector
    X = V(1, 0, 0)

    def cyl_x(r, x0, length):
        return Part.makeCylinder(r, length, V(x0, 0, AX), X)

    def ring(r1, r2, x0, w):
        return cyl_x(r2, x0, w).cut(cyl_x(r1, x0, w))

    def feature(name, shape, color, parent=None):
        o = doc.addObject("Part::Feature", name)
        o.Shape = shape
        if parent is not None:
            parent.addObject(o)
        o.ViewObject.ShapeAppearance = (App.Material(DiffuseColor=color),)
        return o

    # housing: base plate, pedestal with a rounded top, bore, two bearing seats, bolt holes
    base = Part.makeBox(124, 62, 12, V(-62, -31, 0))
    ped = Part.makeBox(70, 62, AX, V(-35, -31, 0))
    top = cyl_x(31, -35, 70)
    body = base.fuse([ped, top])
    cuts = [cyl_x(14, -40, 80), cyl_x(23.5, 21, 14), cyl_x(23.5, -35, 14)]
    cuts += [Part.makeCylinder(6.5, 20, V(x, 0, -4)) for x in (-50, 50)]
    cuts += [Part.makeCylinder(6.5, 20, V(0, 0, 69)),      # spot face and oil hole on top
             Part.makeCylinder(3, 40, V(0, 0, AX))]
    housing = feature("Housing", body.cut(cuts).removeSplitter(), (0.55, 0.68, 0.86))

    # ball bearing 6204 (d 20, D 47, B 14) in an App::Part; the second one is a link to it
    bearing = doc.addObject("App::Part", "Bearing")
    xb, w, pitch, rb = 21.0, 14.0, 16.75, 3.95
    groove = Part.makeTorus(pitch, rb + 0.1, V(xb + w / 2, 0, AX), X)
    feature("OuterRing", ring(20, 23.5, xb, w).cut(groove), (0.80, 0.81, 0.84), bearing)
    feature("InnerRing", ring(10, 13.5, xb, w).cut(groove), (0.80, 0.81, 0.84), bearing)
    balls = [Part.makeSphere(rb, V(xb + w / 2, pitch * math.sin(a), AX + pitch * math.cos(a)))
             for a in (k * math.pi / 4 for k in range(8))]
    feature("Balls", Part.makeCompound(balls), (0.45, 0.47, 0.52), bearing)
    bearing2 = doc.addObject("App::Link", "Bearing001")
    bearing2.LinkedObject = bearing
    bearing2.Placement.Base = V(-56, 0, 0)

    # stepped shaft: d 20, with a d 25 shoulder between the bearings
    shaft = cyl_x(10, -50, 148).fuse(cyl_x(12.5, -21, 42)).removeSplitter()
    feature("Shaft", shaft, (0.95, 0.62, 0.25))

    # V-belt pulley, revolved from its half section (x, r)
    prof = [(55, 10), (80, 10), (80, 20), (71, 20), (71, 28), (76, 28), (76, 40), (73.5, 40),
            (69.5, 30), (65.5, 30), (61.5, 40), (59, 40), (59, 28), (64, 28), (64, 20), (55, 20)]
    prof = [(x + 13, r) for x, r in prof]                # clear of the base and the bolt head
    wire = Part.makePolygon([V(x, 0, AX + r) for x, r in prof] + [V(prof[0][0], 0, AX + prof[0][1])])
    pulley = Part.Face(wire).revolve(V(0, 0, AX), X, 360)
    feature("Pulley", pulley, (0.42, 0.75, 0.42))

    # M12 bolts with hex heads, and nuts under the base; the second set are links
    def hexagon(af, z0, h):
        r = af / math.sqrt(3)
        pts = [V(r * math.cos(k * math.pi / 3), r * math.sin(k * math.pi / 3), z0) for k in range(7)]
        return Part.Face(Part.makePolygon(pts)).extrude(V(0, 0, h))
    bolt = hexagon(18, 12, 8).fuse(Part.makeCylinder(6, 30, V(0, 0, -14))).removeSplitter()
    nut = hexagon(18, -10, 10).cut(Part.makeCylinder(6, 12, V(0, 0, -11)))
    b1 = feature("Bolt", bolt, (0.36, 0.37, 0.42))
    n1 = feature("Nut", nut, (0.62, 0.62, 0.66))
    b1.Placement.Base = n1.Placement.Base = V(50, 0, 0)
    for o, name in ((b1, "Bolt001"), (n1, "Nut001")):
        lk = doc.addObject("App::Link", name)
        lk.LinkedObject = o
        lk.Placement.Base = V(-50, 0, 0)          # a link's placement replaces the object's
    doc.recompute()
    return {"housing": housing, "pulley": doc.getObject("Pulley"), "shaft": doc.getObject("Shaft"),
            "bolt": b1}


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def orientation(view_dir, up=(0, 0, 1)):
    """Camera rotation looking along view_dir with up roughly up."""
    import numpy as np
    from pivy import coin
    d = np.asarray(view_dir, float)
    d /= np.linalg.norm(d)
    z = -d
    y = np.asarray(up, float) - (np.asarray(up, float) @ z) * z
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    m = np.column_stack((x, y, z))
    t = np.trace(m)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = ((m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s)
    else:
        i = int(np.argmax(np.diag(m)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(1.0 + m[i, i] - m[j, j] - m[k, k]) * 2
        qv = [0.0, 0.0, 0.0]
        qv[i] = 0.25 * s
        qv[j] = (m[j, i] + m[i, j]) / s
        qv[k] = (m[k, i] + m[i, k]) / s
        q = (qv[0], qv[1], qv[2], (m[k, j] - m[j, k]) / s)
    return coin.SbRotation(*q)


def main():
    import numpy as np
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    from PySide import QtCore, QtGui
    from freecad.CutPlane import commands, prefs
    from freecad.CutPlane import session as S
    from freecad.CutPlane import gizmo as GZ
    from freecad.CutPlane.session import Options

    # fixed settings (the defaults); the user's come back at the end
    grp = prefs.params()
    TEST = {"Int": {prefs.HANDLE_SIZE: prefs.HANDLE_DEFAULT, prefs.BALL_SIZE: prefs.BALL_DEFAULT,
                    prefs.HATCH_PX: prefs.HATCH_PX_DEFAULT, prefs.OUTLINE_WIDTH: prefs.OUTLINE_DEFAULT},
            "Float": {}, "Bool": {}, "String": {}}
    for name, (default, typ) in Options.FIELDS.items():
        TEST[{bool: "Bool", float: "Float", str: "String"}[typ]][name] = default
    saved = {(kind, n): (n in getattr(grp, "Get%ss" % kind)(), getattr(grp, "Get" + kind)(n))
             for kind, values in TEST.items() for n in values}

    def restore_user():
        for (kind, n), (had, v) in saved.items():
            getattr(grp, "Set" + kind)(n, v) if had else getattr(grp, "Rem" + kind)(n)
    _restore.append(restore_user)
    # no navigation cube or corner axes in the pictures (the user's settings come back too)
    vgrp = App.ParamGet("User parameter:BaseApp/Preferences/View")
    vsaved = {n: (n in vgrp.GetBools(), vgrp.GetBool(n)) for n in ("ShowNaviCube", "CornerCoordSystem")}

    def restore_view():
        for n, (had, v) in vsaved.items():
            vgrp.SetBool(n, v) if had else vgrp.RemBool(n)
    _restore.append(restore_view)
    for n in vsaved:
        vgrp.SetBool(n, False)
    for kind, values in TEST.items():
        for n, v in values.items():
            getattr(grp, "Set" + kind)(n, v)

    commands.register()
    if os.path.isdir(RAW) and not ONLY:
        shutil.rmtree(RAW)
    os.makedirs(RAW, exist_ok=True)
    if ONLY and os.path.exists(os.path.join(RAW, "meta.json")):
        meta.update(json.load(open(os.path.join(RAW, "meta.json"))))
    mw = Gui.getMainWindow()

    doc = App.newDocument("PillowBlock")
    parts = build(doc)
    pump(10)
    view = Gui.ActiveDocument.ActiveView
    w = view.graphicsView()
    while w is not None and not isinstance(w, QtGui.QMdiSubWindow):
        w = w.parentWidget()
    if w is not None:
        mw.findChild(QtGui.QMdiArea).setActiveSubWindow(w)
        pump(10)

    docks = {d.objectName() or d.windowTitle(): d for d in mw.findChildren(QtGui.QDockWidget)}

    def only_docks(*keep):
        for name, d in docks.items():
            d.setVisible(name in keep)
        s = S.Session.current
        if s is not None and s.panel is not None:
            s.panel.setVisible("panel" in keep)
        pump(10)

    def camera(view_dir, up=(0, 0, 1), zoom=1.0):
        view.getCameraNode().orientation.setValue(orientation(view_dir, up))
        pump(4)
        view.fitAll()
        pump(4)
        cam = view.getCameraNode()
        if zoom != 1.0 and hasattr(cam, "height"):
            cam.height.setValue(cam.height.getValue() / zoom)
        pump(6)

    def center_on(point, zoom=1.0):
        """Pan the camera so that point is in the middle of the view, then zoom."""
        cam = view.getCameraNode()
        d = np.array(view.getViewDirection(), float)
        pos = np.array(cam.position.getValue(), float)
        q = np.asarray(point, float)
        cam.position.setValue(*(q - d * float((q - pos) @ d)))
        if zoom != 1.0 and hasattr(cam, "height"):
            cam.height.setValue(cam.height.getValue() / zoom)
        pump(6)

    def frame(name=None):
        pump(3)
        s = S.Session.current
        if s is not None:
            s._update_caps()
        pump(3)
        img = view.getViewer().grabFramebuffer()
        if name:
            img.save(os.path.join(RAW, name))
        return img

    def screen_box(points, margin=30):
        s = S.Session.current
        P = np.array([s.gizmo.to_screen(np.asarray(p, float)) for p in points])
        x0, y0 = P.min(axis=0) - margin
        x1, y1 = P.max(axis=0) + margin
        return [int(x0), int(y0), int(x1), int(y1)]

    def model_corners():
        s = S.Session.current
        lo, hi = s.lo, s.hi
        return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]

    def pick(obj, face_test, point):
        """A click on a face of obj at point (world): what the selection observer sees."""
        f = next(i for i, f in enumerate(obj.Shape.Faces) if face_test(f))
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, obj.Name, "Face%d" % (f + 1), *point)
        pump(6)

    def is_cyl(r, axis=(1, 0, 0)):
        def t(f):
            srf = f.Surface
            return (srf.__class__.__name__ == "Cylinder" and abs(srf.Radius - r) < 1e-6
                    and abs(abs(np.dot(np.array(tuple(srf.Axis)), axis)) - 1) < 1e-9)
        return t

    def want(name):
        return not ONLY or name in ONLY

    # ---------------------------------------------------------------------------------
    # hero: a click on the shaft cuts through its axis
    # ---------------------------------------------------------------------------------
    only_docks("panel")
    hero_dir = (-1.0, 1.8, -0.9)
    camera(hero_dir)
    Gui.Selection.clearSelection()
    s = S.Session.toggle()
    pump(6)
    toward = -np.array(hero_dir) / np.linalg.norm(hero_dir)
    tp = np.array((0, toward[1], toward[2]))
    tp /= np.linalg.norm(tp)
    pt = np.array((-43.0, 0, AX)) + 10.0 * tp            # on the shaft's left end, facing the camera
    pick(parts["shaft"], is_cyl(10), tuple(pt))
    s = S.Session.current
    camera(hero_dir, zoom=1.15)
    out("  docks:", sorted(docks))
    out("  hero plane n", s.planes[0].n.round(3), "origin", s.planes[0].origin.round(2), "status:", s.status)
    out("  parts:", sorted(info.label for info in s.parts.values()))
    g = s.gizmo
    vp = g.widget
    L, NB = QtCore.Qt.LeftButton, QtCore.Qt.NoButton

    def send(kind, x, y, buttons, button=NB, mods=QtCore.Qt.NoModifier):
        pos = QtCore.QPointF(float(x), float(y))
        ev = QtGui.QMouseEvent(kind, pos, vp.mapToGlobal(pos.toPoint()), button, buttons, mods)
        QtGui.QApplication.sendEvent(vp, ev)

    def grab_in_dock(widget, name):
        """A piece of the panel as it looks, background included (the style paints the dock)."""
        dock = S.Session.current.panel
        tl = widget.mapTo(dock, QtCore.QPoint(0, 0))
        dock.grab(QtCore.QRect(tl, widget.size())).save(os.path.join(RAW, name))

    def panel_group(title):
        return next(b for b in S.Session.current.panel.findChildren(QtGui.QGroupBox) if b.title() == title)

    def record(name, cursor, pressed):
        """One animation frame and where the mouse was."""
        d = os.path.join(RAW, name)
        os.makedirs(d, exist_ok=True)
        n = len(meta[name]["frames"])
        frame(os.path.join(name, "%03d.png" % n))
        meta[name]["frames"].append([float(cursor[0]), float(cursor[1]), bool(pressed)])

    hero_box = screen_box(model_corners() + [g.head_point()], 20)
    if want("hero"):
        frame("hero.png")
        meta["hero"] = {"crop": hero_box}

    # ---------------------------------------------------------------------------------
    # the whole window, and the panel on its own
    # ---------------------------------------------------------------------------------
    if want("window"):
        only_docks("panel", *[n for n in docks if n.lower() in ("combo view", "tree view", "model",
                                                                 "property view")])
        camera(hero_dir, zoom=1.1)
        frame()
        mw.grab().save(os.path.join(RAW, "window.png"))
        s.panel.grab().save(os.path.join(RAW, "panel.png"))
        only_docks("panel")
        camera(hero_dir, zoom=1.15)

    # ---------------------------------------------------------------------------------
    # the animation: drag the grey ball through the assembly (Shift: whole millimetres)
    # ---------------------------------------------------------------------------------
    if want("move"):
        shutil.rmtree(os.path.join(RAW, "move"), ignore_errors=True)
        meta["move"] = {"crop": hero_box, "frames": []}
        p = s.planes[0]
        h0 = g.head_point()
        hs = g.to_screen(h0)
        start = hs + np.array((-90.0, 60.0))
        for t in np.linspace(0, 1, 6):
            c = start + (hs - start) * t
            send(QtCore.QEvent.MouseMove, c[0], c[1], NB)
            record("move", c, False)
        send(QtCore.QEvent.MouseButtonPress, hs[0], hs[1], L, L)
        record("move", hs, True)
        path = np.concatenate((np.linspace(0, 24, 16), np.linspace(24, -24, 30), np.linspace(-24, 0, 16)))
        for d in path[1:]:
            c = g.to_screen(h0 + p.n * d)                 # the ball moves with the plane
            send(QtCore.QEvent.MouseMove, c[0], c[1], L, NB, QtCore.Qt.ShiftModifier)
            record("move", c, True)
        send(QtCore.QEvent.MouseButtonRelease, c[0], c[1], NB, L)
        for _ in range(6):
            record("move", c, False)
        out("  move: offset back to %.2f" % p.offset())
        send(QtCore.QEvent.MouseMove, 5, 5, NB)
        s.reset_active()
        pump()

    # ---------------------------------------------------------------------------------
    # several planes: a second, horizontal plane through the axis
    # ---------------------------------------------------------------------------------
    if want("two_planes"):
        s.set_reference(s._default_ref((), "top"), add=True)
        p2 = s.planes[1]
        p2.set_offset(AX - p2.origin[2] if p2.n[2] > 0 else p2.origin[2] - AX)
        s.plane_changed()
        frame("two_planes.png")
        meta["two_planes"] = {"crop": screen_box(model_corners() + [g.head_point()], 20)}
        out("  two planes:", s.status, "p2 n", p2.n.round(3), "o", p2.origin.round(2))
        s.remove_plane()
        s.set_active(0)
        pump()

    # ---------------------------------------------------------------------------------
    # parts kept whole: shaft, balls, bolts and nuts, as a drawing would show them
    # ---------------------------------------------------------------------------------
    if want("keep_whole"):
        s.pick_mode = "exclude"
        s.panel.mode.setCurrentIndex(2)
        for name in ("Shaft", "Balls", "Bolt", "Bolt001", "Nut", "Nut001"):
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, name, "Face1")
            pump(4)
        frame("keep_whole.png")
        meta["keep_whole"] = {"crop": hero_box}
        out("  keep whole:", s.exclude, "|", s.panel.exclude_label.text())
        grab_in_dock(panel_group("Parts"), "panel_parts.png")
        s.clear_list("exclude")
        s.pick_mode = "align"
        s.panel.mode.setCurrentIndex(0)
        pump()

    # ---------------------------------------------------------------------------------
    # an overlap: the pulley bored 2 mm undersize
    # ---------------------------------------------------------------------------------
    if want("overlap"):
        pulley = parts["pulley"]
        good = pulley.Shape.copy()
        X = App.Vector(1, 0, 0)
        skin = Part.makeCylinder(10, 25, App.Vector(68, 0, AX), X).cut(
            Part.makeCylinder(9, 25, App.Vector(68, 0, AX), X))
        pulley.Shape = good.fuse(skin).removeSplitter()
        doc.recompute()
        pump(10)
        center_on((78.0, 0, AX), 2.0)
        frame("overlap.png")
        meta["overlap"] = {"status": s.status}
        grab_in_dock(panel_group("Display"), "panel_display.png")
        grab_in_dock(s.panel.status, "panel_status.png")
        out("  overlap:", s.status)
        pulley.Shape = good
        doc.recompute()
        pump(10)
        camera(hero_dir, zoom=1.15)

    # ---------------------------------------------------------------------------------
    # placing the plane on a flat face: the pedestal's front, dragged 20 mm in
    # ---------------------------------------------------------------------------------
    if want("place_face"):
        pick(parts["housing"], lambda f: f.Surface.__class__.__name__ == "Plane"
             and abs(f.normalAt(0, 0).y + 1) < 1e-9 and f.BoundBox.ZMax > 30, (0, -31, 25))
        s.planes[s.active].set_offset(-20)
        s.plane_changed()
        camera(hero_dir, zoom=1.15)
        frame("place_face.png")
        out("  face plane n", s.planes[s.active].n.round(3), "o", s.planes[s.active].origin.round(2), s.status)
        pick(parts["shaft"], is_cyl(10), tuple(pt))       # back to the plane through the axis

    # ---------------------------------------------------------------------------------
    # the handle, close up, seen from the side so the stem shows its length
    # ---------------------------------------------------------------------------------
    side_dir = (-1.8, 1.0, -0.9)
    grp.SetInt(prefs.HANDLE_SIZE, 230)           # the handle keeps its size on screen: make it bigger
    grp.SetInt(prefs.BALL_SIZE, 44)
    pump(4)
    camera(side_dir)
    p = s.planes[s.active]
    center_on((p.origin + g.head_point()) / 2 + np.array((0, 0, 4.0)), 2.6)
    out("  handle plane n", p.n.round(3), "origin", p.origin.round(2))

    def handle_points():
        pts = {"head": g.head_point(), "base": p.origin.copy(),
               "stem": (p.origin + g.head_point()) / 2}
        for axis, tag in (("u", "red"), ("v", "green")):
            for sign in (1.0, -1.0):
                a_, b_ = g.arrow_points(axis, sign)
                pts["%s%+d" % (tag, sign)] = b_
        return {k: [float(x) for x in g.to_screen(v)] for k, v in pts.items()}

    if want("handle"):
        frame("handle.png")
        meta["handle"] = {"points": handle_points()}
        a_, b_ = g.arrow_points("u", 1.0)
        c = g.to_screen((a_ + b_) / 2)
        send(QtCore.QEvent.MouseMove, c[0], c[1], NB)
        frame("handle_dial.png")
        dp = handle_points()
        R = GZ.DIAL * g.length * g.k                       # the dial's scale, in the start frame
        ang = math.radians(-40)
        ring = p.origin + R * (math.cos(ang) * p.n + math.sin(ang) * p.v)
        dp["dial"] = [float(x) for x in g.to_screen(ring)]
        dp["cursor"] = [float(c[0]), float(c[1])]
        meta["handle_dial"] = {"cursor": dp["cursor"], "points": dp}
        send(QtCore.QEvent.MouseMove, 5, 5, NB)
        pump()

    # ---------------------------------------------------------------------------------
    # the animation: tilt with the red arrows, the dial and the angle arc
    # ---------------------------------------------------------------------------------
    if want("tilt"):
        shutil.rmtree(os.path.join(RAW, "tilt"), ignore_errors=True)
        meta["tilt"] = {"frames": []}
        a_, b_ = g.arrow_points("u", 1.0)
        mid = (a_ + b_) / 2
        c = g.to_screen(mid)
        start = c + np.array((80.0, 70.0))
        for t in np.linspace(0, 1, 6):
            q = start + (c - start) * t
            send(QtCore.QEvent.MouseMove, q[0], q[1], NB)
            record("tilt", q, False)
        for _ in range(4):
            record("tilt", c, False)
        send(QtCore.QEvent.MouseButtonPress, c[0], c[1], L, L)
        o3 = p.origin.copy()
        r3 = mid - o3
        ax = p.u.copy()
        for t in np.linspace(0, 1, 28)[1:]:
            ang = -38 * math.sin(t * math.pi / 2)
            q = g.to_screen(o3 + np.asarray(App.Rotation(App.Vector(*ax), ang).multVec(App.Vector(*r3))))
            send(QtCore.QEvent.MouseMove, q[0], q[1], L, NB)
            record("tilt", q, True)
        for _ in range(5):
            record("tilt", q, True)
        send(QtCore.QEvent.MouseButtonRelease, q[0], q[1], NB, L)
        for _ in range(4):
            record("tilt", q, False)
        out("  tilt: %.1f deg" % p.tilt())
        send(QtCore.QEvent.MouseMove, 5, 5, NB)
        s.reset_active()
        p = s.planes[s.active]
        pump()

    grp.SetInt(prefs.HANDLE_SIZE, prefs.HANDLE_DEFAULT)
    grp.SetInt(prefs.BALL_SIZE, prefs.BALL_DEFAULT)
    pump(4)

    # ---------------------------------------------------------------------------------
    # placing the plane: a cylinder from above, a circle edge, a sketch
    # ---------------------------------------------------------------------------------
    if want("place_cylinder"):
        top_dir = (-0.7, 1.0, -1.7)
        camera(top_dir)
        tw = -np.array(top_dir) / np.linalg.norm(top_dir)
        tq = np.array((0, tw[1], tw[2]))
        tq /= np.linalg.norm(tq)
        pick(parts["pulley"], is_cyl(40), tuple(np.array((76.0, 0, AX)) + 40.0 * tq))
        camera(top_dir, zoom=1.1)
        frame("place_cylinder.png")
        out("  cylinder plane n", s.planes[s.active].n.round(3), s.status)

    if want("place_circle"):
        circ_dir = (-1.8, 1.0, -1.1)
        camera(circ_dir)
        hous = parts["housing"]
        e = next(i for i, ed in enumerate(hous.Shape.Edges)
                 if ed.Curve.__class__.__name__ == "Circle" and abs(ed.Curve.Radius - 3) < 1e-6
                 and abs(ed.Curve.Center.z - 69) < 1e-6)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, hous.Name, "Edge%d" % (e + 1), 0, -3, 69)
        pump(6)
        camera(circ_dir, zoom=1.1)
        frame("place_circle.png")
        out("  circle plane n", s.planes[s.active].n.round(3), s.status)

    if want("place_sketch"):
        camera(hero_dir)
        sk = doc.addObject("Sketcher::SketchObject", "Sketch")
        sk.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 0, 1), 30)
                                     .multiply(App.Rotation(App.Vector(1, 0, 0), 90)))
        corners = [(-75, -20), (105, -20), (105, 90), (-75, 90)]
        for k in range(4):
            (x0, y0), (x1, y1) = corners[k], corners[(k + 1) % 4]
            sk.addGeometry(Part.LineSegment(App.Vector(x0, y0, 0), App.Vector(x1, y1, 0)))
        doc.recompute()
        pump(6)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(doc.Name, sk.Name)             # the sketch, picked in the tree
        pump(6)
        camera(hero_dir, zoom=1.15)
        frame("place_sketch.png")
        out("  sketch plane n", s.planes[s.active].n.round(3), s.status)
        doc.removeObject(sk.Name)
        doc.recompute()
        pump(6)

    # ---------------------------------------------------------------------------------
    # Edit > Preferences > CutPlane
    # ---------------------------------------------------------------------------------
    if want("prefs"):
        def grab_prefs():
            try:
                dlg = next(w for w in QtGui.QApplication.topLevelWidgets()
                           if w.isVisible() and isinstance(w, QtGui.QDialog))
                pump(4)
                dlg.grab().save(os.path.join(RAW, "prefs.png"))
                dlg.reject()
            except Exception as ex:
                out("  prefs grab failed:", ex)
        QtCore.QTimer.singleShot(2500, grab_prefs)
        Gui.showPreferences("CutPlane", 0)
        pump()
    out("RAW DONE")


try:
    main()
except Exception:
    out(traceback.format_exc())
    out("RAW FAILED")
finally:
    for fn in _restore:
        try:
            fn()
        except Exception:
            out(traceback.format_exc())
    try:
        json.dump(meta, open(os.path.join(RAW, "meta.json"), "w"), indent=1)
    except Exception:
        pass
    os._exit(0)
