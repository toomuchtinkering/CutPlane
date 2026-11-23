"""CutPlane on realistic content: PartDesign, datums, an Assembly, heavy models.

Run with tests/run_gui.sh tests/gui_test_models.py. Ends with "MODELS TEST OK".
"""
import math
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


def front_view(view):
    """Bring the 3D view's tab to the front, isometric camera, fit."""
    from PySide import QtGui
    from pivy import coin
    import FreeCADGui as Gui
    pump(10)
    w = view.graphicsView()
    while w is not None and not isinstance(w, QtGui.QMdiSubWindow):
        w = w.parentWidget()
    if w is not None:
        Gui.getMainWindow().findChild(QtGui.QMdiArea).setActiveSubWindow(w)
    pump(10)
    view.getCameraNode().orientation.setValue(coin.SbRotation(0.424708, 0.17592, 0.339851, 0.820473))
    view.fitAll()
    pump(10)


def shot(view, name):
    from freecad.CutPlane import session as S
    pump(4)
    if S.Session.current is not None:
        S.Session.current._update_caps()
    pump(4)
    view.getViewer().grabFramebuffer().save(os.path.join(OUT, name))


def pick(s, doc, obj, sub, pt=None):
    import FreeCADGui as Gui
    if pt is None:
        Gui.Selection.addSelection(doc.Name, obj, sub)
    else:
        Gui.Selection.addSelection(doc.Name, obj, sub, *pt)
    pump()
    return s.planes[s.active]


def build_body(doc, name="Body", holes=True):
    import FreeCAD as App
    import Part
    import Sketcher
    body = doc.addObject("PartDesign::Body", name)
    sk = body.newObject("Sketcher::SketchObject", name + "Sketch")
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[3], "")]      # XY plane
    sk.MapMode = "FlatFace"
    pts = [App.Vector(0, 0, 0), App.Vector(60, 0, 0), App.Vector(60, 40, 0), App.Vector(0, 40, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    pad = body.newObject("PartDesign::Pad", name + "Pad")
    pad.Profile = sk
    pad.Length = 15
    doc.recompute()
    if holes:
        sk2 = body.newObject("Sketcher::SketchObject", name + "HoleSketch")
        sk2.AttachmentSupport = [(pad, "Face6")]
        sk2.MapMode = "FlatFace"
        for x in (15, 30, 45):
            sk2.addGeometry(Part.Circle(App.Vector(x, 20, 0), App.Vector(0, 0, 1), 5))
        pocket = body.newObject("PartDesign::Pocket", name + "Holes")
        pocket.Profile = sk2
        pocket.Type = 1                        # through all
        pocket.Reversed = False
        doc.recompute()
        if pocket.Shape.Volume >= pad.Shape.Volume - 1:      # wrong side: flip
            pocket.Reversed = True
            doc.recompute()
    return body


def main():
    import numpy as np
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part
    from PySide import QtCore, QtGui
    from pivy import coin  # noqa: F401
    import freecad.CutPlane.commands
    from freecad.CutPlane import session as S
    os.makedirs(OUT, exist_ok=True)
    if Gui.Command.get("CutPlane_SectionView") is None:
        freecad.CutPlane.commands.register()

    # ---------------------------------------------------------------- PartDesign and datums
    doc = App.newDocument("PD")
    body = build_body(doc)
    holes = doc.getObject("BodyHoles")
    check(holes.Shape.isValid() and len(holes.Shape.Faces) == 6 + 3, "test body built with 3 holes")
    dplane = body.newObject("PartDesign::Plane", "DatumPlane")
    dplane.AttachmentSupport = [(body.Origin.OriginFeatures[4], "")]     # XZ plane
    dplane.MapMode = "FlatFace"
    dplane.AttachmentOffset = App.Placement(App.Vector(0, 0, -20), App.Rotation())
    lcs = body.newObject("PartDesign::CoordinateSystem", "LCS")
    lcs.MapMode = "Deactivated"
    lcs.Placement = App.Placement(App.Vector(30, 20, 7), App.Rotation(App.Vector(0, 1, 0), 30))
    doc.recompute()
    view = Gui.ActiveDocument.ActiveView
    front_view(view)
    Gui.Selection.clearSelection()
    s = S.Session.toggle()
    pump()
    out("  parts:", sorted(k[1] for k in s.parts))
    check(("PD", "Body") in s.parts, "the Body is one part (not its features)")
    p = s.planes[0]
    check(p.ref.kind == "default plane", "nothing selected: default plane (%s)" % p.label)

    # planar face of the pad
    top = next(i for i, f in enumerate(holes.Shape.Faces)
               if f.Surface.__class__.__name__ == "Plane" and abs(f.normalAt(0, 0).z - 1) < 1e-9)
    p = pick(s, doc, "Body", "BodyHoles.Face%d" % (top + 1), (5, 5, 15))
    check(np.allclose(p.n, (0, 0, 1)) and abs(p.origin[2] - 15) < 1e-9, "Body face: top plane z=15")
    # a hole: plane through its axis
    hole_face = next(i for i, f in enumerate(holes.Shape.Faces) if f.Surface.__class__.__name__ == "Cylinder")
    c = holes.Shape.Faces[hole_face].Surface.Center
    p = pick(s, doc, "Body", "BodyHoles.Face%d" % (hole_face + 1), (c.x + 5, c.y, 8))
    check(abs(p.n[2]) < 1e-9 and abs(float(p.n @ np.array((c.x, c.y, 0))) - float(p.n @ p.origin)) < 1e-6,
          "hole: plane through its axis (n=%s)" % p.n.round(3))
    shot(view, "m01_body_hole_axis.png")
    out("  status:", s.status)
    check("1 part cut" in s.status, "body cut through the hole")
    # circular edge: plane through the circle's axis, origin at its centre
    edge = next(i for i, e in enumerate(holes.Shape.Edges) if e.Curve.__class__.__name__ == "Circle")
    ce = holes.Shape.Edges[edge].Curve.Center
    p = pick(s, doc, "Body", "BodyHoles.Edge%d" % (edge + 1), (ce.x + 5, ce.y, ce.z))
    check(np.allclose(p.origin, (ce.x, ce.y, ce.z)), "circular edge: handles at the circle centre")
    # origin XY plane of the body
    xy = next(f for f in body.Origin.OriginFeatures if f.Role == "XY_Plane")
    p = pick(s, doc, "Body", "%s.%s." % (body.Origin.Name, xy.Name), (10, 10, 0))
    check(abs(abs(p.n[2]) - 1) < 1e-9 and abs(p.origin[2]) < 1e-9, "origin XY plane picked (%s)" % p.ref.kind)
    # PartDesign datum plane (XZ at y=20)
    p = pick(s, doc, "Body", "DatumPlane.", (10, 20, 5))
    check(abs(abs(p.n[1]) - 1) < 1e-9 and abs(p.origin[1] - 20) < 1e-6,
          "datum plane picked: y=20 (n=%s o=%s, %s)" % (p.n.round(3), p.origin.round(3), p.ref.kind))
    shot(view, "m02_datum_plane.png")
    # local coordinate system: its XY plane, tilted 30 degrees about y
    out("  LCS Placement", lcs.Placement, "| global", lcs.getGlobalPlacement(),
        "| getSubObject r3", body.getSubObject("LCS.", retType=3),
        "| r1+matrix", body.getSubObject("LCS.", retType=1, matrix=App.Matrix())[1], "| MapMode", lcs.MapMode)
    p = pick(s, doc, "Body", "LCS.")
    nz = App.Rotation(App.Vector(0, 1, 0), 30).multVec(App.Vector(0, 0, 1))
    check(abs(abs(float(p.n @ np.array(tuple(nz)))) - 1) < 1e-6 and np.allclose(p.origin, (30, 20, 7)),
          "coordinate system: its XY plane (n=%s, o=%s, %s)" % (p.n.round(3), p.origin.round(3), p.ref.kind))
    shot(view, "m03_lcs.png")
    # sketch: its plane
    p = pick(s, doc, "Body", "BodySketch.")
    check(abs(abs(p.n[2]) - 1) < 1e-9, "sketch: its plane (%s)" % p.ref.kind)
    # a vertex keeps the orientation
    n_before = p.n.copy()
    vtx = holes.Shape.Vertexes[0].Point
    p = pick(s, doc, "Body", "BodyHoles.Vertex1", (vtx.x, vtx.y, vtx.z))
    check(np.allclose(p.n, n_before) and np.allclose(p.origin, (vtx.x, vtx.y, vtx.z)),
          "vertex: plane moves through it, orientation kept")
    s.close()
    check(not doc.isTouched(), "PartDesign document not modified by the section")

    # ---------------------------------------------------------------- Assembly of links
    doc2 = App.newDocument("Asm")
    part = build_body(doc2, "Bracket", holes=True)
    part.ViewObject.ShapeAppearance = (App.Material(DiffuseColor=(0.5, 0.65, 0.85)),)
    pin = doc2.addObject("Part::Cylinder", "Pin")
    # 0.5 too big: overlap in the holes. (0.2 is below what the display meshes can tell
    # from a plain fit: the bracket is tessellated to 0.19 mm, see geom.overlaps.)
    pin.Radius, pin.Height = 5.5, 40
    pin.ViewObject.ShapeAppearance = (App.Material(DiffuseColor=(0.9, 0.55, 0.2)),)
    asm = None
    try:
        asm = doc2.addObject("Assembly::AssemblyObject", "Assembly")
        asm.Type = "Assembly"
        asm.newObject("Assembly::JointGroup", "Joints")
    except Exception as e:
        out("  (no Assembly object: %s; using App::Part)" % e)
        asm = doc2.addObject("App::Part", "Assembly")
    links = []
    for i in range(3):
        L = doc2.addObject("App::Link", "Bracket_%d" % i)
        L.LinkedObject = part
        L.Placement = App.Placement(App.Vector(0, 0, i * 16), App.Rotation())
        asm.addObject(L)
        links.append(L)
    pl = doc2.addObject("App::Link", "Pin_0")
    pl.LinkedObject = pin
    pl.Placement.Base = App.Vector(15, 20, -5)
    asm.addObject(pl)
    asm.Placement = App.Placement(App.Vector(100, 0, 0), App.Rotation(App.Vector(0, 0, 1), 20))
    part.Visibility = False
    pin.Visibility = False
    doc2.recompute()
    view2 = Gui.ActiveDocument.ActiveView
    front_view(view2)
    Gui.Selection.clearSelection()
    s = S.Session.toggle()
    pump()
    keys = sorted(k[1] for k in s.parts)
    out("  assembly parts:", keys)
    check({"Bracket_0", "Bracket_1", "Bracket_2", "Pin_0"} <= set(keys), "every link in the assembly is a part")
    check("Bracket" not in keys and "Pin" not in keys, "hidden originals are not parts")
    # pick the pin's cylinder through the link inside the assembly
    pin_face = next(i for i, f in enumerate(pin.Shape.Faces) if f.Surface.__class__.__name__ == "Cylinder")
    gpin = Part.getShape(asm, "Pin_0.Face%d" % (pin_face + 1), needSubElement=True)
    ax = np.array(tuple(gpin.Surface.Axis))
    cen = np.array(tuple(gpin.Surface.Center))
    probe = cen + np.cross(ax, (0, 0, 1)) * 0 + np.array((0, 0, 10))
    p = pick(s, doc2, "Assembly", "Pin_0.Face%d" % (pin_face + 1), tuple(probe))
    check(abs(float(p.n @ ax)) < 1e-6, "pin in rotated assembly: plane contains its axis")
    check(abs(float(p.n @ (cen - p.origin))) < 1e-6, "pin in rotated assembly: plane through its axis")
    shot(view2, "m04_assembly_pin.png")
    out("  status:", s.status)
    check("overlap" in s.status and "Pin_0" in s.status, "pin/bracket overlaps found")
    cut = set(k[1] for k in s.cut_keys.get(0, []))
    check({"Bracket_0", "Bracket_1", "Bracket_2", "Pin_0"} <= cut, "all three brackets and the pin are cut")
    # neighbours get different hatch angles: brackets touch each other along z? (16 apart, 15 thick: no)
    s.view_normal()
    pump(10)
    shot(view2, "m05_assembly_view_normal.png")
    s.close()

    # ---------------------------------------------------------------- heavy models
    doc3 = App.newDocument("Heavy")
    plate = Part.makeBox(200, 200, 10)
    cyls = [Part.makeCylinder(3, 20, App.Vector(5 + 10 * i, 5 + 10 * j, -5)) for i in range(20) for j in range(20)]
    perf = doc3.addObject("Part::Feature", "Perforated")
    perf.Shape = plate.cut(Part.makeCompound(cyls))
    many = []
    gear = Part.makeCylinder(8, 6).cut(Part.makeCylinder(3, 6))
    for k in range(60):
        g = doc3.addObject("Part::Feature", "Ring%02d" % k)
        g.Shape = gear
        g.Placement.Base = App.Vector(220 + (k % 10) * 18, (k // 10) * 18, 2)
        many.append(g)
    doc3.recompute()
    view3 = Gui.ActiveDocument.ActiveView
    front_view(view3)
    Gui.Selection.clearSelection()
    t0 = time.perf_counter()
    s = S.Session.toggle()
    pump()
    out("  start ms %.0f, harvest ms %.0f, parts %d, triangles %d" % (
        (time.perf_counter() - t0) * 1e3, s.harvest_ms, len(s.parts), sum(len(i.T) for i in s.instances)))
    s.set_reference(s._default_ref((), "top"))
    s.planes[0].set_offset(0.0)          # through the middle of the plate and rings
    times = []
    for off in np.linspace(-2.5, 2.5, 7):      # rings span z 2..8, the plane starts at z=5
        s.planes[0].set_offset(off)
        s.plane_changed(quick=True)
        t1 = time.perf_counter()
        s._update_caps()
        times.append((time.perf_counter() - t1) * 1e3)
    out("  drag steps ms: %s | status: %s" % (", ".join("%.0f" % t for t in times), s.status))
    check("61 parts cut" in s.status, "plate and 60 rings cut")
    check(np.median(times) < 200, "median drag step under 200 ms on 61 parts / 400 holes (%.0f)" % np.median(times))
    shot(view3, "m06_heavy.png")
    import cProfile, pstats, io
    pr = cProfile.Profile()
    pr.enable()
    s._update_caps()
    pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(22)
    out(buf.getvalue())
    s.close()


try:
    main()
except Exception:
    out(traceback.format_exc())
    failures.append("exception")
finally:
    out("MODELS TEST " + ("OK" if not failures else "FAILED (%d): %s" % (len(failures), "; ".join(failures))))
    os._exit(0 if not failures else 1)
