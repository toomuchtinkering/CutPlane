# SPDX-License-Identifier: LGPL-2.1-or-later
"""Section plane from what the user clicked.

Rules (the plane normal always points to the side that is removed):

* planar face: the face's own plane, normal = outward face normal, so the
  part is kept whole until the plane is dragged into it;
* cylinder, cone, surface of revolution, torus: the plane through the axis
  that faces the camera, snapped to the closest mid-plane of the part's frame;
* sphere: through the centre, facing the camera (snapped);
* other curved faces: the tangent plane at the picked point;
* straight edge or datum line: the plane containing it, facing the camera;
* circle or arc: the plane through its axis (a cut through the hole);
* vertex or datum point: through the point, keeping the current orientation;
* datum plane, coordinate system (its XY plane), sketch: their plane.

The origin, where the handles appear, is the picked point projected onto the
plane, so the handles show up where the user clicked.
"""

import numpy as np

import FreeCAD as App
import Part

from . import geom

WORLD_AXES = (np.array((1.0, 0, 0)), np.array((0, 1.0, 0)), np.array((0, 0, 1.0)))

DATUM_PLANES = ("App::Plane",)
DATUM_LINES = ("App::Line",)
DATUM_POINTS = ("App::Point",)
FRAMES = ("App::LocalCoordinateSystem", "PartDesign::CoordinateSystem", "App::Origin")
SKETCHES = ("Sketcher::SketchObject",)


class Reference:
    """A plane frame: origin, normal (removed side), in-plane u; plus a label."""

    def __init__(self, origin, normal, u=None, label="", kind=""):
        self.origin = np.asarray(origin, dtype=float)
        self.normal = geom.unit(normal)
        self.u, _ = geom.frame_from_normal(self.normal, u)
        self.label = label
        self.kind = kind

    def __repr__(self):
        return "Reference(%s, o=%s, n=%s)" % (self.kind, self.origin.round(4), self.normal.round(4))


def vec(v):
    return np.array((v.x, v.y, v.z), dtype=float)


def app_vec(a):
    return App.Vector(float(a[0]), float(a[1]), float(a[2]))


def _project(p, origin, n):
    p = np.asarray(p, dtype=float)
    return p - n * float(np.dot(p - origin, n))


def _best_inplane(n, axes):
    """Of the candidate axes, the one that lies most in the plane (for u)."""
    best, best_len = None, 0.0
    for a in axes:
        w = a - n * float(np.dot(a, n))
        ln = float(np.linalg.norm(w))
        if ln > best_len + 1e-9:
            best, best_len = w, ln
    return best


def split_sub(sub):
    """'Body.Pad.Face6' -> ('Body.Pad.', 'Face6'); 'Body.Pad.' -> ('Body.Pad.', '')."""
    sub = sub or ""
    elem = sub.rsplit(".", 1)[-1]
    return sub[:len(sub) - len(elem)], elem


def frame_axes(top, path):
    """Global axes of the object at `path` below `top`, then the world axes."""
    axes = []
    try:
        _obj, mat = top.getSubObject(path, retType=1, matrix=App.Matrix())
        A = np.array(mat.A, dtype=float).reshape(4, 4)[:3, :3]
        axes = [geom.unit(A[:, k]) for k in range(3)]
    except Exception:
        pass
    for w in WORLD_AXES:
        if all(abs(float(np.dot(w, a))) < 0.999 for a in axes):
            axes.append(w)
    return axes


# ----------------------------------------------------------------------
# Shapes
# ----------------------------------------------------------------------
def from_face(face, picked, toward, axes, label=""):
    surf = face.Surface
    kind = type(surf).__name__
    P = np.asarray(picked, float) if picked is not None else vec(face.CenterOfMass)

    axis = center = None
    if kind in ("Cylinder", "Cone", "Toroid"):
        axis, center = vec(surf.Axis), vec(surf.Center)
    elif kind == "SurfaceOfRevolution":
        axis, center = vec(surf.Direction), vec(surf.Location)

    if axis is not None:
        n = geom.plane_through_axis(axis, toward, axes)
        return Reference(_project(P, center, n), n, axis, label, "axis of " + kind.lower())
    if kind == "Sphere":
        center = vec(surf.Center)
        n = geom.snap_direction(toward, axes)
        return Reference(_project(P, center, n), n, _best_inplane(n, axes), label, "sphere centre")

    # planar or general surface: tangent plane at the picked point, outward normal
    try:
        uv = face.Surface.parameter(app_vec(P))
        n = vec(face.normalAt(*uv))
    except Exception:
        n = vec(face.normalAt(0, 0))
    if np.linalg.norm(n) < 1e-12:
        return None
    n = geom.unit(n)
    if face.isPlanar() if hasattr(face, "isPlanar") else kind == "Plane":
        n = geom.snap_direction(n, axes, 0.01)          # clean up rounding only
        kind = "face"
    else:
        kind = "tangent plane"
    return Reference(P, n, _best_inplane(n, axes), label, kind)


def from_edge(edge, picked, toward, axes, label=""):
    curve = edge.Curve
    kind = type(curve).__name__
    P = np.asarray(picked, float) if picked is not None else vec(edge.CenterOfMass)
    if kind in ("Line", "LineSegment") or (len(edge.Vertexes) == 2 and edge.Length > 0
                                           and abs(edge.Length - edge.Vertexes[0].Point.distanceToPoint(
                                               edge.Vertexes[1].Point)) < 1e-9 * edge.Length):
        a, b = vec(edge.Vertexes[0].Point), vec(edge.Vertexes[-1].Point)
        d = geom.unit(b - a)
        n = geom.plane_through_axis(d, toward, axes)
        return Reference(_project(P, a, n), n, d, label, "edge")
    if kind in ("Circle", "ArcOfCircle", "Ellipse", "ArcOfEllipse"):
        axis, center = vec(curve.Axis), vec(curve.Center)
        n = geom.plane_through_axis(axis, toward, axes)
        return Reference(center, n, axis, label, "axis of circle")
    try:
        t = vec(edge.tangentAt(edge.Curve.parameter(app_vec(P))))
    except Exception:
        t = vec(edge.tangentAt(edge.FirstParameter))
    n = geom.plane_through_axis(t, toward, axes)
    return Reference(P, n, t, label, "edge")


def from_point(p, toward, axes, current_normal=None, current_u=None, label=""):
    if current_normal is not None:
        return Reference(p, current_normal, current_u, label, "point")
    n = geom.snap_direction(toward, axes)
    return Reference(p, n, _best_inplane(n, axes), label, "point")


def from_placement(pl, picked, toward, label="", kind="plane"):
    """Plane = XY plane of a global placement; normal oriented toward the camera."""
    R = pl.Rotation
    n = vec(R.multVec(App.Vector(0, 0, 1)))
    u = vec(R.multVec(App.Vector(1, 0, 0)))
    base = vec(pl.Base)
    if np.dot(n, toward) < 0:
        n, u = -n, u
    o = _project(picked, base, n) if picked is not None else base
    return Reference(o, n, u, label, kind)


def _contains_origin(top, sub):
    try:
        shape = Part.getShape(top, sub, needSubElement=True, noElementMap=True)
        return (not shape.isNull()) and shape.BoundBox.isInside(App.Vector(0, 0, 0))
    except Exception:
        return False


# ----------------------------------------------------------------------
# Selection entries
# ----------------------------------------------------------------------
def from_selection(top, sub, picked, toward, current=None):
    """Reference for one selection entry (top object, full subname, picked point or None).

    `toward` is the unit direction from the scene to the camera; `current` an
    existing Reference whose orientation a picked vertex keeps.
    """
    toward = geom.unit(toward)
    path, elem = split_sub(sub)
    axes = frame_axes(top, path)
    if picked is not None and not np.any(picked) and (not elem or not _contains_origin(top, sub)):
        picked = None           # selections made without a 3D click report (0, 0, 0)
    try:
        leaf = top.getSubObject(path, retType=1) if path else top      # the object, not its shape
    except Exception:
        leaf = None
    if leaf is not None and not hasattr(leaf, "TypeId"):
        leaf = None
    name = leaf.Label if leaf is not None else top.Label
    label = "%s · %s" % (name, elem) if elem else name
    cur_n = current.normal if current is not None else None
    cur_u = current.u if current is not None else None

    if elem:
        try:
            shape = Part.getShape(top, sub, needSubElement=True, noElementMap=True)
        except Exception:
            shape = None
        if shape is None or shape.isNull():
            return None
        if elem.startswith("Face") and shape.Faces:
            return from_face(shape.Faces[0], picked, toward, axes, label)
        if elem.startswith("Edge") and shape.Edges:
            return from_edge(shape.Edges[0], picked, toward, axes, label)
        if elem.startswith("Vertex") and shape.Vertexes:
            return from_point(vec(shape.Vertexes[0].Point), toward, axes, cur_n, cur_u, label)
        return None

    # whole object: datums and sketches
    if leaf is None:
        return None
    tid = leaf.TypeId
    try:
        pl = top.getSubObject(path, retType=3) if path else top.getGlobalPlacement()
    except Exception:
        pl = getattr(leaf, "Placement", None)
    if pl is None:
        return None
    if tid in DATUM_PLANES or tid in SKETCHES or tid in FRAMES:
        kind = "sketch plane" if tid in SKETCHES else "XY plane" if tid in FRAMES else "datum plane"
        return from_placement(pl, picked, toward, label, kind)
    if tid in DATUM_LINES:
        d = vec(pl.Rotation.multVec(App.Vector(1, 0, 0)))
        n = geom.plane_through_axis(d, toward, axes)
        base = vec(pl.Base)
        return Reference(_project(picked, base, n) if picked is not None else base, n, d, label, "datum line")
    if tid in DATUM_POINTS:
        return from_point(vec(pl.Base), toward, axes, cur_n, cur_u, label)
    # other objects with a simple shape: a datum plane / line / point of PartDesign
    try:
        shape = Part.getShape(top, path, needSubElement=False, noElementMap=True)
    except Exception:
        shape = None
    if shape is not None and not shape.isNull() and not shape.Solids:
        if len(shape.Faces) == 1:
            return from_face(shape.Faces[0], picked, toward, axes, label)
        if len(shape.Edges) == 1 and not shape.Faces:
            return from_edge(shape.Edges[0], picked, toward, axes, label)
        if len(shape.Vertexes) == 1 and not shape.Edges:
            return from_point(vec(shape.Vertexes[0].Point), toward, axes, cur_n, cur_u, label)
    return None


def default_reference(center, toward, which=None):
    """Principal plane through center: the one facing the camera, or 'top'/'front'/'right'."""
    toward = geom.unit(toward)
    if which == "top":
        n = np.array((0, 0, 1.0))
    elif which == "front":
        n = np.array((0, -1.0, 0))
    elif which == "right":
        n = np.array((1.0, 0, 0))
    else:
        n = geom.snap_direction(toward, WORLD_AXES)
    if np.dot(n, toward) < 0:
        n = -n
    label = {"top": "Top plane (XY)", "front": "Front plane (XZ)", "right": "Right plane (YZ)"}.get(which)
    if label is None:
        ax = int(np.argmax(np.abs(n)))
        label = ("Right plane (YZ)", "Front plane (XZ)", "Top plane (XY)")[ax]
    return Reference(center, n, _best_inplane(n, WORLD_AXES), label, "default plane")
