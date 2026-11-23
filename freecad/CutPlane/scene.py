# SPDX-License-Identifier: LGPL-2.1-or-later
"""The meshes FreeCAD displays, read from the Coin scene graph.

Reading the scene instead of the document gives exactly what is on screen:
visibility, display mode, links, containers, assemblies, sub-assemblies and
any workbench's objects come for free, with their world transforms. Vertex
data is cached by Coin node id, which changes whenever a node is modified,
so an unchanged scene costs one search action per harvest.
"""

import numpy as np
from pivy import coin

import FreeCAD as App
import FreeCADGui as Gui

from . import geom

CONTAINERS = {
    "App::Part", "App::DocumentObjectGroup", "App::LinkGroup", "App::Origin",
    "Assembly::AssemblyObject", "Assembly::AssemblyLink", "Assembly::JointGroup",
    "Assembly::ViewGroup", "Assembly::BomGroup",
}


def ptr(node):
    return int(node.this)


class PartInfo:
    """One sectionable part: the first non-container object on a mesh's scene path."""

    __slots__ = ("key", "obj", "label", "color", "node", "path_len", "path")

    def __init__(self, key, obj, label, color, node, path, path_len):
        self.key, self.obj, self.label, self.color = key, obj, label, color
        self.node, self.path, self.path_len = node, path, path_len


def _color_of(c):
    try:
        return tuple(float(x) for x in c.getValue())
    except Exception:
        return (0.8, 0.8, 0.8)


class SceneMeshes:
    def __init__(self, view, skip_nodes=()):
        self.view = view
        self.root = view.getSceneGraph()
        self.skip = {ptr(n) for n in skip_nodes}
        self._mesh_cache = {}           # (faceset, faceset id, coords, coords id) -> (V, T)
        self._root_map = {}
        self._root_map_docs = None

    # -- object lookup ---------------------------------------------------
    def _object_roots(self):
        docs = tuple(sorted(App.listDocuments()))
        sig = tuple((d, len(App.getDocument(d).Objects)) for d in docs)
        if sig != self._root_map_docs:
            m = {}
            for d in docs:
                gdoc = Gui.getDocument(d)
                if gdoc is None:
                    continue
                for o in App.getDocument(d).Objects:
                    vo = getattr(o, "ViewObject", None)
                    try:
                        if vo is not None and vo.RootNode is not None:
                            m[ptr(vo.RootNode)] = o
                    except Exception:
                        pass
            self._root_map, self._root_map_docs = m, sig
        return self._root_map

    @staticmethod
    def _is_container(obj):
        if obj.TypeId in CONTAINERS:
            return True
        if obj.isDerivedFrom("App::Link") or obj.TypeId == "App::LinkElement":
            try:
                linked = obj.getLinkedObject(True)
                return linked is not None and linked is not obj and linked.TypeId in CONTAINERS
            except Exception:
                return False
        return False

    # -- path helpers ------------------------------------------------------
    @staticmethod
    def _find_before(path, types):
        """Closest node of one of `types` that affects the path tail (scans siblings before each step)."""
        for k in range(path.getLength() - 1, 0, -1):
            parent = path.getNode(k - 1)
            idx = path.getIndex(k)
            try:
                for j in range(idx - 1, -1, -1):
                    c = parent.getChild(j)
                    for t in types:
                        if c.isOfType(t):
                            return c
            except Exception:
                continue
        return None

    def _mesh(self, faceset, coords):
        key = (ptr(faceset), faceset.getNodeId(), ptr(coords), coords.getNodeId())
        hit = self._mesh_cache.get(key)
        if hit is not None:
            return hit
        pts = coords.point.getValues() if not coords.isOfType(coin.SoVertexProperty.getClassTypeId()) \
            else coords.vertex.getValues()
        V = np.array([v.getValue() for v in pts], dtype=float).reshape(-1, 3)
        T = geom.triangles_from_index(faceset.coordIndex.getValues())
        if len(T):
            T = T[(T < len(V)).all(axis=1)]
        if len(V):
            diag = float(np.linalg.norm(V.max(axis=0) - V.min(axis=0)))
            V, T = geom.weld(V, T, max(diag * 1e-7, 1e-12))
        # forget older versions of the same nodes
        for k in [k for k in self._mesh_cache if k[0] == key[0] and k != key]:
            del self._mesh_cache[k]
        self._mesh_cache[key] = (V, T)
        return V, T

    # -- harvest -----------------------------------------------------------
    def harvest(self):
        """Returns (instances, parts): geom.MeshInstance list and {part key: PartInfo}."""
        sa = coin.SoSearchAction()
        sa.setType(coin.SoIndexedFaceSet.getClassTypeId(), True)
        sa.setInterest(coin.SoSearchAction.ALL)
        sa.apply(self.root)
        paths = sa.getPaths()
        roots = self._object_roots()
        vpr = self.view.getViewer().getSoRenderManager().getViewportRegion()
        coord_types = (coin.SoCoordinate3.getClassTypeId(), coin.SoVertexProperty.getClassTypeId())
        mat_types = (coin.SoMaterial.getClassTypeId(),)
        instances, parts = [], {}
        owners = None                   # face set -> object, built only when needed
        for i in range(paths.getLength()):
            path = paths[i]
            nodes = [path.getNode(k) for k in range(path.getLength())]
            if any(ptr(n) in self.skip for n in nodes):
                continue
            tail = nodes[-1]
            coords = None
            try:
                vp = tail.vertexProperty.getValue()
                if vp is not None and vp.vertex.getNum() > 0:
                    coords = vp
            except Exception:
                pass
            if coords is None:
                coords = self._find_before(path, coord_types)
            if coords is None:
                continue
            V, T = self._mesh(tail, coords)
            if len(T) == 0:
                continue
            # the part: outermost non-container object on the path
            part_obj, part_k = None, -1
            for k, n in enumerate(nodes):
                o = roots.get(ptr(n))
                if o is not None and not self._is_container(o):
                    part_obj, part_k = o, k
                    break
            own_root = None
            if part_obj is None:
                # a link to a container draws the container's parts through copies of
                # their nodes, so no object is on the path; the face set is shared
                if owners is None:
                    owners = self._faceset_owners()
                part_obj = owners.get(ptr(tail))
                if part_obj is not None:
                    own_root = part_obj.ViewObject.RootNode      # for its colour
            if part_obj is not None:
                key = (part_obj.Document.Name, part_obj.Name)
            else:
                key = ("", "mesh%x" % ptr(tail))
            gm = coin.SoGetMatrixAction(vpr)
            gm.apply(path)
            M = np.array(gm.getMatrix().getValue(), dtype=float).T      # Coin is row-major
            instances.append(geom.MeshInstance((key, i), key, V, T, M))
            if key not in parts or (parts[key].path is None and part_k > 0):
                node = nodes[part_k] if part_k >= 0 else (own_root or tail)
                color = self._shape_color(node, path, mat_types)
                label = part_obj.Label if part_obj is not None else "Mesh"
                # the path down to the part's node: its matrix is what the part sits in
                parts[key] = PartInfo(key, part_obj, label, color, node,
                                      path.copy(0, part_k + 1) if part_k > 0 else None, part_k)
        return instances, parts

    def _faceset_owners(self):
        """ptr(face set) -> the non-container object whose own display holds it."""
        m = {}
        for d in sorted(App.listDocuments()):
            if Gui.getDocument(d) is None:
                continue
            for o in App.getDocument(d).Objects:
                if self._is_container(o):
                    continue
                try:
                    root = o.ViewObject.RootNode
                except Exception:
                    continue
                if root is None:
                    continue
                sa = coin.SoSearchAction()
                sa.setType(coin.SoIndexedFaceSet.getClassTypeId(), True)
                sa.setInterest(coin.SoSearchAction.ALL)
                sa.apply(root)
                found = sa.getPaths()
                for k in range(found.getLength()):
                    m.setdefault(ptr(found[k].getTail()), o)
        return m

    def _shape_color(self, node, path, mat_types):
        """Display colour of a part: its view provider's 'ShapeMaterial' node (the visible
        one, so a Body gives its tip's colour and a link its own override or its target's)."""
        sa = coin.SoSearchAction()
        sa.setType(coin.SoMaterial.getClassTypeId())
        sa.setInterest(coin.SoSearchAction.ALL)
        sa.apply(node)
        found = sa.getPaths()
        fallback = None
        for i in range(found.getLength()):
            m = found[i].getTail()
            if not m.diffuseColor.getNum():
                continue
            name = m.getName()
            name = name.getString() if hasattr(name, "getString") else str(name)
            if name == "ShapeMaterial":
                return _color_of(m.diffuseColor[0])
            if fallback is None and name not in ("PointMaterial", "LineMaterial"):
                fallback = m
        if fallback is None:
            fallback = self._find_before(path, mat_types)
        if fallback is not None and fallback.diffuseColor.getNum():
            return _color_of(fallback.diffuseColor[0])
        return (0.8, 0.8, 0.8)

    def part_parent_matrix(self, info):
        """4x4 world matrix in effect where a part's node is drawn (to draw the node elsewhere).

        The path ends at the part's node; a matrix action does not descend into
        the tail, so this is everything applied before the node."""
        if info.path is None:
            return np.eye(4)
        vpr = self.view.getViewer().getSoRenderManager().getViewportRegion()
        gm = coin.SoGetMatrixAction(vpr)
        gm.apply(info.path)
        return np.array(gm.getMatrix().getValue(), dtype=float).T
