"""Unit tests of freecad.CutPlane.geom (pure numpy/shapely).

Run:  tests/run_unit.sh   (uses the FreeCAD flatpak's python, which has numpy and shapely)
"""
import math
import os
import sys
import unittest

import numpy as np
import shapely

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "freecad", "CutPlane"))
import geom  # noqa: E402


def box_mesh(lo=(0, 0, 0), hi=(1, 1, 1)):
    """Box with 4 own vertices per face, like a FreeCAD display mesh (24 vertices)."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    faces = [
        [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],   # bottom (-z)
        [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],   # top (+z)
        [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],   # -y
        [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],   # +y
        [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],   # -x
        [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],   # +x
    ]
    V, T = [], []
    for f in faces:
        b = len(V)
        V += f
        T += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    return np.array(V, float), np.array(T, np.int64)


def tube_mesh(R=2.0, r=1.0, h=4.0, N=32):
    """Closed tube along z (annulus extruded), welded."""
    ang = np.linspace(0, 2 * math.pi, N, endpoint=False)
    ring = lambda rad, z: np.stack((rad * np.cos(ang), rad * np.sin(ang), np.full(N, z)), axis=1)
    V = np.concatenate((ring(R, 0), ring(R, h), ring(r, 0), ring(r, h)))
    o0, o1, i0, i1 = 0, N, 2 * N, 3 * N
    T = []
    for k in range(N):
        k1 = (k + 1) % N
        T += [(o0 + k, o0 + k1, o1 + k1), (o0 + k, o1 + k1, o1 + k)]      # outer wall
        T += [(i0 + k, i1 + k1, i0 + k1), (i0 + k, i1 + k, i1 + k1)]      # inner wall
        T += [(o1 + k, o1 + k1, i1 + k1), (o1 + k, i1 + k1, i1 + k)]      # top
        T += [(o0 + k, i0 + k1, o0 + k1), (o0 + k, i0 + k, i0 + k1)]      # bottom
    return V, np.array(T, np.int64)


def ngon_area(rad, N):
    return 0.5 * N * rad * rad * math.sin(2 * math.pi / N)


def square(c, a):
    x, y = c
    return np.array([(x - a, y - a), (x + a, y - a), (x + a, y + a), (x - a, y + a)], float)


class TestMesh(unittest.TestCase):
    def test_triangles_from_index(self):
        self.assertEqual(geom.triangles_from_index([0, 1, 2, -1, 2, 3, 0, -1]).tolist(),
                         [[0, 1, 2], [2, 3, 0]])
        # a quad and a pentagon, last one without terminator
        t = geom.triangles_from_index([0, 1, 2, 3, -1, 4, 5, 6, 7, 8])
        self.assertEqual(len(t), 2 + 3)

    def test_weld_box(self):
        V, T = box_mesh()
        Vw, Tw = geom.weld(V, T, 1e-9)
        self.assertEqual(len(Vw), 8)
        self.assertEqual(len(Tw), 12)

    def test_box_section(self):
        V, T = geom.weld(*box_mesh(), 1e-9)
        closed, opened = geom.plane_section(V, T, V[:, 2] - 0.3)
        self.assertEqual(len(closed), 1)
        self.assertEqual(opened, [])
        self.assertTrue(np.allclose(closed[0][:, 2], 0.3))
        region = geom.region_from_loops([closed[0][:, :2]])
        self.assertAlmostEqual(region.area, 1.0, places=9)

    def test_unwelded_box_still_closes(self):
        V, T = box_mesh()           # every face has its own vertices: chains are open
        closed, opened = geom.plane_section(V, T, V[:, 2] - 0.3)
        self.assertEqual(closed, [])
        more, rest = geom.join_open(opened, 1e-9)
        self.assertEqual(len(more), 1)
        self.assertEqual(rest, [])
        self.assertAlmostEqual(geom.region_from_loops([more[0][:, :2]]).area, 1.0, places=9)

    def test_plane_through_vertices(self):
        V, T = geom.weld(*box_mesh(), 1e-9)
        # plane exactly at the top face: top vertices count as positive, the cut runs
        # through them and gives the top outline
        closed, _ = geom.plane_section(V, T, V[:, 2] - 1.0)
        self.assertEqual(len(closed), 1)
        self.assertAlmostEqual(geom.region_from_loops([closed[0][:, :2]]).area, 1.0, places=9)
        # plane at the bottom face: everything is on the positive side, no cut
        closed, opened = geom.plane_section(V, T, V[:, 2] - 0.0)
        self.assertEqual((closed, opened), ([], []))

    def test_tube_across(self):
        V, T = tube_mesh(N=32)
        closed, opened = geom.plane_section(V, T, V[:, 2] - 1.7)
        self.assertEqual(len(closed), 2)
        self.assertEqual(opened, [])
        region = geom.region_from_loops([L[:, :2] for L in closed])
        self.assertAlmostEqual(region.area, ngon_area(2, 32) - ngon_area(1, 32), places=9)
        self.assertEqual(len(region.interiors), 1)

    def test_tube_through_axis(self):
        V, T = tube_mesh(R=2, r=1, h=4, N=32)
        # plane x = 0.0 contains the axis: two wall rectangles of (R - r) x h
        closed, _ = geom.plane_section(V, T, V[:, 0] - 1e-7)
        self.assertEqual(len(closed), 2)
        region = geom.region_from_loops([L[:, 1:] for L in closed])
        self.assertAlmostEqual(region.area, 2 * 1.0 * 4.0, places=5)

    def test_instance_transform(self):
        V, T = geom.weld(*box_mesh(), 1e-9)
        M = np.eye(4)
        M[:3, :3] *= 2.0                         # scaled link
        M[:3, 3] = (10, 0, 0)
        inst = geom.MeshInstance("k", "P", V, T, M)
        n = np.array((1.0, 0, 0))
        self.assertTrue(inst.crosses(n, 11.0))
        self.assertFalse(inst.crosses(n, 13.0))
        closed, _ = inst.section(n, 11.0, 0.0)
        self.assertEqual(len(closed), 1)
        self.assertTrue(np.allclose(closed[0][:, 0], 11.0))
        self.assertAlmostEqual(geom.region_from_loops([closed[0][:, 1:]]).area, 4.0, places=9)

    def test_section_parts(self):
        V, T = geom.weld(*box_mesh(), 1e-9)
        a = geom.MeshInstance("a", "A", V, T, np.eye(4))
        M = np.eye(4)
        M[:3, 3] = (0.5, 0, 0)                   # overlaps A by half
        b = geom.MeshInstance("b", "B", V, T, M)
        n = np.array((0, 0, 1.0))
        u, v = geom.frame_from_normal(n, (1, 0, 0))
        parts = geom.section_parts([a, b], (0, 0, 0.5), n, u, v)
        self.assertEqual(set(parts), {"A", "B"})
        ov, pairs = geom.overlaps([parts["A"][0], parts["B"][0]])
        self.assertAlmostEqual(ov.area, 0.5, places=9)
        self.assertEqual(pairs, [(0, 1)])


class TestRegions(unittest.TestCase):
    def test_island_in_hole(self):
        loops = [square((0, 0), 5), square((0, 0), 3), square((0, 0), 1)]
        r = geom.region_from_loops(loops)
        self.assertAlmostEqual(r.area, 100 - 36 + 4, places=9)

    def test_two_separate_with_holes(self):
        loops = [square((0, 0), 2), square((0, 0), 1), square((10, 0), 2), square((10, 0), 1)]
        self.assertAlmostEqual(geom.region_from_loops(loops).area, 2 * (16 - 4), places=9)

    def test_triangulate_with_hole(self):
        r = geom.region_from_loops([square((0, 0), 2), square((0, 0), 1)])
        tris = geom.triangulate(r)
        self.assertEqual(tris.shape[1:], (3, 2))
        a = tris[:, 1] - tris[:, 0]
        b = tris[:, 2] - tris[:, 0]
        area = 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum()
        self.assertAlmostEqual(area, 12.0, places=9)
        # no triangle inside the hole
        c = tris.mean(axis=1)
        self.assertFalse(np.any((np.abs(c[:, 0]) < 1) & (np.abs(c[:, 1]) < 1)))
        # all counter-clockwise (front faces seen from +n)
        self.assertTrue(np.all(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0] > 0))

    def test_hatch(self):
        r = shapely.box(0, 0, 10, 10)
        segs = geom.hatch_segments(r, 0.0, 1.0)
        lengths = np.linalg.norm(segs[:, 1] - segs[:, 0], axis=1)
        self.assertGreaterEqual(len(segs), 9)
        self.assertTrue(np.allclose(lengths, 10.0))
        segs45 = geom.hatch_segments(r, 45.0, 1.0)
        total = np.linalg.norm(segs45[:, 1] - segs45[:, 0], axis=1).sum()
        self.assertAlmostEqual(total, 100.0, delta=5.0)      # area / spacing
        # every segment lies inside the region
        mid = 0.5 * (segs45[:, 0] + segs45[:, 1])
        self.assertTrue(np.all((mid >= -1e-9) & (mid <= 10 + 1e-9)))
        # grid anchored at the origin: moving the region by whole spacings keeps the lines
        moved = geom.hatch_segments(shapely.box(0, 3, 10, 13), 0.0, 1.0)
        self.assertTrue(np.allclose(np.unique(np.round(moved[:, 0, 1] % 1.0, 9)), [0.0]))

    def test_hatch_with_hole(self):
        r = geom.region_from_loops([square((0, 0), 2), square((0, 0), 1)])
        segs = geom.hatch_segments(r, 0.0, 0.5)
        mid = 0.5 * (segs[:, 0] + segs[:, 1])
        self.assertFalse(np.any((np.abs(mid[:, 0]) < 0.99) & (np.abs(mid[:, 1]) < 0.99)))

    def test_hatch_line_limit(self):
        segs = geom.hatch_segments(shapely.box(0, 0, 1000, 1000), 0.0, 0.01, max_lines=100)
        self.assertLessEqual(len(segs), 102)

    def test_scanline_hatch_matches_shapely(self):
        loops = [square((0, 0), 5), square((0, 0), 3), square((0, 0), 1), square((12, 1), 2)]
        region = geom.region_from_loops(loops)
        for ang in (0.0, 45.0, 135.0, 15.0):
            a = geom.hatch_segments(region, ang, 0.37)
            b = geom.hatch_from_loops(loops, ang, 0.37)
            la = np.linalg.norm(a[:, 1] - a[:, 0], axis=1).sum()
            lb = np.linalg.norm(b[:, 1] - b[:, 0], axis=1).sum()
            self.assertAlmostEqual(la, lb, places=6, msg="angle %g" % ang)
            mid = 0.5 * (b[:, 0] + b[:, 1])
            self.assertTrue(np.all(shapely.contains_xy(region.buffer(1e-9), mid[:, 0], mid[:, 1])))

    def test_region_fast_path_polygon_with_holes(self):
        loops = [square((0, 0), 10)] + [square((x, y), 1) for x in (-5, 0, 5) for y in (-5, 0, 5)]
        r = geom.region_from_loops(loops)
        self.assertEqual(r.geom_type, "Polygon")
        self.assertEqual(len(r.interiors), 9)
        self.assertAlmostEqual(r.area, 400 - 9 * 4, places=9)

    def test_clean_loop_and_key(self):
        sq = square((0, 0), 1)
        with_mid = np.array([sq[0], (0.0, -1.0), sq[1], sq[2], sq[3]])      # collinear point on an edge
        c = geom.clean_loop(with_mid, 1e-9)
        self.assertEqual(len(c), 4)
        k1 = geom.loops_key([c], 1e-6)
        k2 = geom.loops_key([np.roll(sq, 2, axis=0)[::-1]], 1e-6)         # other start, other direction
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, geom.loops_key([square((0, 0), 1.1)], 1e-6))

    def test_styles(self):
        regions = [shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1), shapely.box(2, 0, 3, 1),
                   shapely.box(10, 10, 11, 11)]
        pairs = geom.touching_pairs(regions, 1e-6)
        self.assertEqual(sorted(pairs), [(0, 1), (1, 2)])
        st = geom.assign_styles(4, pairs, 4)
        self.assertNotEqual(st[0], st[1])
        self.assertNotEqual(st[1], st[2])

    def test_overlaps_ignore_tessellation_slivers(self):
        """A shaft in a ring of the same nominal radius, each tessellated on its own:
        slivers, not an overlap. A real interference of 0.5 mm stays."""
        def ngon(r, n, phase=0.0):
            a = phase + 2 * np.pi * np.arange(n) / n
            return shapely.Polygon(np.column_stack((r * np.cos(a), r * np.sin(a))))
        sag = lambda r, n: r * (1 - np.cos(np.pi / n))         # chord deviation of an n-gon
        shaft = ngon(10, 24)
        ring = shapely.difference(ngon(20, 40), ngon(10, 17, 0.1))
        tols = [sag(10, 24), sag(10, 17)]
        ov, pairs = geom.overlaps([shaft, ring])
        self.assertEqual(pairs, [(0, 1)])                       # without tolerances: slivers count
        ov, pairs = geom.overlaps([shaft, ring], tols=tols)
        self.assertEqual(pairs, [])
        self.assertTrue(ov.is_empty)
        tight = shapely.difference(ngon(20, 40), ngon(9.5, 17, 0.1))
        ov, pairs = geom.overlaps([shaft, tight], tols=tols)
        self.assertEqual(pairs, [(0, 1)])
        self.assertAlmostEqual(ov.area, ngon(10, 24).difference(ngon(9.5, 17, 0.1)).area, places=6)
        ov, pairs = geom.overlaps([shaft, ring], tols=[0.0, 0.0])   # meshes: no tolerance
        self.assertEqual(pairs, [(0, 1)])


class TestFrames(unittest.TestCase):
    def test_frame(self):
        u, v = geom.frame_from_normal((0, 0, 1), (1, 1, 0))
        self.assertTrue(np.allclose(np.cross(u, v), (0, 0, 1)))
        self.assertTrue(np.allclose(u, np.array((1, 1, 0)) / math.sqrt(2)))

    def test_plane_through_axis_snaps_to_mid_plane(self):
        axes = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
        n = geom.plane_through_axis((0, 0, 1), (1, 0.4, 0.3), axes)
        self.assertTrue(np.allclose(n, (1, 0, 0)))
        n = geom.plane_through_axis((0, 0, 1), (-0.2, -1, 5), axes)
        self.assertTrue(np.allclose(n, (0, -1, 0)))
        # no candidates: the view direction without its axis part
        n = geom.plane_through_axis((0, 0, 1), (1, 1, 7))
        self.assertTrue(np.allclose(n, np.array((1, 1, 0)) / math.sqrt(2)))
        # looking straight down the axis
        n = geom.plane_through_axis((0, 0, 1), (0, 0, 1), axes)
        self.assertAlmostEqual(abs(float(np.dot(n, (0, 0, 1)))), 0.0)

    def test_snap_direction(self):
        c = [(1, 0, 0), (0, 1, 0)]
        self.assertTrue(np.allclose(geom.snap_direction((-0.9, 0.1, 0), c), (-1, 0, 0)))
        self.assertTrue(np.allclose(geom.snap_direction((0, 0, 1), c, 30), (0, 0, 1)))

    def test_nice_step(self):
        self.assertEqual([geom.nice_step(x) for x in (0.13, 2.4, 4.0, 80)], [0.1, 2, 5, 100])

    def test_nice_hatch(self):
        got = [geom.nice_hatch(x) for x in (0.13, 1.3, 2.4, 3.3, 4.4, 7.4, 9.5, 120)]
        self.assertTrue(np.allclose(got, [0.15, 1.5, 2.5, 3, 4, 8, 10, 100]), got)
        for x in np.geomspace(0.01, 1000, 200):          # never more than ~22 % off
            self.assertLess(abs(math.log(geom.nice_hatch(x) / x)), math.log(1.23))

    def test_hatch_px_per_mm(self):
        s = 4.0                                            # face-on: 4 px per mm in every direction
        R = np.array(((0.6, -0.8), (0.8, 0.6)))
        for a in (0, 45, 105):
            self.assertAlmostEqual(geom.hatch_px_per_mm(s * R, a), s)
        # tilted 60 deg about u: v is shortened to cos 60 = 0.5
        P = np.diag((s, 0.5 * s))
        self.assertAlmostEqual(geom.hatch_px_per_mm(P, 0), 0.5 * s)    # lines along u, spaced along v: squeezed
        self.assertAlmostEqual(geom.hatch_px_per_mm(P, 90), s)         # lines along v, spaced along u: not
        # nearly edge-on: kept above smax / max_stretch
        self.assertAlmostEqual(geom.hatch_px_per_mm(np.diag((s, 0.01 * s)), 0, max_stretch=3), s / 3)

    def test_screen_metric(self):
        R = np.array(((0.6, -0.8), (0.8, 0.6)))
        A, key = geom.screen_metric(3.0 * R)                 # face-on, any screen rotation
        self.assertEqual(key[0], key[1])
        self.assertTrue(np.allclose(A, A[0, 0] * np.eye(2)) and abs(A[0, 0] / 3.0 - 1) < 0.05, A)
        A2, key2 = geom.screen_metric(3.03 * R)              # 1 % zoom: same step
        self.assertEqual(key, key2)
        P = np.array(((4.0, 0.0), (0.0, 2.0))) @ np.array(((0.0, 1.0), (-1.0, 0.0)))   # shortened, turned
        A, key = geom.screen_metric(P)
        M, Ma = P.T @ P, A.T @ A                              # same distances in the plane
        self.assertTrue(np.allclose(Ma, M, rtol=0.1, atol=0.1 * np.abs(M).max()), (Ma, M))
        A, key = geom.screen_metric(np.diag((4.0, 0.01)))     # edge-on: held at 1/3
        self.assertAlmostEqual(np.linalg.svd(A, compute_uv=False)[1] / 4.0, 1 / 3, delta=0.04)

    def test_outline_strip(self):
        def tris(g, A, w):
            V, counts = geom.outline_strip(g, A, w)
            self.assertEqual(int(counts.sum()), len(V))
            return geom.strip_triangles(V, counts)

        def area(T):
            a, b = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
            return float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2)

        rect = shapely.box(0, 0, 100, 50)
        self.assertAlmostEqual(area(tris(rect, 2.0 * np.eye(2), 4.0)), 100 * 50 - 96 * 46, delta=1e-6)
        self.assertAlmostEqual(area(tris(rect, np.diag((2.0, 1.0)), 4.0)),
                               100 * 50 - 96 * 42, delta=1e-6)          # v shortened: 2 mm along u, 4 along v
        V, counts = geom.outline_strip(rect, np.eye(2), 1.0)
        self.assertEqual(list(counts), [10])                            # 4 corners + closing pair, 2 points each
        holed = rect.difference(shapely.box(40, 20, 60, 30))
        T = tris(holed, np.eye(2), 1.0)
        self.assertAlmostEqual(area(T), (100 * 50 - 98 * 48) + (22 * 12 - 20 * 10), delta=1e-6)
        inside = shapely.contains_xy(holed.buffer(1e-6), T[:, :, 0].ravel(), T[:, :, 1].ravel())
        self.assertTrue(inside.all(), "strip stays inside the region, also around the hole")
        for g in (shapely.MultiPolygon([rect, shapely.box(200, 0, 210, 10)]),           # several polygons
                  shapely.Point(0, 0).buffer(10, quad_segs=16)):                       # round
            T = tris(g, np.eye(2), 1.0)
            self.assertTrue(shapely.contains_xy(g.buffer(1e-6), T[:, :, 0].ravel(), T[:, :, 1].ravel()).all())
            self.assertAlmostEqual(area(T), g.area - g.buffer(-1.0, join_style="mitre").area, delta=0.05 * area(T))
        sharp = shapely.Polygon([(0, 0), (100, 0), (0, 5)])                            # very sharp corner
        self.assertTrue(np.isfinite(geom.outline_strip(sharp, np.eye(2), 1.0)[0]).all())
        self.assertEqual(len(geom.outline_strip(shapely.Polygon(), np.eye(2), 3.0)[0]), 0)

if __name__ == "__main__":
    unittest.main()
