# SPDX-License-Identifier: LGPL-2.1-or-later
"""A live section view in one 3D view: planes, picking, caps, handles, panel.

Nothing here touches the document: the section is Coin nodes only, so it
never marks a document modified or adds to the undo stack.
"""

import math
import time

import numpy as np
from PySide import QtCore, QtGui

import FreeCAD as App
import FreeCADGui as Gui

from . import geom, gizmo, prefs, reference, render, scene
from .prefs import PARAMS

HATCH_ANGLES = (45.0, 135.0, 15.0, 105.0, 75.0, 165.0)
MAX_PLANES = render.MAX_PLANES
DRAG_INTERVAL_MS = 15
IDLE_INTERVAL_MS = 40
SNAP_RADIUS_PX = 6          # pick radius when looking for snap points
HATCH_STRETCH = 3.0         # automatic hatch on a plane seen at an angle: at most this
                            # many times the spacing it has seen face-on


class SectionPlane:
    """Current frame of one plane plus the reference it was placed from."""

    def __init__(self, ref):
        self.ref = ref
        self.origin = ref.origin.copy()
        self.n = ref.normal.copy()
        self.u = ref.u.copy()

    @property
    def v(self):
        return np.cross(self.n, self.u)

    @property
    def label(self):
        return self.ref.label

    def copy(self):
        c = SectionPlane.__new__(SectionPlane)
        c.ref, c.origin, c.n, c.u = self.ref, self.origin.copy(), self.n.copy(), self.u.copy()
        return c

    def assign(self, other):
        self.ref, self.origin, self.n, self.u = other.ref, other.origin.copy(), other.n.copy(), other.u.copy()

    def offset(self):
        """Distance of the plane from its reference point, along the normal (the handle's stem)."""
        return float(self.n @ (self.origin - self.ref.origin))

    def set_offset(self, val):
        self.origin = self.origin + self.n * (val - self.offset())

    def tilt(self):
        c = float(np.clip(abs(self.n @ self.ref.normal), -1.0, 1.0))
        return math.degrees(math.acos(c))

    def flip(self):
        self.n = -self.n

    def reset(self):
        self.origin, self.n, self.u = self.ref.origin.copy(), self.ref.normal.copy(), self.ref.u.copy()


class Options:
    FIELDS = {
        "fill_mode": ("part", str),            # "part" colour or "single" colour
        "single_color": ("0.62,0.63,0.66", str),
        "hatch": (True, bool),
        "hatch_spacing": (0.0, float),         # 0 = automatic
        "outline": (True, bool),
        "interference": (True, bool),
        "show_plane": (True, bool),
    }

    def __init__(self):
        p = App.ParamGet(PARAMS)
        for name, (default, typ) in self.FIELDS.items():
            if typ is bool:
                val = p.GetBool(name, default)
            elif typ is float:
                val = p.GetFloat(name, default)
            else:
                val = p.GetString(name, default)
            setattr(self, name, val)

    def save(self):
        p = App.ParamGet(PARAMS)
        for name, (_default, typ) in self.FIELDS.items():
            val = getattr(self, name)
            if typ is bool:
                p.SetBool(name, bool(val))
            elif typ is float:
                p.SetFloat(name, float(val))
            else:
                p.SetString(name, str(val))

    def single_rgb(self):
        try:
            return tuple(float(x) for x in self.single_color.split(","))[:3]
        except Exception:
            return (0.62, 0.63, 0.66)


# ----------------------------------------------------------------------
# Observers
# ----------------------------------------------------------------------
class _ParamObserver:
    """Edit > Preferences > CutPlane (or the parameter editor) changed a setting."""

    def __init__(self, session):
        self.s = session

    def onChange(self, _grp, name):
        try:
            self.s.on_param(name)
        except Exception as e:
            App.Console.PrintError("CutPlane settings: %s\n" % e)


class _SelectionObserver:
    def __init__(self, s):
        self.s = s

    def addSelection(self, doc, obj, sub, pnt):
        self.s.on_selection()


class _AppObserver:
    def __init__(self, s):
        self.s = s

    def slotChangedObject(self, obj, prop):
        self.s.mark_dirty()

    def slotCreatedObject(self, obj):
        self.s.mark_dirty()

    def slotDeletedObject(self, obj):
        self.s.mark_dirty()

    def slotRecomputedDocument(self, doc):
        self.s.mark_dirty()

    def slotDeletedDocument(self, doc):
        if self.s.doc_name == getattr(doc, "Name", None):
            QtCore.QTimer.singleShot(0, self.s.close)


class _GuiObserver:
    def __init__(self, s):
        self.s = s

    def slotChangedObject(self, vp, prop):
        self.s.mark_dirty()


# ----------------------------------------------------------------------
# Session
# ----------------------------------------------------------------------
class Session:
    current = None

    @classmethod
    def toggle(cls):
        view = Gui.ActiveDocument.ActiveView if Gui.ActiveDocument else None
        if cls.current is not None:
            same = cls.current.view is view
            cls.current.close()
            if same:
                return None
        if view is None or not hasattr(view, "getSceneGraph"):
            App.Console.PrintWarning("CutPlane: the active view is not a 3D view\n")
            return None
        s = cls(view)
        s.start()
        return s

    @classmethod
    def edit(cls):
        if cls.current is not None:
            cls.current.show_panel(True)

    def __init__(self, view):
        from . import panel                      # Qt widgets: imported late
        Session.current = self
        self.view = view
        self.gdoc = Gui.ActiveDocument
        self.doc_name = self.gdoc.Document.Name
        self.closed = False
        self.opts = Options()
        self.planes = []
        self.active = 0
        self.include, self.exclude = [], []
        self.pick_mode = "align"
        self.instances, self.parts = [], {}
        self.sectioned = set()
        self.scene_dirty = True
        self.lo = self.hi = np.zeros(3)
        self.diag = 1.0
        self.style_pref = {}
        self._cap_cache = {}            # (plane, part) -> (outline key, region, triangles)
        self._hatch_cache = {}          # (plane, part) -> ((outline key, angle, spacing), segments)
        self._strip_cache = {}          # (plane, part) -> ((outline key, metric key, width), triangles)
        self.dragging = False
        self.status = ""
        self.cut_keys = {}              # plane index -> part keys cut by it (last update)
        self.last_update_ms = 0.0
        self._pick_pending = False

        self.renderer = render.Renderer(view.getSceneGraph())
        self.scene = scene.SceneMeshes(view, skip_nodes=[self.renderer.root])
        self.gizmo = gizmo.Gizmo(self, view, self.renderer.handles)

        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._update_caps)

        self.panel = panel.SectionPanel(self)
        self._sel_obs = _SelectionObserver(self)
        self._app_obs = _AppObserver(self)
        self._gui_obs = _GuiObserver(self)
        Gui.Selection.addObserver(self._sel_obs)
        App.addDocumentObserver(self._app_obs)
        Gui.addDocumentObserver(self._gui_obs)
        self._params = prefs.params()
        self._param_obs = _ParamObserver(self)
        self._params.Attach(self._param_obs)
        try:
            view.graphicsView().destroyed.connect(self._view_destroyed)
        except Exception:
            pass

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        self._harvest()
        toward = self.toward()
        ref, include = None, []
        for s in Gui.Selection.getSelectionEx("", 0):
            subs = s.SubElementNames or ("",)
            pts = s.PickedPoints
            for k, sub in enumerate(subs):
                pp = reference.vec(pts[k]) if k < len(pts) else None
                r = reference.from_selection(s.Object, sub, pp, toward)
                if r is not None:
                    ref = ref or r
                elif not reference.split_sub(sub)[1]:
                    key = self.part_key_for(s.Object, sub)
                    if key is not None and key not in include:
                        include.append(key)
        self.include = include
        if ref is None:
            ref = self._default_ref(include)
        self.planes = [SectionPlane(ref)]
        self.active = 0
        Gui.Selection.clearSelection()
        self._apply_filters()
        self.show_panel(True)
        self.plane_changed()

    def close(self):
        if self.closed:
            return
        self.closed = True
        for fn in (lambda: self._params.Detach(self._param_obs),
                   lambda: Gui.Selection.removeObserver(self._sel_obs),
                   lambda: App.removeDocumentObserver(self._app_obs),
                   lambda: Gui.removeDocumentObserver(self._gui_obs),
                   self.timer.stop, self.gizmo.remove, self.renderer.remove, self.panel.dispose):
            try:
                fn()
            except Exception:
                pass
        if Session.current is self:
            Session.current = None

    def _view_destroyed(self, *_args):
        self.closed = True              # the scene graph is gone with the view
        for fn in (lambda: self._params.Detach(self._param_obs),
                   lambda: Gui.Selection.removeObserver(self._sel_obs),
                   lambda: App.removeDocumentObserver(self._app_obs),
                   lambda: Gui.removeDocumentObserver(self._gui_obs),
                   self.timer.stop, self.panel.dispose):
            try:
                fn()
            except Exception:
                pass
        if Session.current is self:
            Session.current = None

    def show_panel(self, flag):
        self.panel.setVisible(flag)
        self.gizmo.set_visible(flag)
        self._update_outlines()

    @property
    def editing(self):
        return not self.closed and self.panel.isVisible()

    # -- helpers -------------------------------------------------------------
    def toward(self):
        """Unit direction from the scene to the camera."""
        return -geom.unit(reference.vec(self.view.getViewDirection()))

    @property
    def slack(self):
        """Planes sit this far toward the removed side, so a face lying in the
        plane (right after picking it) stays visible and is not cut."""
        return self.diag * 1e-6

    def part_key_for(self, top, sub):
        """Part key (doc, name) of the first non-container object on a selection path."""
        try:
            objs = top.getSubObjectList(reference.split_sub(sub)[0])
        except Exception:
            objs = [top]
        for o in objs:
            if not scene.SceneMeshes._is_container(o):
                return (o.Document.Name, o.Name)
        return None

    def _default_ref(self, keys=(), which=None):
        lo, hi = self.lo, self.hi
        if keys:
            sel = [i for i in self.instances if i.part in keys]
            if sel:
                lo = np.min([i.lo for i in sel], axis=0)
                hi = np.max([i.hi for i in sel], axis=0)
        return reference.default_reference(0.5 * (lo + hi), self.toward(), which)

    def offset_text(self, p):
        return "%.2f mm" % p.offset()

    # -- scene ---------------------------------------------------------------
    def mark_dirty(self):
        if self.closed:
            return
        self.scene_dirty = True
        self._schedule(IDLE_INTERVAL_MS)

    def _harvest(self):
        t0 = time.perf_counter()
        self.instances, self.parts = self.scene.harvest()
        if self.instances:
            self.lo = np.min([i.lo for i in self.instances], axis=0)
            self.hi = np.max([i.hi for i in self.instances], axis=0)
            self.diag = max(float(np.linalg.norm(self.hi - self.lo)), 1e-6)
        self.scene_dirty = False
        self._tol_cache = {}
        self._cap_cache.clear()        # outlines are keyed by geometry, but parts may have moved
        self._hatch_cache.clear()
        self._strip_cache.clear()
        self.harvest_ms = (time.perf_counter() - t0) * 1e3

    def _apply_filters(self):
        keys = set(self.parts)
        if self.include:
            self.sectioned = keys & set(self.include)
        else:
            self.sectioned = keys - set(self.exclude)
        items = []
        for key in sorted(keys - self.sectioned):
            info = self.parts[key]
            if info.path is None:
                continue
            items.append((key, info.node, self.scene.part_parent_matrix(info)))
        self.renderer.set_excluded(items)

    def _tess_tol(self, key):
        """How far a part's display mesh may stray from its true surface. FreeCAD
        tessellates with a chord deviation of (dx + dy + dz) / 300 x the view
        provider's Deviation (a percentage). 0 for meshes and anything else."""
        cache = getattr(self, "_tol_cache", None)
        if cache is None:
            cache = self._tol_cache = {}
        tol = cache.get(key)
        if tol is None:
            tol = 0.0
            info = self.parts.get(key)
            obj = getattr(info, "obj", None)
            try:
                src = obj.getLinkedObject(True)
                bb = src.Shape.BoundBox
                tol = (bb.XLength + bb.YLength + bb.ZLength) / 300.0 * float(src.ViewObject.Deviation)
            except Exception:
                tol = 0.0
            cache[key] = tol
        return tol

    def part_label(self, key):
        info = self.parts.get(key)
        if info is not None:
            return info.label
        try:
            return App.getDocument(key[0]).getObject(key[1]).Label
        except Exception:
            return key[1]

    # -- planes --------------------------------------------------------------
    def plane_changed(self, quick=False):
        if self.closed:
            return
        self.renderer.set_planes(self.planes, self.slack)
        self.gizmo.refresh()
        self._update_outlines()
        self.panel.refresh_values()
        self._schedule(DRAG_INTERVAL_MS if quick else 0)

    def _schedule(self, ms):
        if not self.timer.isActive():
            self.timer.start(ms)

    def drag_started(self):
        self.dragging = True

    def drag_finished(self):
        self.dragging = False
        self.plane_changed()

    def set_active(self, idx):
        if 0 <= idx < len(self.planes):
            self.active = idx
            self.panel.refresh_planes()
            self.plane_changed()

    def add_plane(self, ref=None):
        if len(self.planes) >= MAX_PLANES:
            return False
        if ref is None:
            ref = self._new_plane_ref()
        self.planes.append(SectionPlane(ref))
        self.active = len(self.planes) - 1
        self.panel.refresh_planes()
        self.plane_changed()
        return True

    def _new_plane_ref(self):
        """A principal plane through the scene centre, as perpendicular as possible to the others."""
        used = [p.n for p in self.planes]
        toward = self.toward()
        best, best_score = None, -1e9
        for ax in reference.WORLD_AXES:
            for s in (1.0, -1.0):
                n = ax * s
                score = float(n @ toward) - 2.0 * sum(abs(float(n @ m)) for m in used)
                if score > best_score:
                    best, best_score = n, score
        c = 0.5 * (self.lo + self.hi)
        return reference.Reference(c, best, None, "New plane", "default plane")

    def remove_plane(self):
        if len(self.planes) <= 1:
            return
        del self.planes[self.active]
        self.active = min(self.active, len(self.planes) - 1)
        self.panel.refresh_planes()
        self.plane_changed()

    def set_reference(self, ref, add=False):
        if add and self.add_plane(ref):
            return
        self.planes[self.active] = SectionPlane(ref)
        self.panel.refresh_planes()
        self.plane_changed()

    def flip_active(self):
        self.planes[self.active].flip()
        self.plane_changed()

    def reset_active(self):
        self.planes[self.active].reset()
        self.plane_changed()

    def default_plane(self, which):
        self.set_reference(self._default_ref(self.include, which))

    def view_normal(self):
        """Look at the active cut face straight on (from the removed side)."""
        p = self.planes[self.active]
        m = App.Matrix()
        u, v, n = p.u, p.v, p.n
        m.A11, m.A21, m.A31 = float(u[0]), float(u[1]), float(u[2])
        m.A12, m.A22, m.A32 = float(v[0]), float(v[1]), float(v[2])
        m.A13, m.A23, m.A33 = float(n[0]), float(n[1]), float(n[2])
        self.view.setCameraOrientation(App.Rotation(m))

    def inference_point(self, x, y, widget):
        """Snap point under the widget pixel (x, y): vertex, edge midpoint or circle centre."""
        dpr = widget.devicePixelRatioF() if hasattr(widget, "devicePixelRatioF") else 1.0
        cx, cy = int(x * dpr), int((widget.height() - 1 - y) * dpr)
        try:
            info = self.view.getObjectInfo((cx, cy), int(SNAP_RADIUS_PX * dpr))
        except Exception:
            info = None
        if not info:
            return None
        cands = self._snap_candidates(info)
        if not cands:
            return None
        g = self.gizmo
        vv = g._vv()
        m = np.array((x, y))
        best, best_d = None, gizmo.SNAP_PX
        for c in cands:
            d = float(np.linalg.norm(g.to_screen(c, vv) - m))
            if d < best_d:
                best, best_d = c, d
        return best

    def _snap_candidates(self, info):
        import Part
        try:
            doc = App.getDocument(info["Document"])
            if info.get("ParentObject") is not None:
                top = info["ParentObject"]
                top = doc.getObject(top) if isinstance(top, str) else top
                sub = info.get("SubName", "")
            else:
                top = doc.getObject(info["Object"])
                sub = info.get("Component", "")
            elem = reference.split_sub(sub)[1]
            if not elem:
                return []
            # geometry only: with an element map, reading Vertexes/Edges of a part linked from
            # another file prints "hasher mismatch" for every sub-shape (and is slower)
            shape = Part.getShape(top, sub, needSubElement=True, noElementMap=True)
        except Exception:
            return []
        out = []
        vec = reference.vec
        if elem.startswith("Vertex"):
            out = [vec(v.Point) for v in shape.Vertexes]
        elif elem.startswith("Edge") and shape.Edges:
            e = shape.Edges[0]
            out = [vec(v.Point) for v in e.Vertexes]
            try:
                out.append(vec(e.valueAt(0.5 * (e.FirstParameter + e.LastParameter))))
                if hasattr(e.Curve, "Center"):
                    out.append(vec(e.Curve.Center))
            except Exception:
                pass
        elif elem.startswith("Face") and shape.Faces:
            f = shape.Faces[0]
            for e in f.Edges:
                c = getattr(e.Curve, "Center", None)
                if c is not None and type(e.Curve).__name__ in ("Circle", "ArcOfCircle"):
                    out.append(vec(c))
            out += [vec(v.Point) for v in f.Vertexes]
        return out

    # -- picking ---------------------------------------------------------------
    def on_selection(self):
        if not self.editing or self._pick_pending:
            return
        self._pick_pending = True
        QtCore.QTimer.singleShot(0, self._process_pick)

    def _process_pick(self):
        self._pick_pending = False
        if not self.editing:
            return
        sel = Gui.Selection.getSelectionEx("", 0)
        if not sel:
            return
        last = sel[-1]
        subs = last.SubElementNames or ("",)
        sub = subs[-1]
        pts = last.PickedPoints
        pp = reference.vec(pts[-1]) if pts else None
        ctrl = bool(QtGui.QApplication.keyboardModifiers() & QtCore.Qt.ControlModifier)
        if self.pick_mode == "align":
            cur = self.planes[self.active] if self.planes else None
            cur_ref = reference.Reference(cur.origin, cur.n, cur.u) if cur is not None else None
            ref = reference.from_selection(last.Object, sub, pp, self.toward(), cur_ref)
            if ref is not None:
                self.set_reference(ref, add=ctrl)
        elif self.pick_mode in ("include", "exclude"):
            key = self.part_key_for(last.Object, sub)
            if key is not None:
                lst = self.include if self.pick_mode == "include" else self.exclude
                if key in lst:
                    lst.remove(key)
                else:
                    lst.append(key)
                self._apply_filters()
                self.panel.refresh_lists()
                self.plane_changed()
        Gui.Selection.clearSelection()

    def clear_list(self, which):
        (self.include if which == "include" else self.exclude).clear()
        self._apply_filters()
        self.panel.refresh_lists()
        self.plane_changed()

    # -- outlines and caps -------------------------------------------------------
    def _update_outlines(self):
        polys = []
        if self.editing and self.opts.show_plane and self.planes and self.instances:
            p = self.planes[self.active]
            polys.append(render.box_section(self.lo, self.hi, p.origin, p.n))
        self.renderer.set_outlines(polys)

    def hatch_spacing(self):
        """A representative spacing (the offset field's step): the fixed spacing if set,
        else the automatic one for the active plane seen face-on."""
        if self.opts.hatch_spacing > 0:
            return self.opts.hatch_spacing
        try:
            wpp = self.gizmo.world_per_px(self.planes[self.active].origin)
        except Exception:
            wpp = self.diag / 800.0
        return geom.nice_hatch(prefs.hatch_px() * wpp)

    def hatch_spacings(self, k):
        """Hatch spacing in mm for plane k, per hatch angle. Automatic: the lines are
        prefs.hatch_px() apart on screen, measured across them, so a plane seen at an angle
        is not hatched denser than one seen face-on; rounded with geom.nice_hatch."""
        if self.opts.hatch_spacing > 0:
            return {a: self.opts.hatch_spacing for a in HATCH_ANGLES}
        target = prefs.hatch_px()
        p = self.planes[k]
        try:
            P = self.gizmo.plane_px_per_mm(p.origin, p.u, p.v)
            out = {}
            for a in HATCH_ANGLES:
                s = geom.hatch_px_per_mm(P, a, HATCH_STRETCH)
                out[a] = geom.nice_hatch(target / s) if s > 0 else self.hatch_spacing()
            return out
        except Exception:
            return {a: self.hatch_spacing() for a in HATCH_ANGLES}

    def plane_metric(self, k):
        """(A, key) of geom.screen_metric for plane k: plane mm -> screen px, in steps."""
        p = self.planes[k]
        try:
            return geom.screen_metric(self.gizmo.plane_px_per_mm(p.origin, p.u, p.v), HATCH_STRETCH)
        except Exception:
            s = 800.0 / self.diag
            return geom.screen_metric(np.diag((s, s)))

    def _view_signature(self):
        """What the zoom and the view direction change in the caps: the automatic hatch
        spacings and the outline strips' metric, per plane."""
        sig = []
        for k in range(len(self.planes)):
            h = tuple(sorted(self.hatch_spacings(k).items())) \
                if self.opts.hatch and self.opts.hatch_spacing <= 0 else None
            m = self.plane_metric(k)[1] if self.opts.outline else None
            sig.append((h, m))
        return tuple(sig)

    def on_scale_changed(self):
        """Zoom or view direction changed (from the handles' render callback): update the
        caps if a hatch spacing or an outline strip changed."""
        if not self.closed and (self.opts.hatch or self.opts.outline) \
                and self._view_signature() != getattr(self, "_view_used", None):
            self._schedule(IDLE_INTERVAL_MS)

    def on_param(self, name):
        """A setting changed in Edit > Preferences (or our own panel saved its options)."""
        if self.closed:
            return
        if name == prefs.HANDLE_SIZE:
            self.gizmo.set_size(prefs.handle_px())
        elif name == prefs.BALL_SIZE:
            self.gizmo.set_ball(prefs.ball_px())
        elif name in (prefs.OUTLINE_WIDTH, prefs.HATCH_PX):
            self._schedule(IDLE_INTERVAL_MS)
        elif name == prefs.HATCH_SPACING:
            v = max(0.0, self._params.GetFloat(name, 0.0))
            if abs(v - self.opts.hatch_spacing) > 1e-12:       # Options.save writes it back unchanged
                self.opts.hatch_spacing = v
                self.panel.refresh_options()
                self.plane_changed()

    def _styles(self, keys, regions):
        for k in keys:
            if k not in self.style_pref:
                self.style_pref[k] = len(self.style_pref) % len(HATCH_ANGLES)
        pairs = geom.touching_pairs(regions, self.diag * 1e-4)
        nb = [set() for _ in keys]
        for i, j in pairs:
            nb[i].add(j)
            nb[j].add(i)
        style = [-1] * len(keys)
        for i in range(len(keys)):           # keys are sorted: stable choice
            used = {style[j] for j in nb[i] if style[j] >= 0}
            pref = self.style_pref[keys[i]]
            style[i] = next(((pref + s) % len(HATCH_ANGLES) for s in range(len(HATCH_ANGLES))
                             if (pref + s) % len(HATCH_ANGLES) not in used), pref)
        return style

    def _update_caps(self):
        if self.closed:
            return
        t0 = time.perf_counter()
        try:
            if self.scene_dirty:
                self._harvest()
                self._apply_filters()
                self._update_outlines()
            insts = [i for i in self.instances if i.part in self.sectioned]
            eps = self.diag * 2e-5
            self._view_used = self._view_signature()
            width = prefs.outline_px()
            single = self.opts.single_rgb()
            status = []
            q = self.diag * 1e-9
            min_area = (self.diag * 1e-7) ** 2
            for k, p in enumerate(self.planes):
                o = p.origin + p.n * self.slack
                pu, pv, pn = p.u, p.v, p.n                    # v is a cross product: once per plane
                loops = geom.section_loops(insts, o, pn, pu, pv, gap=self.diag * 1e-6)
                anchor = geom.plane_anchor(o, p.n)
                spacings = self.hatch_spacings(k) if self.opts.hatch else {}
                A, akey = self.plane_metric(k) if self.opts.outline else (None, None)
                entries = []
                for key in sorted(loops):
                    l2d, l3d = loops[key]
                    clean = geom.clean_loops(l2d, 1e-9)
                    lk = geom.loops_key(clean, q)
                    hit = self._cap_cache.get((k, key))
                    if hit is not None and hit[0] == lk:      # same outline as last time (prismatic parts)
                        region, tris = hit[1], hit[2]
                    else:
                        region = geom.region_from_loops(clean, min_area)
                        tris = geom.triangulate(region)
                        self._cap_cache[(k, key)] = (lk, region, tris)
                    if not region.is_empty:
                        entries.append((key, lk, clean, l3d, region, tris))
                keys = [e[0] for e in entries]
                self.cut_keys[k] = keys
                regions = [e[4] for e in entries]
                styles = self._styles(keys, regions) if keys else []
                fills, hatches, outlines, strips = [], [], [], []
                for (key, lk, clean, l3d, region, tris), st in zip(entries, styles):
                    info = self.parts.get(key)
                    color = info.color if (info is not None and self.opts.fill_mode == "part") else single
                    fills.append((geom.to_3d(tris, anchor, pu, pv, eps, pn), color))
                    if self.opts.hatch:
                        angle = HATCH_ANGLES[st]
                        spacing = spacings[angle]
                        hc = self._hatch_cache.get((k, key))
                        if hc is not None and hc[0] == (lk, angle, spacing):
                            segs = hc[1]
                        else:
                            segs = geom.hatch_from_loops(clean, angle, spacing)
                            self._hatch_cache[(k, key)] = ((lk, angle, spacing), segs)
                        hatches.append((geom.to_3d(segs, anchor, pu, pv, 3 * eps, pn),
                                        render.line_color(color, 0.35)))
                    if self.opts.outline:
                        ocol = render.line_color(color, 0.12)
                        outlines.append(([L + pn * (3 * eps) for L in l3d], ocol))
                        sc = self._strip_cache.get((k, key))
                        if sc is not None and sc[0] == (lk, akey, width):
                            sv, sn = sc[1]
                        else:
                            sv, sn = geom.outline_strip(region, A, width)
                            self._strip_cache[(k, key)] = ((lk, akey, width), (sv, sn))
                        strips.append((geom.to_3d(sv, anchor, pu, pv, 3 * eps, pn), sn, ocol))
                ov_tris = None
                if self.opts.interference and len(regions) > 1:
                    ov, pairs = geom.overlaps(regions, min_area=(self.diag * 1e-5) ** 2,
                                              tols=[self._tess_tol(key) for key in keys])
                    if not ov.is_empty:
                        ov_tris = geom.to_3d(geom.triangulate(ov), anchor, pu, pv, 4 * eps, pn)   # over the outlines
                        names = sorted({"%s ↔ %s" % (self.part_label(keys[i]), self.part_label(keys[j]))
                                        for i, j in pairs})
                        status.append("overlap: " + ", ".join(names))
                self.renderer.caps[k].show(p.n, fills, ov_tris, hatches, outlines, strips)
                if k == self.active:
                    status.insert(0, "%d part%s cut" % (len(keys), "" if len(keys) == 1 else "s"))
            self.status = " · ".join(status)
        except Exception as e:
            import traceback
            App.Console.PrintError("CutPlane: %s\n%s" % (e, traceback.format_exc()))
        self.last_update_ms = (time.perf_counter() - t0) * 1e3
        self.panel.refresh_status()

