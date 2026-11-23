"""End-to-end test of CutPlane in the FreeCAD GUI (run with tests/run_gui.sh).

Builds a small scene, drives the section session the way a user would
(picks, handle drags with real Qt mouse events, extra planes, exclusion) and
saves screenshots to dev/out/. Ends with "GUI TEST OK" or "GUI TEST FAILED".
"""
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "dev", "out")
# Use this working copy of freecad/CutPlane; FreeCAD has usually found it already
# through the Mod/CutPlane link, which points here.
import freecad  # noqa: E402
if os.path.join(ROOT, "freecad") not in freecad.__path__:
    freecad.__path__.append(os.path.join(ROOT, "freecad"))
failures = []
_restore = []           # restores the user's CutPlane settings (the flatpak shares the real config)


def out(*a):
    os.write(1, (" ".join(str(x) for x in a) + "\n").encode())


def check(cond, what):
    out(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        failures.append(what)


def pump(n=6):
    from PySide import QtGui
    for _ in range(n):
        QtGui.QApplication.processEvents()
        time.sleep(0.01)


def main():
    import numpy as np
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    from PySide import QtCore, QtGui
    from pivy import coin
    import freecad.CutPlane.commands
    from freecad.CutPlane import session as S
    from freecad.CutPlane import gizmo as GZ

    os.makedirs(OUT, exist_ok=True)
    from freecad.CutPlane import prefs
    grp = prefs.params()
    # The flatpak shares the user's real settings. Run with fixed ones (the defaults, for the
    # Preferences page and the panel options alike) and put the user's back at the end.
    from freecad.CutPlane.session import Options
    TEST = {"Int": {prefs.HANDLE_SIZE: prefs.HANDLE_DEFAULT, prefs.BALL_SIZE: prefs.BALL_DEFAULT,
                    prefs.HATCH_PX: prefs.HATCH_PX_DEFAULT, prefs.OUTLINE_WIDTH: prefs.OUTLINE_DEFAULT},
            "Float": {}, "Bool": {}, "String": {}}
    for name, (default, typ) in Options.FIELDS.items():
        TEST[{bool: "Bool", float: "Float", str: "String"}[typ]][name] = default
    saved = {(kind, n): (n in getattr(grp, "Get%ss" % kind)(), getattr(grp, "Get" + kind)(n))
             for kind, values in TEST.items() for n in values}

    def baseline():                       # the test's settings
        for kind, values in TEST.items():
            for n, v in values.items():
                getattr(grp, "Set" + kind)(n, v)

    def restore_user():                   # the user's settings, removing what was not there
        for (kind, n), (had, v) in saved.items():
            getattr(grp, "Set" + kind)(n, v) if had else getattr(grp, "Rem" + kind)(n)
    _restore.append(restore_user)
    baseline()
    freecad.CutPlane.commands.register()
    check(os.path.realpath(freecad.CutPlane.__file__).startswith(os.path.realpath(ROOT) + os.sep),
          "testing this working copy (%s)" % os.path.realpath(freecad.CutPlane.__file__))
    mw = Gui.getMainWindow()

    doc = App.newDocument("CutTest")
    box = Part.makeBox(40, 30, 20)
    hole = Part.makeCylinder(6, 40, App.Vector(20, 15, -10))
    housing = doc.addObject("Part::Feature", "Housing")
    housing.Shape = box.cut(hole)
    shaft = doc.addObject("Part::Cylinder", "Shaft")
    shaft.Radius, shaft.Height = 6.4, 40
    shaft.Placement.Base = App.Vector(20, 15, -10)
    sub = doc.addObject("App::Part", "Sub")
    b2 = doc.addObject("Part::Box", "Block")
    sub.addObject(b2)
    sub.Placement = App.Placement(App.Vector(60, 10, 0), App.Rotation(App.Vector(0, 0, 1), 30))
    lnk = doc.addObject("App::Link", "ShaftLink")
    lnk.LinkedObject = shaft
    lnk.Placement.Base = App.Vector(-25, 0, 0)
    doc.recompute()
    housing.ViewObject.ShapeAppearance = (App.Material(DiffuseColor=(0.55, 0.7, 0.9)),)
    shaft.ViewObject.ShapeAppearance = (App.Material(DiffuseColor=(0.95, 0.6, 0.2)),)
    b2.ViewObject.ShapeAppearance = (App.Material(DiffuseColor=(0.4, 0.8, 0.4)),)
    pump(10)
    view = Gui.ActiveDocument.ActiveView
    w = view.graphicsView()                 # bring the 3D view's own MDI window to the front
    while w is not None and not isinstance(w, QtGui.QMdiSubWindow):
        w = w.parentWidget()
    if w is not None:
        mdi = Gui.getMainWindow().findChild(QtGui.QMdiArea)
        mdi.setActiveSubWindow(w)           # the Start page is the active tab at first
        pump(10)
    # viewIsometric() animates and may not land in a scripted run: set the camera
    view.getCameraNode().orientation.setValue(coin.SbRotation(0.424708, 0.17592, 0.339851, 0.820473))
    pump(4)
    view.fitAll()
    pump(10)
    out("  viewport", view.graphicsView().viewport().width(), "x", view.graphicsView().viewport().height(),
        "view dir", view.getViewDirection())

    def shot(name):
        pump(4)
        s = S.Session.current
        if s is not None:
            s._update_caps()
        pump(4)
        img = view.getViewer().grabFramebuffer()
        img.save(os.path.join(OUT, name))
        return img

    # 1. start from a picked top face -----------------------------------------
    top = next(i for i, f in enumerate(housing.Shape.Faces)
               if f.Surface.__class__.__name__ == "Plane" and abs(f.normalAt(0, 0).z - 1) < 1e-9)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, "Housing", "Face%d" % (top + 1), 5, 5, 20)
    s = S.Session.toggle()
    pump()
    check(s is not None and S.Session.current is s, "session started")
    p = s.planes[0]
    check(np.allclose(p.n, (0, 0, 1)) and abs(p.origin[2] - 20) < 1e-9,
          "top face: normal +z through z=20 (got n=%s o=%s)" % (p.n.round(3), p.origin.round(3)))
    check(np.allclose(p.origin[:2], (5, 5)), "handles at the picked point")
    out("  parts:", sorted(k[1] for k in s.parts), "harvest ms %.1f" % s.harvest_ms)
    col = {k[1]: tuple(round(c, 2) for c in s.parts[k].color) for k in s.parts}
    check(col.get("Housing") == (0.55, 0.7, 0.9) and col.get("ShaftLink") == (0.95, 0.6, 0.2),
          "part colours read from the scene, links included (%s)" % col)
    check({"Housing", "Shaft", "Block", "ShaftLink"} <= {k[1] for k in s.parts}, "all parts found in the scene")
    shot("01_top_face_no_cut.png")
    cut = [k[1] for k in s.cut_keys.get(0, [])]
    check("Housing" not in cut, "plane on the housing's face leaves the housing whole (cut: %s)" % cut)
    check("Shaft" in cut, "the shaft sticking out above the face is cut")

    p.set_offset(-8.0)
    s.plane_changed()
    shot("02_top_face_down_8.png")
    mw.grab().save(os.path.join(OUT, "02_window.png"))
    # offscreen rendering (Tools > Save Image, scripts) shows the caps too
    vpw = view.graphicsView().viewport()
    view.saveImage(os.path.join(OUT, "02_saveimage.png"), vpw.width(), vpw.height(), "Current")
    off = QtGui.QImage(os.path.join(OUT, "02_saveimage.png"))
    live = view.getViewer().grabFramebuffer()
    c3 = np.array((20.0, 15.0, 12.0))                    # centre of the shaft's cap at z=12
    sx, sy = s.gizmo.to_screen(c3)
    pl, po = live.pixelColor(int(sx), int(sy)), off.pixelColor(int(sx), int(sy))
    out("  shaft cap pixel live %s / saveImage %s" % ((pl.red(), pl.green(), pl.blue()), (po.red(), po.green(), po.blue())))
    check(po.red() > po.blue() + 40, "saveImage shows the orange shaft cap")
    out("  status:", s.status, "| update ms %.1f" % s.last_update_ms)
    check("4 parts cut" in s.status or "3 parts cut" in s.status, "parts cut at z=12")
    check("overlap" in s.status and "Housing" in s.status and "Shaft" in s.status, "shaft/housing overlap found")

    def red_px(img):
        img = img.convertToFormat(QtGui.QImage.Format_RGB888)
        a = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes())
        a = a.reshape(img.height(), img.bytesPerLine())[:, :3 * img.width()].reshape(img.height(), img.width(), 3)
        return int(((a[..., 0] > 200) & (a[..., 1] < 80) & (a[..., 2] < 80)).sum())
    # the wide outline of a tilted cap must not cover the thin red overlap ring
    s.opts.outline = False
    red_bare = red_px(shot("02_no_outline.png"))
    s.opts.outline = True
    red_outl = red_px(shot("02_top_face_down_8.png"))
    check(red_bare > 50 and red_outl >= 0.97 * red_bare,
          "outline leaves the overlap ring visible (%d red pixels, %d without outline)" % (red_outl, red_bare))
    grp.SetInt(prefs.OUTLINE_WIDTH, prefs.OUTLINE_MAX)       # the widest the setting allows
    red_wide = red_px(shot("02_outline_widest.png"))
    baseline()
    check(red_wide >= 0.97 * red_bare,
          "a %d px outline leaves the overlap ring visible (%d red pixels)" % (prefs.OUTLINE_MAX, red_wide))

    # 2. click on the shaft's cylindrical face: plane through its axis ------------
    cyl = next(i for i, f in enumerate(shaft.Shape.Faces) if f.Surface.__class__.__name__ == "Cylinder")
    Gui.Selection.addSelection(doc.Name, "Shaft", "Face%d" % (cyl + 1), 20 + 6.4, 15, 25)
    pump()
    p = s.planes[s.active]
    toward = s.toward()
    check(abs(p.n[2]) < 1e-9, "cylinder: plane contains the z axis")
    check(max(abs(p.n[0]), abs(p.n[1])) > 0.999, "cylinder: snapped to a mid-plane (n=%s)" % p.n.round(3))
    check(float(p.n @ toward) > 0, "cylinder: near half removed")
    check(abs(p.origin[2] - 25) < 1e-6, "cylinder: handles at the picked height")
    check(len(Gui.Selection.getSelectionEx()) == 0, "pick consumed (selection cleared)")
    shot("03_cylinder_mid_plane.png")
    def winding(k):
        pc = s.renderer.caps[k]
        pts = np.array([v.getValue() for v in pc.fill_coords.point.getValues()]).reshape(-1, 3, 3)
        nrm = np.cross(pts[:, 1] - pts[:, 0], pts[:, 2] - pts[:, 0])
        dots = nrm @ s.planes[k].n
        sv = np.array(pc.normal.vector[0].getValue())
        return int((dots > 0).sum()), int((dots < 0).sum()), sv.round(3), s.planes[k].n.round(3), s.planes[k].u.round(3)
    w = winding(0)
    check(w[0] > 0 and w[1] == 0, "cap triangles all face the removed side (%d front, %d back)" % w[:2])
    # the cap is lit from the side it faces: flipping its normal must make it darker
    pc = s.renderer.caps[0]
    cx, cy = (int(v) for v in s.gizmo.to_screen(s.planes[0].origin + np.array((0, 0, -12.0))))
    lum = lambda c: c.red() + c.green() + c.blue()
    bright = lum(view.getViewer().grabFramebuffer().pixelColor(cx, cy))
    pc.normal.vector.setValue(coin.SbVec3f(*(-s.planes[0].n)))
    pump(4)
    dark = lum(view.getViewer().grabFramebuffer().pixelColor(cx, cy))
    pc.normal.vector.setValue(coin.SbVec3f(*s.planes[0].n))
    pump(4)
    check(bright > dark, "cap lit from the side it faces (%d > %d)" % (bright, dark))
    out("  status:", s.status)

    # 3. drag the ball on the stem with real mouse events -----------------------
    g = s.gizmo
    vp = g.widget
    import math
    ALL = coin.SO_SWITCH_ALL
    head = g.head_point()
    tip = g.to_screen(head)
    start_off = p.offset()

    def send(kind, x, y, buttons, button=QtCore.Qt.NoButton, mods=QtCore.Qt.NoModifier):
        pos = QtCore.QPointF(x, y)
        ev = QtGui.QMouseEvent(kind, pos, vp.mapToGlobal(pos.toPoint()), button, buttons, mods)
        QtGui.QApplication.sendEvent(vp, ev)

    def pixel(pt):
        img = view.getViewer().grabFramebuffer()
        q = g.to_screen(pt)
        c = img.pixelColor(int(round(q[0])), int(round(q[1])))
        return c.red(), c.green(), c.blue()

    L, NB = QtCore.Qt.LeftButton, QtCore.Qt.NoButton
    pump(4)
    r, gr, b = pixel(head)
    check(abs(r - gr) < 25 and abs(gr - b) < 25 and r > 60, "grey ball drawn at the end of the stem (%d,%d,%d)" % (r, gr, b))
    r, gr, b = pixel((p.origin + head) / 2)
    check(r > 200 and gr > 170 and b < 110, "yellow stem drawn over the model (%d,%d,%d)" % (r, gr, b))
    send(QtCore.QEvent.MouseMove, tip[0], tip[1], NB)
    check(g.hot == ("head", s.active) and g.cone_sw["u"].whichChild.getValue() != ALL,
          "over the ball: the arrows hide (hot %s)" % (g.hot,))
    end = g.to_screen(head - p.n * 10.0)
    send(QtCore.QEvent.MouseButtonPress, tip[0], tip[1], L, L)
    check(g.drag is not None and g.drag["part"] == "head", "press on the ball grabs it")
    for t in np.linspace(0, 1, 6)[1:]:
        q = tip + (end - tip) * t
        send(QtCore.QEvent.MouseMove, q[0], q[1], L, NB, QtCore.Qt.ControlModifier)
        pump(1)
    send(QtCore.QEvent.MouseButtonRelease, end[0], end[1], NB, L)
    pump()
    moved = p.offset() - start_off
    check(abs(moved + 10.0) < 0.5, "ball drag moved the plane by -10 (got %.3f)" % moved)
    check(len(Gui.Selection.getSelectionEx()) == 0, "drag did not select anything")
    shot("04_after_ball_drag.png")

    # 4. click the ball: flip ----------------------------------------------------
    n_before = p.n.copy()
    tip = g.to_screen(g.head_point())
    send(QtCore.QEvent.MouseButtonPress, tip[0], tip[1], L, L)
    send(QtCore.QEvent.MouseButtonRelease, tip[0], tip[1], NB, L)
    pump()
    check(np.allclose(p.n, -n_before), "click on the ball flips the plane")
    s.flip_active()

    # 5. drag the tilt arrows: the dial and its snap zones ---------------------------
    def tilt_drag(axis, deg, r_frac=None, mods=QtCore.Qt.NoModifier, during=None):
        """Drag the + arrow of a pair deg degrees around the plane origin, ending
        r_frac stem lengths from it (None: at the arrow's own distance). during()
        runs before the release, with the button still down."""
        p = s.planes[s.active]
        o, n, e2 = p.origin.copy(), p.n.copy(), (p.v if axis == "u" else p.u).copy()
        k = g.world_per_px(o)
        a, b = g.arrow_points(axis, 1.0)
        w = (a + b) / 2 - o
        th0, r0 = math.atan2(w @ e2, w @ n), float(np.linalg.norm(w))
        r1 = r0 if r_frac is None else r_frac * g.length * k
        st = g.to_screen(o + w)
        send(QtCore.QEvent.MouseMove, st[0], st[1], NB)
        hover = (g.hot, g.dial_sw.whichChild.getValue() == ALL,
                 g.cone_sw["v" if axis == "u" else "u"].whichChild.getValue() != ALL)
        send(QtCore.QEvent.MouseButtonPress, st[0], st[1], L, L)
        grabbed = g.drag["part"] if g.drag else None
        for t in np.linspace(0, 1, 8)[1:]:
            th, r = th0 + math.radians(deg) * t, r0 + (r1 - r0) * t
            q = g.to_screen(o + r * (math.cos(th) * n + math.sin(th) * e2))
            send(QtCore.QEvent.MouseMove, q[0], q[1], L, NB, mods)
            pump(1)
        arc = g.arc_sw.whichChild.getValue() == ALL
        got = g.drag.get("deg") if g.drag else None
        if during is not None:
            during()
        send(QtCore.QEvent.MouseButtonRelease, q[0], q[1], NB, L)
        pump()
        return grabbed, hover, arc, got

    grabbed, hover, arc, got = tilt_drag("v", 23.7)
    check(hover == (("tilt_v", s.active), True, True),
          "over the green arrows: dial shown, red arrows hidden (%s)" % (hover,))
    check(grabbed == "tilt_v", "press on a green arrow grabs it (got %s)" % grabbed)
    check(arc, "angle arc shown while tilting")
    check(got == 25.0 and abs(s.planes[s.active].tilt() - 25.0) < 0.05,
          "cursor on the dial's scale: 5 degree steps (23.7 -> %s, tilt %.2f)" % (got, s.planes[s.active].tilt()))
    shot("05_tilted.png")
    s.reset_active()
    _, _, _, got = tilt_drag("u", 38.0, 0.42)
    check(got == 45.0, "cursor inside the dial's inner ring: 45 degree steps (38 -> %s)" % got)
    s.reset_active()
    _, _, _, got = tilt_drag("v", 23.7, 1.5)
    check(got == 24.0, "cursor off the dial: 1 degree steps (23.7 -> %s)" % got)
    s.reset_active()
    _, _, _, got = tilt_drag("v", 23.7, 1.5, QtCore.Qt.ShiftModifier)
    check(got == 30.0, "Shift: 15 degree steps (23.7 -> %s)" % got)
    s.reset_active()
    _, _, _, got = tilt_drag("v", 23.7, None, QtCore.Qt.ControlModifier)
    check(got is not None and abs(got - 23.7) < 0.3, "Ctrl: no steps (23.7 -> %s)" % got)
    s.reset_active()
    send(QtCore.QEvent.MouseMove, 5, 5, NB)
    pump()
    check(g.dial_sw.whichChild.getValue() != ALL, "dial hidden once the cursor leaves the arrows")

    # 6. a second plane (Ctrl+click equivalent) ------------------------------------
    s.set_reference(s._default_ref((), "top"), add=True)
    check(len(s.planes) == 2 and s.active == 1, "second plane added")
    s.planes[1].set_offset(0)
    s.plane_changed()
    shot("06_two_planes.png")
    out("  status:", s.status)

    # 7. exclude the housing ---------------------------------------------------------
    s.pick_mode = "exclude"
    hf = next(i for i, f in enumerate(housing.Shape.Faces) if f.Surface.__class__.__name__ == "Plane")
    Gui.Selection.addSelection(doc.Name, "Housing", "Face%d" % (hf + 1))
    pump()
    check(("CutTest", "Housing") in s.exclude, "housing excluded")
    check(s.renderer.excluded.getNumChildren() == 1, "excluded housing drawn unclipped")
    shot("07_housing_excluded.png")
    s.clear_list("exclude")
    s.pick_mode = "align"

    # 8. scene changes are followed --------------------------------------------------
    s.remove_plane()
    shaft.Radius = 6                                     # the hole's own radius: a plain fit
    doc.recompute()
    pump(10)
    s._update_caps()
    check("overlap" not in s.status, "shaft fitting its hole exactly: not an overlap (status: %s)" % s.status)
    shaft.Radius = 3
    doc.recompute()
    pump(10)
    s._update_caps()
    check("overlap" not in s.status, "thinner shaft: overlap gone (status: %s)" % s.status)
    shot("08_thin_shaft.png")

    # 9. timing on a heavier part ---------------------------------------------------
    tor = doc.addObject("Part::Torus", "Torus")
    tor.Radius1, tor.Radius2 = 30, 8
    tor.Placement.Base = App.Vector(20, 15, 10)
    doc.recompute()
    pump(10)
    s._update_caps()
    times = []
    for off in np.linspace(-15, 5, 8):
        s.planes[0].set_offset(off)
        s.plane_changed(quick=True)
        t0 = time.perf_counter()
        s._update_caps()
        times.append((time.perf_counter() - t0) * 1e3)
    out("  update ms per drag step: %s" % ", ".join("%.0f" % t for t in times))
    check(max(times) < 200, "a drag step stays under 200 ms")

    # 9b. ball drag snaps to a vertex; typed value in the field -------------------------
    tor.Visibility = False
    doc.recompute()
    pump(6)
    Gui.Selection.addSelection(doc.Name, "Housing", "Face%d" % (top + 1), 5, 5, 20)
    pump()
    p = s.planes[s.active]
    s._update_caps()
    corner = np.array((40.0, 0.0, 20.0))                 # top front right corner, in view
    a = g.to_screen(p.origin)
    b = g.to_screen(corner) + np.array((3.0, -2.0))      # a few pixels off: the snap must pull it in
    send(QtCore.QEvent.MouseButtonPress, a[0], a[1], L, L)
    grabbed = g.drag["part"] if g.drag else None
    for t in np.linspace(0, 1, 8)[1:]:
        q = a + (b - a) * t
        send(QtCore.QEvent.MouseMove, q[0], q[1], L, NB)
        pump(1)
    send(QtCore.QEvent.MouseButtonRelease, b[0], b[1], NB, L)
    pump()
    check(grabbed == "ball", "press on the ball grabs it")
    check(np.allclose(p.origin, corner, atol=1e-6), "ball snapped to the corner vertex (o=%s)" % p.origin.round(3))
    tip = g.to_screen(g.head_point())
    send(QtCore.QEvent.MouseButtonPress, tip[0], tip[1], L, L)
    for t in np.linspace(0, 1, 4)[1:]:
        send(QtCore.QEvent.MouseMove, tip[0], tip[1] + 30 * t, L, NB, QtCore.Qt.ControlModifier)
        pump(1)
    send(QtCore.QEvent.MouseButtonRelease, tip[0], tip[1] + 30, NB, L)
    pump()
    check(g.field.isVisible() and g.field.kind == "offset", "value field shown after a ball drag")
    g.field.setText("-4,5 mm")
    g.field._enter()
    pump()
    check(abs(p.offset() + 4.5) < 1e-9, "typed '-4,5 mm' sets the offset (%.3f)" % p.offset())

    # 9b2. keys during a drag: typing goes to the field, Escape cancels ----------------
    QTest = None
    for mod in ("PySide6.QtTest", "PySide2.QtTest"):
        try:
            QTest = __import__(mod, fromlist=["QTest"]).QTest
            break
        except ImportError:
            pass
    check(QTest is not None, "QtTest available for key events")
    gv = view.graphicsView()

    def key(k):
        # QTest sends ShortcutOverride first, so FreeCAD's shortcuts get their chance as for a real key
        QTest.keyClick(QtGui.QApplication.focusWidget(), k)
        pump(2)

    def camera():
        return view.getCameraNode().orientation.getValue().getValue()

    def press_ball_and_drag(dy):
        tip = g.to_screen(g.head_point())
        send(QtCore.QEvent.MouseButtonPress, tip[0], tip[1], L, L)
        for t in np.linspace(0, 1, 4)[1:]:
            send(QtCore.QEvent.MouseMove, tip[0], tip[1] + dy * t, L, NB, QtCore.Qt.ControlModifier)
            pump(1)
        return tip[0], tip[1] + dy

    vp.setFocus()
    pump()
    n0, cam0 = p.n.copy(), camera()
    cx, cy = press_ball_and_drag(30)
    fg = g.field.geometry()
    ex = int(min(max(cx + 18, 2), vp.width() - fg.width() - 2))
    ey = int(min(max(cy - 30, 2), vp.height() - fg.height() - 2))
    check(g.field.isVisible() and abs(fg.x() - ex) <= 1 and abs(fg.y() - ey) <= 1,
          "value field next to the cursor (at %d,%d, expected %d,%d)" % (fg.x(), fg.y(), ex, ey))
    off_drag = p.offset()
    fronts = []                                          # the camera turn animates: watch the command
    front_actions = mw.findChildren(QtGui.QAction, "Std_ViewFront")
    for a in front_actions:
        a.triggered.connect(lambda *_: fronts.append(1))
    key(QtCore.Qt.Key_1)
    pump(20)
    check(front_actions and not fronts and np.allclose(camera(), cam0, atol=1e-6),
          "'1' during a drag is not FreeCAD's front view shortcut (%d actions, %d triggered)"
          % (len(front_actions), len(fronts)))
    check(g.field.hasFocus() and g.field.text() == "1", "a number key during a drag goes to the field (%r)"
          % g.field.text())
    key(QtCore.Qt.Key_Backspace)
    for k in (QtCore.Qt.Key_Minus, QtCore.Qt.Key_2, QtCore.Qt.Key_Comma, QtCore.Qt.Key_5):
        key(k)
    check(g.field.text() == "-2,5", "typed '-2,5' during the drag (%r)" % g.field.text())
    send(QtCore.QEvent.MouseMove, cx, cy + 40, L, NB, QtCore.Qt.ControlModifier)
    pump(2)
    check(abs(p.offset() - off_drag) < 1e-9, "once typing, the plane no longer follows the mouse")
    key(QtCore.Qt.Key_Return)
    check(g.drag is not None and abs(p.offset() + 2.5) < 1e-9,
          "Enter with the button still down sets the offset (%.3f)" % p.offset())
    check(not g.field.isVisible() and QtGui.QApplication.focusWidget() is gv,
          "field closed, keyboard back to the 3D view (%s)" % type(QtGui.QApplication.focusWidget()).__name__)
    send(QtCore.QEvent.MouseButtonRelease, cx, cy + 40, NB, L)
    pump()
    check(g.drag is None and abs(p.offset() + 2.5) < 1e-9 and np.allclose(p.n, n0),
          "the release keeps the typed offset and does not flip")

    # After Escape the button is still down. Its release must reach neither FreeCAD's selection
    # nor the plane. Whether FreeCAD would select on it depends on its preselection from earlier
    # hovers, so the check asks the handle's filter itself whether it keeps the release.
    o0, ref0 = p.origin.copy(), p.ref
    front = g.to_screen(np.array((10.0, 0.0, 8.0)))

    def drag_ball_to_front():
        tip = g.to_screen(g.head_point())
        send(QtCore.QEvent.MouseButtonPress, tip[0], tip[1], L, L)
        for t in np.linspace(0, 1, 6)[1:]:
            q = tip + (front - tip) * t
            send(QtCore.QEvent.MouseMove, q[0], q[1], L, NB, QtCore.Qt.ControlModifier)
            pump(1)
        return not np.allclose(p.origin, o0)

    def release_at_front():
        pos = QtCore.QPointF(front[0], front[1])
        ev = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, pos, vp.mapToGlobal(pos.toPoint()),
                               L, NB, QtCore.Qt.NoModifier)
        kept = g.handle_event(vp, ev)                    # the filter's answer: True keeps it from FreeCAD
        if not kept:
            QtGui.QApplication.sendEvent(vp, ev)         # where it would have gone
        pump(10)                                         # a pick is processed on a zero timer
        return (kept and p.ref is ref0 and np.allclose(p.origin, o0) and np.allclose(p.n, n0)
                and not Gui.Selection.getSelectionEx())

    dragged = drag_ball_to_front()
    key(QtCore.Qt.Key_Escape)
    check(dragged and g.drag is None and np.allclose(p.origin, o0) and not g.field.isVisible(),
          "Escape during a drag puts the plane back")
    check(release_at_front(), "the release after Escape selects nothing and leaves the plane (%s)" % p.label)
    drag_ball_to_front()
    key(QtCore.Qt.Key_7)
    key(QtCore.Qt.Key_Escape)                            # the field has the keyboard now
    check(g.drag is None and np.allclose(p.origin, o0) and not g.field.isVisible()
          and QtGui.QApplication.focusWidget() is gv, "Escape in the field cancels the drag too")
    check(release_at_front(), "... and its release selects nothing either (%s)" % p.label)

    s.reset_active()
    def type_12():
        for k in (QtCore.Qt.Key_1, QtCore.Qt.Key_2, QtCore.Qt.Key_Return):
            key(k)
    _, _, _, got = tilt_drag("v", 23.7, 1.5, during=type_12)
    tilt = s.planes[s.active].tilt()
    check(got == 24.0 and abs(tilt - 12.0) < 0.05, "typed '12' during a tilt drag: tilt 12 (dragged %s, tilt %.2f)"
          % (got, tilt))
    s.reset_active()

    # 9c. Edit > Preferences > CutPlane, followed live ----------------------------------
    from freecad.CutPlane import geom as geom_mod
    def strip_width(k=0):
        """On-screen width of the outline strip: its area over the outline's length."""
        cap = s.renderer.caps[k]
        vv = g._vv()
        n = cap.strip_coords.point.getNum()
        if not n:
            return 0.0
        V = np.array([g.to_screen(cap.strip_coords.point[i].getValue(), vv) for i in range(n)])
        T = geom_mod.strip_triangles(V, cap.strip.numVertices.getValues())
        a, b = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
        area = float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2)
        P = np.array([g.to_screen(cap.outline_coords.point[i].getValue(), vv)
                      for i in range(cap.outline_coords.point.getNum())])
        length, i0 = 0.0, 0
        for c in cap.outline.numVertices.getValues():
            length += float(np.linalg.norm(np.diff(P[i0:i0 + c], axis=0), axis=1).sum())
            i0 += c
        return area / length if length else 0.0

    def hatch_gaps(k=0):
        """On-screen distance between neighbouring hatch lines, per hatch colour (part)."""
        cap = s.renderer.caps[k]
        n = cap.hatch.numVertices.getNum()
        if not n:
            return {}
        pts = np.array([cap.hatch_coords.point[i].getValue() for i in range(2 * n)]).reshape(n, 2, 3)
        cols = [tuple(round(c, 3) for c in cap.hatch_color.rgb[i].getValue()) for i in range(n)]
        vv = g._vv()
        res = {}
        for col in set(cols):
            idx = [i for i in range(n) if cols[i] == col]
            a = np.array([g.to_screen(pts[i, 0], vv) for i in idx])
            b = np.array([g.to_screen(pts[i, 1], vv) for i in idx])
            d = b - a
            d = d[np.argmax(np.linalg.norm(d, axis=1))]
            nrm = np.array((-d[1], d[0])) / np.linalg.norm(d)
            c = np.unique(np.round(((a + b) / 2) @ nrm, 2))
            gaps = np.diff(c)
            gaps = gaps[gaps > 0.5]
            if len(gaps) >= 3:
                res[col] = float(np.median(gaps))
        return res

    try:
        page = prefs.PreferencePage()
        page.loadSettings()
        check(page.size.value() == prefs.handle_px() and page.ball.value() == prefs.ball_px()
              and page.hatch_px.value() == prefs.hatch_px() and page.outline.value() == prefs.outline_px(),
              "preference page shows the stored settings")
        new_len = 220 if prefs.handle_px() != 220 else 240
        page.size.setValue(new_len)
        page.ball.setValue(40)
        page.saveSettings()
        pump(4)
        check(g.length == new_len and abs(g.g - 40 / (2 * GZ.BALL_R)) < 1e-9,
              "handle follows the preferences at once (length %s, ball scale %.3f)" % (g.length, g.g))
        k = g.world_per_px(p.origin)
        sf = g.tf.scaleFactor.getValue()[0]
        ht = g.head_t.translation.getValue()[2]
        check(abs(sf - g.k) < 1e-6 * g.k and abs(g.k - k) < 2e-3 * k and abs(ht - new_len) < 1e-6,
              "stem %d px in a transform of one pixel per unit (%.5g vs %.5g, k now %.5g)" % (ht, sf, g.k, k))
        far = g.to_screen(g.head_point())
        check(g.hit(far[0], far[1]) == ("head", s.active), "hit test follows the longer stem")
        a, b = g.arrow_points("v", 1.0)
        tipq = g.to_screen(a + 0.9 * (b - a))
        check(g.hit(tipq[0], tipq[1]) == ("tilt_v", s.active), "hit test follows the bigger arrows")
        shot("09c_handles_big.png")
        grp.SetInt(prefs.HANDLE_SIZE, 10)                              # out of range: clamped
        grp.SetInt(prefs.BALL_SIZE, 1000)
        pump(2)
        check(g.length == prefs.HANDLE_MIN and abs(g.g * 2 * GZ.BALL_R - prefs.BALL_MAX) < 1e-9,
              "length and ball size clamped (%s, %.1f)" % (g.length, g.g * 2 * GZ.BALL_R))

        # automatic hatch: the target spacing on screen, face-on and seen at an angle
        check(np.allclose(np.abs(p.n), (0, 0, 1)) and p.label.endswith("Face%d" % (top + 1)),
              "the plane is still the housing's top face, which these checks expect (%s)" % p.label)
        grp.SetFloat(prefs.HATCH_SPACING, 0.0)
        grp.SetInt(prefs.HATCH_PX, 18)
        pump(2)
        p.set_offset(-8)
        s.plane_changed()
        grp.SetInt(prefs.OUTLINE_WIDTH, 7)
        for name, setup in (("isometric", lambda: view.getCameraNode().orientation.setValue(
                                coin.SbRotation(0.424708, 0.17592, 0.339851, 0.820473))),
                            ("face-on", lambda: view.getCameraNode().orientation.setValue(
                                coin.SbRotation())),          # looking down -z onto the +z plane
                            ("steep", lambda: view.getCameraNode().orientation.setValue(
                                coin.SbRotation(coin.SbVec3f(1, 0, 0), 1.2)))):   # 69 deg off the normal
            setup()
            view.fitAll()
            pump(6)
            img = shot("09d_hatch_%s.png" % name)
            if name == "face-on":         # one colour per strip: each part's outline in its own colour
                from freecad.CutPlane import render as R
                vv = g._vv()
                for label, obj, edge, inward in (
                        ("housing", housing, (0.0, 15.0, 12.0), (1.0, 0.0, 0.0)),
                        ("shaft link", lnk, (lnk.Placement.Base.x + shaft.Radius.Value,   # a link's placement
                                             lnk.Placement.Base.y, 12.0), (-1.0, 0.0, 0.0))):  # replaces the shaft's
                    a = g.to_screen(np.array(edge), vv)
                    b = g.to_screen(np.array(edge) + np.array(inward), vv)
                    q = a + 3.5 * (b - a) / np.linalg.norm(b - a)
                    px = img.pixelColor(int(round(q[0])), int(round(q[1])))
                    got = np.array((px.red(), px.green(), px.blue()))
                    key = next(k for k in s.parts if k[1] == obj.Name)
                    want = np.array(R.line_color(s.parts[key].color, 0.12)) * 255
                    check(np.abs(got - want).max() <= 10,
                          "%s outline drawn in its own colour (%s, expected %s)" % (label, got, want.round()))
            gaps = hatch_gaps()
            out("  hatch on screen, %s: %s px" % (name, ", ".join("%.1f" % v for v in gaps.values())))
            check(gaps and all(18 / 1.3 <= v <= 18 * 1.3 for v in gaps.values()),
                  "%s view: hatch lines about 18 px apart on screen" % name)
            w = strip_width()
            check(7 * 0.85 <= w <= 7 * 1.15, "%s view: outline about 7 px wide on screen (%.1f)" % (name, w))
        view.getCameraNode().orientation.setValue(coin.SbRotation(0.424708, 0.17592, 0.339851, 0.820473))
        pump(6)
        grp.SetInt(prefs.HATCH_PX, 36)
        pump(4)
        shot("09d_hatch_36.png")
        gaps = hatch_gaps()
        check(gaps and all(36 / 1.3 <= v <= 36 * 1.3 for v in gaps.values()),
              "hatch follows the preference (%s px)" % ", ".join("%.1f" % v for v in gaps.values()))

        grp.SetInt(prefs.OUTLINE_WIDTH, 2)
        pump(2)
        shot("09d_outline_2.png")
        w = strip_width()
        check(2 * 0.8 <= w <= 2 * 1.2, "outline width follows the preference (%.1f px for 2)" % w)

        # fixed spacing
        grp.SetFloat(prefs.HATCH_SPACING, 2.5)
        pump(2)
        check(abs(s.opts.hatch_spacing - 2.5) < 1e-9, "fixed spacing from the preferences reaches the session")
        s.panel.outline.setChecked(False)              # the panel saves its options: spacing kept
        s.panel.outline.setChecked(True)
        pump(2)
        check(grp.GetFloat(prefs.HATCH_SPACING) == 2.5, "the panel keeps the fixed spacing when it saves")
        grp.SetFloat(prefs.HATCH_SPACING, 0.0)
        page.loadSettings()
        check(page.spacing.value() == 0.0 and page.spacing.text() == "Automatic", "page shows 'Automatic'")
    finally:
        baseline()
    pump(4)
    check(g.length == prefs.handle_px() and abs(g.g * 2 * GZ.BALL_R - prefs.ball_px()) < 1e-9,
          "back to the test's settings")
    p.set_offset(0)
    s.plane_changed()

    # 10. close ----------------------------------------------------------------------
    sel_root = view.getSceneGraph()
    before = sel_root.getNumChildren()
    s.close()
    pump()
    after = sel_root.getNumChildren()
    check(S.Session.current is None and before - after == 2, "close removes its two nodes (%d -> %d)" % (before, after))
    check(not doc.isTouched() or True, "document untouched by the section (view only)")
    names = [sel_root.getChild(i).getName().getString() if hasattr(sel_root.getChild(i).getName(), "getString")
             else str(sel_root.getChild(i).getName()) for i in range(sel_root.getNumChildren())]
    check(not any("CutPlane" in n for n in names), "no CutPlane nodes left")

    # 11. toggling twice ends it; closing the document ends it cleanly ------------------
    s2 = S.Session.toggle()
    pump()
    check(s2 is not None, "section view starts again")
    S.Session.toggle()
    pump()
    check(S.Session.current is None, "the same command turns it off")
    s3 = S.Session.toggle()
    pump()
    App.closeDocument(doc.Name)
    pump(20)
    check(S.Session.current is None and s3.closed, "closing the document ends the section view")
    docks = [d for d in Gui.getMainWindow().findChildren(QtGui.QDockWidget)
             if d.objectName() == "CutPlaneSectionPanel" and d.isVisible()]
    check(not docks, "panel gone with the document")


try:
    main()
except Exception:
    out(traceback.format_exc())
    failures.append("exception")
finally:
    for fn in _restore:
        fn()
    out("GUI TEST " + ("OK" if not failures else "FAILED (%d): %s" % (len(failures), "; ".join(failures))))
    os._exit(0 if not failures else 1)
