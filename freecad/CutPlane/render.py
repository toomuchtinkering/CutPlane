# SPDX-License-Identifier: LGPL-2.1-or-later
"""Coin nodes: clip planes, section caps, plane outlines, excluded parts.

Layout under FreeCAD's SoFCUnifiedSelection (the node that holds every
object):

    [0] CutPlaneRoot (separator, so nothing in it is clipped)
          excluded   parts that are not sectioned, drawn again unclipped
          caps       one separator per plane, clipped by the *other* planes
          outline    plane outlines
          handles    the gizmo (added by gizmo.py)
    [1] CutPlaneClip (group, so the clip planes affect what follows)
    [2...] FreeCAD's own nodes

Coin keeps the side a clip plane's normal points to; the section normal
points to the removed side, so the clip plane gets the negated normal.
"""

import numpy as np
from pivy import coin

from . import geom

MAX_PLANES = 3
# The cut-face outline is a strip of triangles inside the face (geom.outline_strip),
# because some OpenGL drivers draw every line 1 px wide (VMware SVGA reports a line
# width range of 1..1); a 1 px line marks the exact edge on top of it. Depth order
# on the plane, back to front: fill, hatch lines, outline strip, overlaps. Polygon
# offset scales with the slope of a tilted cap, which the small lifts along the
# normal (eps) cannot cover.
FILL_OFFSET = (2.0, 1.0)            # (factor, units)
STRIP_OFFSET = (-1.0, -1.0)
OVERLAP_OFFSET = (-2.0, -1.0)


def _offset(factor_units):
    po = coin.SoPolygonOffset()
    po.factor, po.units = factor_units
    return po


def _sbvec(a):
    return coin.SbVec3f(float(a[0]), float(a[1]), float(a[2]))


_NUMPY_OK = [None]          # does this pivy accept numpy arrays in setValues?


def _set_points(coord_node, pts):
    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    coord_node.point.setNum(len(pts))
    if not len(pts):
        return
    if _NUMPY_OK[0] is not False:
        try:
            coord_node.point.setValues(0, len(pts), np.ascontiguousarray(pts, dtype=np.float32))
            if _NUMPY_OK[0] is None:            # check once that the values arrived
                v = coord_node.point[len(pts) - 1].getValue()
                _NUMPY_OK[0] = bool(np.allclose(v, pts[-1], rtol=1e-5, atol=1e-5 * (1 + abs(pts[-1]).max())))
                if not _NUMPY_OK[0]:
                    coord_node.point.setValues(0, len(pts), pts.tolist())
            return
        except Exception:
            _NUMPY_OK[0] = False
    coord_node.point.setValues(0, len(pts), pts.tolist())


def darker(c, f):
    return tuple(max(0.0, min(1.0, x * f)) for x in c)


def line_color(c, f=0.3):
    """Colour for hatch and outline lines on a cap of colour c: dark on light parts,
    light on dark ones, so the lines always show."""
    lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    if lum > 0.28:
        return darker(c, f)
    return tuple(min(1.0, 0.55 + 0.45 * x) for x in c)


class PlaneCaps:
    """Nodes of one plane's caps: fill, overlap fill, hatch, outline."""

    def __init__(self):
        self.sep = coin.SoSeparator()
        self.clips = coin.SoGroup()          # the other planes' clip nodes
        self.sep.addChild(self.clips)

        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
        hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE   # two-sided lighting
        self.sep.addChild(hints)

        # fill: one face set, one material per triangle
        fill = coin.SoSeparator()
        self.sep.addChild(fill)
        fill.addChild(_offset(FILL_OFFSET))
        self.normal = coin.SoNormal()
        fill.addChild(self.normal)
        nb = coin.SoNormalBinding()
        nb.value = coin.SoNormalBinding.OVERALL
        fill.addChild(nb)
        self.fill_mat = coin.SoMaterial()
        fill.addChild(self.fill_mat)
        mb = coin.SoMaterialBinding()
        mb.value = coin.SoMaterialBinding.PER_FACE_INDEXED
        fill.addChild(mb)
        self.fill_coords = coin.SoCoordinate3()
        fill.addChild(self.fill_coords)
        self.fill = coin.SoIndexedFaceSet()
        fill.addChild(self.fill)

        # lines and overlaps: unlit
        lines = coin.SoSeparator()
        self.sep.addChild(lines)
        lm = coin.SoLightModel()
        lm.model = coin.SoLightModel.BASE_COLOR
        lines.addChild(lm)

        ov = coin.SoSeparator()
        lines.addChild(ov)
        ov.addChild(_offset(OVERLAP_OFFSET))
        self.ov_color = coin.SoBaseColor()
        self.ov_color.rgb.setValue(0.95, 0.1, 0.1)
        ov.addChild(self.ov_color)
        self.ov_coords = coin.SoCoordinate3()
        ov.addChild(self.ov_coords)
        self.ov = coin.SoIndexedFaceSet()
        ov.addChild(self.ov)

        hatch = coin.SoSeparator()
        lines.addChild(hatch)
        hs = coin.SoDrawStyle()
        hs.lineWidth = 1.0
        hatch.addChild(hs)
        self.hatch_color = coin.SoBaseColor()
        hatch.addChild(self.hatch_color)
        hb = coin.SoMaterialBinding()
        hb.value = coin.SoMaterialBinding.PER_FACE        # for line sets: one colour per polyline
        hatch.addChild(hb)
        self.hatch_coords = coin.SoCoordinate3()
        hatch.addChild(self.hatch_coords)
        self.hatch = coin.SoLineSet()
        hatch.addChild(self.hatch)

        strip = coin.SoSeparator()
        lines.addChild(strip)
        strip.addChild(_offset(STRIP_OFFSET))
        self.strip_color = coin.SoBaseColor()
        strip.addChild(self.strip_color)
        sb = coin.SoMaterialBinding()
        sb.value = coin.SoMaterialBinding.PER_PART       # triangle strips: one colour per strip
        strip.addChild(sb)
        self.strip_coords = coin.SoCoordinate3()
        strip.addChild(self.strip_coords)
        self.strip = coin.SoTriangleStripSet()
        strip.addChild(self.strip)

        outl = coin.SoSeparator()
        lines.addChild(outl)
        self.outline_style = coin.SoDrawStyle()
        self.outline_style.lineWidth = 1.0
        outl.addChild(self.outline_style)
        self.outline_color = coin.SoBaseColor()
        outl.addChild(self.outline_color)
        ob = coin.SoMaterialBinding()
        ob.value = coin.SoMaterialBinding.PER_FACE        # PER_PART would be per segment
        outl.addChild(ob)
        self.outline_coords = coin.SoCoordinate3()
        outl.addChild(self.outline_coords)
        self.outline = coin.SoLineSet()
        outl.addChild(self.outline)
        self.clear()

    def clear(self):
        for c in (self.fill_coords, self.ov_coords, self.hatch_coords, self.strip_coords, self.outline_coords):
            c.point.setNum(0)
        for f in (self.fill, self.ov):
            f.coordIndex.setNum(0)
        self.fill.materialIndex.setNum(0)
        for ls in (self.hatch, self.strip, self.outline):
            ls.numVertices.setNum(0)

    def set_clips(self, clip_nodes):
        self.clips.removeAllChildren()
        for c in clip_nodes:
            self.clips.addChild(c)

    @staticmethod
    def _faces(coord_node, faceset, tris3d):
        m = len(tris3d)
        _set_points(coord_node, tris3d.reshape(-1, 3))
        idx = np.full((m, 4), -1, dtype=np.int64)
        idx[:, :3] = np.arange(3 * m).reshape(m, 3)
        faceset.coordIndex.setNum(4 * m)
        if m:
            faceset.coordIndex.setValues(0, 4 * m, idx.reshape(-1).tolist())

    def show(self, normal, fills, overlap, hatches, outlines, strips=()):
        """fills: [(tris3d (m,3,3), color)], overlap: tris3d, hatches: [(segs3d (k,2,3), color)],
        outlines: [(loops3d list, color)], strips: [(points3d (N,3), counts, color)], the wide
        outline as triangle strips (geom.outline_strip)."""
        self.normal.vector.setValue(_sbvec(normal))
        tris = [t for t, _ in fills if len(t)]
        if tris:
            allt = np.concatenate(tris)
            self._faces(self.fill_coords, self.fill, allt)
            colors = [c for _, c in fills]
            # lit with the plane normal like a face, plus some ambient and emissive light
            # so a cap turned away from the light still reads clearly
            for field, f in ((self.fill_mat.diffuseColor, 1.0), (self.fill_mat.ambientColor, 0.35),
                             (self.fill_mat.emissiveColor, 0.3)):
                field.setNum(len(colors))
                field.setValues(0, len(colors), [[x * f for x in c] for c in colors])
            self.fill_mat.specularColor.setNum(len(colors))
            self.fill_mat.specularColor.setValues(0, len(colors), [[0.08, 0.08, 0.08]] * len(colors))
            mi = np.concatenate([np.full(len(t), k) for k, (t, _) in enumerate(fills) if len(t)])
            self.fill.materialIndex.setNum(len(mi))
            self.fill.materialIndex.setValues(0, len(mi), mi.tolist())
        else:
            self.fill_coords.point.setNum(0)
            self.fill.coordIndex.setNum(0)
            self.fill.materialIndex.setNum(0)

        self._faces(self.ov_coords, self.ov, overlap if overlap is not None else np.zeros((0, 3, 3)))

        segs, cols = [], []
        for s, c in hatches:
            if len(s):
                segs.append(s)
                cols += [c] * len(s)
        if segs:
            alls = np.concatenate(segs)
            _set_points(self.hatch_coords, alls.reshape(-1, 3))
            self.hatch.numVertices.setNum(len(alls))
            self.hatch.numVertices.setValues(0, len(alls), [2] * len(alls))
            self.hatch_color.rgb.setNum(len(cols))
            self.hatch_color.rgb.setValues(0, len(cols), [list(c) for c in cols])
        else:
            self.hatch_coords.point.setNum(0)
            self.hatch.numVertices.setNum(0)

        strips = [(V, n, c) for V, n, c in strips if len(n)]
        if strips:
            _set_points(self.strip_coords, np.concatenate([V for V, _, _ in strips]))
            counts = np.concatenate([n for _, n, _ in strips]).tolist()
            self.strip.numVertices.setNum(len(counts))
            self.strip.numVertices.setValues(0, len(counts), counts)
            cols = [list(c) for _, n, c in strips for _ in range(len(n))]
            self.strip_color.rgb.setNum(len(cols))
            self.strip_color.rgb.setValues(0, len(cols), cols)
        else:
            self.strip_coords.point.setNum(0)
            self.strip.numVertices.setNum(0)

        pts, counts, ocols = [], [], []
        for loops, c in outlines:
            for L in loops:
                if len(L) < 2:
                    continue
                pts.append(np.concatenate((L, L[:1])))
                counts.append(len(L) + 1)
                ocols.append(c)
        if pts:
            _set_points(self.outline_coords, np.concatenate(pts))
            self.outline.numVertices.setNum(len(counts))
            self.outline.numVertices.setValues(0, len(counts), counts)
            self.outline_color.rgb.setNum(len(ocols))
            self.outline_color.rgb.setValues(0, len(ocols), [list(c) for c in ocols])
        else:
            self.outline_coords.point.setNum(0)
            self.outline.numVertices.setNum(0)


class Renderer:
    def __init__(self, sel_root):
        self.sel = sel_root
        self.root = coin.SoSeparator()
        self.root.setName("CutPlaneRoot")
        self.excluded = coin.SoSeparator()
        self.caps_root = coin.SoSeparator()
        self.outline_root = coin.SoSeparator()
        self.handles = coin.SoSeparator()
        for n in (self.excluded, self.caps_root, self.outline_root, self.handles):
            self.root.addChild(n)

        self.clip_group = coin.SoGroup()
        self.clip_group.setName("CutPlaneClip")
        self.clips = []
        for _ in range(MAX_PLANES):
            c = coin.SoClipPlane()
            c.on = False
            self.clips.append(c)
            self.clip_group.addChild(c)

        self.caps = []
        for _ in range(MAX_PLANES):
            pc = PlaneCaps()
            self.caps.append(pc)
            self.caps_root.addChild(pc.sep)

        self._build_outline()
        self._excluded_keys = None
        self.sel.insertChild(self.clip_group, 0)
        self.sel.insertChild(self.root, 0)

    def _build_outline(self):
        lm = coin.SoLightModel()
        lm.model = coin.SoLightModel.BASE_COLOR
        self.outline_root.addChild(lm)
        pick = coin.SoPickStyle()
        pick.style = coin.SoPickStyle.UNPICKABLE
        self.outline_root.addChild(pick)
        ds = coin.SoDrawStyle()
        ds.lineWidth = 1.0
        ds.linePattern = 0xF0F0
        self.outline_root.addChild(ds)
        self.outline_color = coin.SoBaseColor()
        self.outline_color.rgb.setValue(1.0, 0.55, 0.0)
        self.outline_root.addChild(self.outline_color)
        self.outline_coords = coin.SoCoordinate3()
        self.outline_root.addChild(self.outline_coords)
        self.outline_lines = coin.SoLineSet()
        self.outline_root.addChild(self.outline_lines)
        self.outline_lines.numVertices.setNum(0)

    def remove(self):
        for n in (self.root, self.clip_group):
            try:
                idx = self.sel.findChild(n)
                if idx >= 0:
                    self.sel.removeChild(idx)
            except Exception:
                pass

    # -- clip planes -------------------------------------------------------
    def set_planes(self, planes, slack):
        """planes: objects with origin, n. slack moves each plane a hair to the removed side."""
        for k, c in enumerate(self.clips):
            if k < len(planes):
                p = planes[k]
                d = float(np.dot(p.n, p.origin)) + slack
                c.plane.setValue(coin.SbPlane(_sbvec(-p.n), -d))
                c.on = True
            else:
                c.on = False
        for k, pc in enumerate(self.caps):
            pc.set_clips([self.clips[j] for j in range(len(planes)) if j != k])
            if k >= len(planes):
                pc.clear()

    # -- outlines of the planes --------------------------------------------
    def set_outlines(self, polys):
        """polys: list of (k, 3) closed polygons (the plane inside the scene box)."""
        pts, counts = [], []
        for P in polys:
            if len(P) >= 3:
                pts.append(np.concatenate((P, P[:1])))
                counts.append(len(P) + 1)
        if pts:
            _set_points(self.outline_coords, np.concatenate(pts))
            self.outline_lines.numVertices.setNum(len(counts))
            self.outline_lines.numVertices.setValues(0, len(counts), counts)
        else:
            self.outline_coords.point.setNum(0)
            self.outline_lines.numVertices.setNum(0)

    # -- excluded parts ------------------------------------------------------
    def set_excluded(self, items):
        """items: [(key, node, 4x4 matrix)] parts drawn unclipped over their clipped original."""
        keys = tuple((k, round(float(M.sum()), 9)) for k, _, M in items)
        if keys == self._excluded_keys:
            return
        self._excluded_keys = keys
        self.excluded.removeAllChildren()
        for _key, node, M in items:
            sep = coin.SoSeparator()
            mt = coin.SoMatrixTransform()
            mt.matrix.setValue(coin.SbMatrix(*[float(x) for x in M.T.reshape(-1)]))
            sep.addChild(mt)
            sep.addChild(node)
            self.excluded.addChild(sep)


def box_section(lo, hi, origin, n, grow=0.04):
    """Polygon where the plane cuts the box [lo, hi] (grown a little), or an empty array."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    pad = (hi - lo) * grow + 1e-9
    lo, hi = lo - pad, hi + pad
    n = geom.unit(n)
    d = float(np.dot(n, origin))
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    s = corners @ n - d
    edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
    pts = []
    for a, b in edges:
        if (s[a] >= 0) != (s[b] >= 0):
            t = s[a] / (s[a] - s[b])
            pts.append(corners[a] + t * (corners[b] - corners[a]))
    if len(pts) < 3:
        return np.zeros((0, 3))
    P = np.array(pts)
    c = P.mean(axis=0)
    u, v = geom.frame_from_normal(n)
    ang = np.arctan2((P - c) @ v, (P - c) @ u)
    return P[np.argsort(ang)]
