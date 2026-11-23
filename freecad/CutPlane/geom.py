# SPDX-License-Identifier: LGPL-2.1-or-later
"""Pure geometry for section caps.

No FreeCAD or Coin imports, so the whole module is unit-tested on its own
(tests/test_geom.py). Arrays are numpy float64; 2D geometry is shapely.

Conventions
-----------
A section plane is (origin O, unit normal n, in-plane axes u, v) with
u x v = n. The normal points to the side that is *removed*; material with
n.(x - O) <= 0 is kept. 2D plane coordinates are measured from the point of
the plane closest to the world origin, so a hatch pattern stays put while the
plane is dragged along its normal.
"""

import math

import numpy as np
import shapely
from shapely import affinity

EMPTY = shapely.Polygon()


# ----------------------------------------------------------------------
# Vectors and frames
# ----------------------------------------------------------------------
def unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-300 else v


def frame_from_normal(n, hint_u=None):
    """Right-handed (u, v) for the unit normal n, with u as close to hint_u as possible."""
    n = unit(n)
    cands = []
    if hint_u is not None:
        cands.append(np.asarray(hint_u, dtype=float))
    cands += [np.array((1.0, 0, 0)), np.array((0, 1.0, 0)), np.array((0, 0, 1.0))]
    for c in cands:
        u = c - n * np.dot(c, n)
        if np.linalg.norm(u) > 1e-6:
            u = unit(u)
            return u, np.cross(n, u)
    raise ValueError("degenerate normal")


def rotate(v, axis, angle_rad):
    """Rodrigues rotation of v about the unit axis."""
    v = np.asarray(v, dtype=float)
    k = unit(axis)
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return v * c + np.cross(k, v) * s + k * np.dot(k, v) * (1 - c)


def snap_direction(target, candidates, max_angle_deg=90.0):
    """The candidate (or its negative) closest to target, or target if none is within max_angle."""
    t = unit(target)
    best, best_dot = None, math.cos(math.radians(max_angle_deg))
    for c in candidates:
        c = unit(c)
        for s in (c, -c):
            d = float(np.dot(s, t))
            if d > best_dot + 1e-12:
                best, best_dot = s, d
    return t if best is None else best


def plane_through_axis(axis, toward, candidates=()):
    """Unit normal of the plane that contains `axis` and faces `toward` as much as possible.

    The normal is `toward` with its axis component removed; it snaps to the
    nearest candidate direction perpendicular to the axis (a part's own frame,
    usually), which picks the closest "mid-plane". Falls back to any
    perpendicular when looking straight down the axis.
    """
    a = unit(axis)
    t = np.asarray(toward, dtype=float)
    perp = [unit(c - a * np.dot(c, a)) for c in candidates
            if np.linalg.norm(np.asarray(c, float) - a * np.dot(c, a)) > 0.5]
    w = t - a * np.dot(t, a)
    if np.linalg.norm(w) < 1e-9 * max(np.linalg.norm(t), 1e-300):
        if perp:
            return perp[0]
        return frame_from_normal(a)[0]
    w = unit(w)
    if perp:
        return snap_direction(w, perp)
    return w


# ----------------------------------------------------------------------
# Meshes
# ----------------------------------------------------------------------
def triangles_from_index(coord_index):
    """SoIndexedFaceSet coordIndex (polygons ended by -1) -> (m, 3) triangles (fans)."""
    ci = np.asarray(coord_index, dtype=np.int64).reshape(-1)
    if ci.size == 0:
        return np.zeros((0, 3), dtype=np.int64)
    if ci.size % 4 == 0:                       # FreeCAD writes "a b c -1" triangles
        r = ci.reshape(-1, 4)
        if np.all(r[:, 3] < 0) and np.all(r[:, :3] >= 0):
            return r[:, :3].copy()
    tris = []
    start = 0
    ends = np.flatnonzero(ci < 0).tolist()
    if ci[-1] >= 0:
        ends.append(len(ci))
    for e in ends:
        poly = ci[start:e]
        start = e + 1
        for k in range(1, len(poly) - 1):
            tris.append((poly[0], poly[k], poly[k + 1]))
    return np.array(tris, dtype=np.int64).reshape(-1, 3)


def weld(V, T, tol):
    """Merge vertices closer than about tol (coincident border vertices of a tessellation).

    Shared edges then have shared vertex ids, so the section chains by edge id.
    Degenerate triangles are dropped.
    """
    V = np.asarray(V, dtype=float)
    T = np.asarray(T, dtype=np.int64)
    if len(V) == 0 or len(T) == 0:
        return V, T.reshape(-1, 3)
    q = np.round(V / tol).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    Tw = inv[T]
    ok = (Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])
    return V[first], Tw[ok]


def plane_section(V, T, s):
    """Section of a welded triangle mesh by the zero set of the per-vertex values s.

    s is the signed distance to the plane (anything affine in position works);
    vertices with s >= 0 count as being on the positive side. Every crossed
    triangle gives one segment whose ends are keyed by the mesh edge they lie
    on, so segments chain into loops without a distance search.
    Returns (closed, open): lists of (k, 3) point arrays; closed loops do not
    repeat their first point.
    """
    V = np.asarray(V, dtype=float)
    T = np.asarray(T, dtype=np.int64)
    s = np.asarray(s, dtype=float)
    if len(T) == 0:
        return [], []
    pos = s[T] >= 0
    cnt = pos.sum(axis=1)
    cut = (cnt == 1) | (cnt == 2)
    if not cut.any():
        return [], []
    Tc, pc = T[cut], pos[cut]
    lone = np.where(cnt[cut] == 1, pc.argmax(axis=1), (~pc).argmax(axis=1))
    r = np.arange(len(Tc))
    a, b, c = Tc[r, lone], Tc[r, (lone + 1) % 3], Tc[r, (lone + 2) % 3]
    nv = len(V)
    keys = np.concatenate((np.minimum(a, b) * nv + np.maximum(a, b),
                           np.minimum(a, c) * nv + np.maximum(a, c)))
    uk, inv = np.unique(keys, return_inverse=True)
    inv = inv.reshape(-1)
    i, j = uk // nv, uk % nv
    t = s[i] / (s[i] - s[j])
    P = V[i] + t[:, None] * (V[j] - V[i])
    m = len(Tc)
    eab, eac = inv[:m], inv[m:]
    # Orient each segment by the triangle's winding: (a, b, c) is a rotation of the
    # triangle, so its boundary a->b->c crosses + to - on edge ab exactly when the
    # lone vertex a is positive. Starting every segment at its + to - crossing makes
    # a consistently oriented closed mesh give each crossing exactly one successor.
    start_ab = cnt[cut] == 1
    src = np.where(start_ab, eab, eac)
    dst = np.where(start_ab, eac, eab)
    fast = _chain_directed(src, dst, P)
    if fast is not None:
        return fast
    return _chain(eab, eac, P)


def _chain_directed(src, dst, P):
    """Loops of a directed segment set where every node has one successor; None otherwise."""
    K = len(P)
    if len(src) != K:
        return None
    nxt = np.full(K, -1, dtype=np.int64)
    nxt[src] = dst
    if (nxt < 0).any() or len(np.unique(dst)) != K:
        return None
    nxt_l = nxt.tolist()
    seen = bytearray(K)
    loops = []
    for n0 in range(K):
        if seen[n0]:
            continue
        chain = []
        cur = n0
        while not seen[cur]:
            seen[cur] = 1
            chain.append(cur)
            cur = nxt_l[cur]
        if cur != n0 or len(chain) < 3:
            return None
        loops.append(P[chain])
    return loops, []


def _chain(e0, e1, P):
    """Chain segments (e0[k], e1[k]) over the node points P into polylines."""
    K = len(P)
    src = np.concatenate((e0, e1))
    dst = np.concatenate((e1, e0))
    order = np.argsort(src, kind="stable")
    dst_s = dst[order].tolist()
    deg = np.bincount(src, minlength=K)
    start = (np.cumsum(deg) - deg).tolist()
    deg_l = deg.tolist()
    seen = [False] * K

    def walk(n0):
        chain = [n0]
        seen[n0] = True
        cur = n0
        while True:
            nxt = -1
            for q in range(start[cur], start[cur] + deg_l[cur]):
                cand = dst_s[q]
                if not seen[cand]:
                    nxt = cand
                    break
            if nxt < 0:
                return chain
            seen[nxt] = True
            chain.append(nxt)
            cur = nxt

    closed, opened = [], []
    for n0 in np.flatnonzero(deg == 1).tolist():        # open ends first
        if not seen[n0]:
            opened.append(walk(n0))
    for n0 in range(K):
        if not seen[n0]:
            ch = walk(n0)
            last = ch[-1]
            if len(ch) >= 3 and n0 in dst_s[start[last]:start[last] + deg_l[last]]:
                closed.append(ch)
            else:
                opened.append(ch)
    return [P[ch] for ch in closed], [P[ch] for ch in opened]


def join_open(chains, gap, max_chains=300):
    """Join open polylines whose ends meet within gap (cracks in a mesh).

    Returns (closed, still_open).
    """
    pool = [np.asarray(ch, dtype=float) for ch in chains if len(ch) >= 2]
    if not pool or len(pool) > max_chains:
        return [], pool
    closed, rest = [], []
    while pool:
        cur = pool.pop()
        while True:
            if len(cur) >= 3 and np.linalg.norm(cur[0] - cur[-1]) <= gap:
                closed.append(cur[:-1] if np.linalg.norm(cur[0] - cur[-1]) < 1e-15 else cur)
                cur = None
                break
            hit = False
            for k, ch in enumerate(pool):
                if np.linalg.norm(cur[-1] - ch[0]) <= gap:
                    cur = np.concatenate((cur, ch))
                elif np.linalg.norm(cur[-1] - ch[-1]) <= gap:
                    cur = np.concatenate((cur, ch[::-1]))
                elif np.linalg.norm(cur[0] - ch[-1]) <= gap:
                    cur = np.concatenate((ch, cur))
                elif np.linalg.norm(cur[0] - ch[0]) <= gap:
                    cur = np.concatenate((ch[::-1], cur))
                else:
                    continue
                pool.pop(k)
                hit = True
                break
            if not hit:
                break
        if cur is not None:
            rest.append(cur)
    return closed, rest


# ----------------------------------------------------------------------
# Plane coordinates
# ----------------------------------------------------------------------
def plane_anchor(origin, n):
    """Point of the plane closest to the world origin (2D coordinates start here)."""
    n = unit(n)
    return n * float(np.dot(n, origin))


def to_2d(P, anchor, u, v):
    P = np.asarray(P, dtype=float) - anchor
    return np.stack((P @ u, P @ v), axis=1)


def to_3d(Q, anchor, u, v, lift=0.0, n=None):
    Q = np.asarray(Q, dtype=float)
    out = anchor + Q[..., 0:1] * u + Q[..., 1:2] * v
    if lift and n is not None:
        out = out + lift * n
    return out


# ----------------------------------------------------------------------
# Regions
# ----------------------------------------------------------------------
def _polygonal(g):
    """Polygonal part of a geometry (make_valid can return collections)."""
    if g.is_empty:
        return EMPTY
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts) if parts else EMPTY


def region_from_loops(loops, min_area=0.0):
    """Material region bounded by closed 2D loops, filled even-odd.

    Each loop is classified by how many other loops contain it: even depth
    is an outer boundary, odd depth a hole (islands inside holes are outer
    again). Returns a shapely Polygon / MultiPolygon (possibly empty).
    """
    rings, polys = [], []
    for L in loops:
        L = np.asarray(L, dtype=float)
        if len(L) < 3:
            continue
        p = shapely.Polygon(L)
        simple = p.is_valid
        if not simple:
            p = _polygonal(shapely.make_valid(p))
        if p.is_empty or p.area <= min_area:
            continue
        polys.append(p)
        rings.append(L if simple else None)
    if not polys:
        return EMPTY
    if len(polys) == 1:
        return polys[0]
    arr = np.array(polys, dtype=object)
    area = shapely.area(arr)
    # pi[k] lies inside gi[k]; polygon containment (not a sample point) so that
    # loops touching their container and loops nested further in count right
    if len(arr) <= 6:
        M = shapely.covered_by(arr[:, None], arr[None, :])
        pi, gi = np.nonzero(M)
    else:
        pi, gi = shapely.STRtree(arr).query(arr, predicate="covered_by")
    keep = (pi != gi) & (area[gi] > area[pi])
    pi, gi = pi[keep], gi[keep]
    depth = np.bincount(pi, minlength=len(arr))
    pieces = []
    for e in np.flatnonzero(depth % 2 == 0).tolist():
        kids = pi[(gi == e) & (depth[pi] == depth[e] + 1)].tolist()
        shell = arr[e]
        if kids:
            built = None
            if rings[e] is not None and all(rings[k] is not None for k in kids):
                built = shapely.Polygon(rings[e], [rings[k] for k in kids])
                if not built.is_valid:
                    built = None
            shell = built if built is not None else \
                shapely.difference(shell, shapely.union_all(arr[kids]))
        pieces.append(shell)
    if len(pieces) == 1:
        return _polygonal(pieces[0])
    return _polygonal(shapely.union_all(pieces))


def clean_loops(loops, eps):
    """Drop repeated and collinear points of closed 2D loops, all loops in one numpy pass.

    Mesh diagonals add collinear points that move with the plane; without them
    the outline of a prismatic part is identical at every height.
    """
    loops = [np.asarray(L, dtype=float) for L in loops if len(L)]
    if not loops:
        return []
    P = np.concatenate(loops)
    lens = np.array([len(L) for L in loops])
    for _ in range(3):
        starts = np.cumsum(lens) - lens
        lid = np.repeat(np.arange(len(lens)), lens)
        pos = np.arange(len(P)) - starts[lid]
        prev = starts[lid] + (pos - 1) % lens[lid]
        nxt = starts[lid] + (pos + 1) % lens[lid]
        d1, d2 = P - P[prev], P[nxt] - P
        l1, l2 = np.linalg.norm(d1, axis=1), np.linalg.norm(d2, axis=1)
        cross = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
        keep = (np.abs(cross) > eps * np.maximum(l1 * l2, 1e-300)) & (l1 > 0)
        kept = np.bincount(lid, weights=keep, minlength=len(lens))
        keep |= (kept < 3)[lid]                       # never collapse a loop below a triangle
        if keep.all():
            break
        P, lens = P[keep], np.bincount(lid[keep], minlength=len(lens))
        lens = lens[lens > 0]
    return np.split(P, np.cumsum(lens)[:-1])


def clean_loop(L, eps):
    return clean_loops([L], eps)[0]


def loops_key(loops, quantum):
    """Hashable key of a set of 2D loops, independent of start points, direction and order
    (the sorted points plus the sorted loop lengths)."""
    loops = [L for L in loops if len(L)]
    if not loops:
        return 0
    q = np.round(np.concatenate(loops) / quantum).astype(np.int64)
    q = q[np.lexsort((q[:, 1], q[:, 0]))]
    lens = np.sort(np.array([len(L) for L in loops], dtype=np.int64))
    return hash(q.tobytes() + b"|" + lens.tobytes())


def triangulate(region):
    """(m, 3, 2) triangles covering a shapely polygonal region (holes respected)."""
    if region is None or region.is_empty:
        return np.zeros((0, 3, 2))
    tris = shapely.get_parts(shapely.constrained_delaunay_triangles(region))
    if len(tris) == 0:
        return np.zeros((0, 3, 2))
    coords = shapely.get_coordinates(tris)
    if len(coords) != 4 * len(tris):           # not plain triangles: go one by one
        out = [np.asarray(t.exterior.coords)[:3] for t in tris
               if t.geom_type == "Polygon" and len(t.exterior.coords) == 4]
        T = np.array(out).reshape(-1, 3, 2)
    else:
        T = coords.reshape(-1, 4, 2)[:, :3].copy()
    # counter-clockwise in (u, v), i.e. facing +n: OpenGL then sees the cap's front
    # from the removed side and lights it with the given normal
    a, b = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    cw = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0] < 0
    T[cw] = T[cw][:, ::-1]
    return T


def hatch_segments(region, angle_deg, spacing, max_lines=3000):
    """(k, 2, 2) hatch segments across a region.

    Lines run at angle_deg to the 2D x axis, spacing apart, on a grid anchored
    at the 2D origin, so the pattern is stable while the region changes.
    The spacing grows when more than max_lines lines would be needed.
    """
    if region is None or region.is_empty or spacing <= 0:
        return np.zeros((0, 2, 2))
    g = affinity.rotate(region, -angle_deg, origin=(0.0, 0.0))
    minx, miny, maxx, maxy = g.bounds
    n = math.ceil(maxy / spacing) - math.floor(miny / spacing) + 1
    if n > max_lines:
        spacing *= n / max_lines
    ks = np.arange(math.floor(miny / spacing), math.ceil(maxy / spacing) + 1)
    ys = ks * spacing
    pad = 1.0 + 0.01 * (maxx - minx)
    lines = np.empty((len(ys), 2, 2))
    lines[:, 0, 0], lines[:, 1, 0] = minx - pad, maxx + pad
    lines[:, :, 1] = ys[:, None]
    cut = shapely.intersection(shapely.linestrings(lines), g)
    parts = shapely.get_parts(cut)
    parts = parts[shapely.get_type_id(parts) == 1]          # LineStrings only
    if len(parts) == 0:
        return np.zeros((0, 2, 2))
    coords, idx = shapely.get_coordinates(parts, return_index=True)
    same = idx[1:] == idx[:-1]
    segs = np.stack((coords[:-1][same], coords[1:][same]), axis=1)
    a = math.radians(angle_deg)
    R = np.array(((math.cos(a), -math.sin(a)), (math.sin(a), math.cos(a))))
    return segs @ R.T


def hatch_from_loops(loops, angle_deg, spacing, max_lines=3000):
    """(k, 2, 2) hatch segments of the even-odd region bounded by closed 2D loops.

    A scanline in numpy: every loop edge contributes its crossings with the
    lines y' = k * spacing (in coordinates turned by -angle); crossings are
    sorted along each line and paired. Same grid as hatch_segments, much
    faster on regions with many holes.
    """
    loops = [np.asarray(L, dtype=float) for L in loops if len(L) >= 3]
    if not loops or spacing <= 0:
        return np.zeros((0, 2, 2))
    a = math.radians(angle_deg)
    c, s_ = math.cos(a), math.sin(a)
    P = np.concatenate(loops)
    Q = np.concatenate([np.roll(L, -1, axis=0) for L in loops])
    x0, y0 = P[:, 0] * c + P[:, 1] * s_, -P[:, 0] * s_ + P[:, 1] * c
    x1, y1 = Q[:, 0] * c + Q[:, 1] * s_, -Q[:, 0] * s_ + Q[:, 1] * c
    ylo, yhi = np.minimum(y0, y1), np.maximum(y0, y1)
    n_lines = math.ceil((yhi.max() - ylo.min()) / spacing) + 1
    if n_lines > max_lines:
        spacing *= n_lines / max_lines
    k0 = np.ceil(ylo / spacing).astype(np.int64)
    k1 = np.ceil(yhi / spacing).astype(np.int64) - 1          # line k crosses if ylo <= k*s < yhi
    cnt = np.maximum(k1 - k0 + 1, 0)
    total = int(cnt.sum())
    if total == 0:
        return np.zeros((0, 2, 2))
    e = np.repeat(np.arange(len(cnt)), cnt)
    k = k0[e] + (np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt))
    y = k * spacing
    t = (y - y0[e]) / (y1[e] - y0[e])
    x = x0[e] + t * (x1[e] - x0[e])
    order = np.lexsort((x, k))
    k, x, y = k[order], x[order], y[order]
    first = np.r_[0, np.flatnonzero(np.diff(k)) + 1]
    sizes = np.diff(np.r_[first, total])
    rank = np.arange(total) - np.repeat(first, sizes)
    i = np.flatnonzero((rank % 2 == 0) & (rank + 1 < np.repeat(sizes, sizes)))
    xa, xb, yy = x[i], x[i + 1], y[i]
    seg = np.empty((len(i), 2, 2))
    seg[:, 0, 0], seg[:, 0, 1] = xa * c - yy * s_, xa * s_ + yy * c
    seg[:, 1, 0], seg[:, 1, 1] = xb * c - yy * s_, xb * s_ + yy * c
    keep = np.abs(xb - xa) > 1e-12
    return seg[keep]


OVERLAP_OPEN = 0.6     # an overlap counts if thicker than 2 x this x the larger tolerance


def overlaps(regions, min_area=0.0, tols=None):
    """Union of the pairwise overlaps of a list of regions, and the pairs (i, j) involved.

    tols: per region, how far its outline may stray from the true surface (the
    chord deviation of the display mesh). Parts that only touch, a bearing on its
    shaft say, overlap in slivers about that thick, because each part is
    tessellated on its own. A pair's overlap only counts if something is left of
    it after shrinking it by OVERLAP_OPEN x the larger of the two tolerances.
    """
    idx = [k for k, r in enumerate(regions) if r is not None and not r.is_empty]
    if len(idx) < 2:
        return EMPTY, []
    arr = np.array([regions[k] for k in idx], dtype=object)
    a, b = shapely.STRtree(arr).query(arr, predicate="intersects")
    keep = a < b
    a, b = a[keep], b[keep]
    if len(a) == 0:
        return EMPTY, []
    inter = shapely.intersection(arr[a], arr[b])
    area = shapely.area(inter)
    good = area > min_area
    if tols is not None and good.any():
        t = np.asarray([tols[k] for k in idx], dtype=float)
        r = OVERLAP_OPEN * np.maximum(t[a], t[b])
        sel = np.flatnonzero(good & (r > 0))
        if len(sel):
            good[sel] = ~shapely.is_empty(shapely.buffer(inter[sel], -r[sel]))
    if not good.any():
        return EMPTY, []
    pairs = [(idx[i], idx[j]) for i, j in zip(a[good].tolist(), b[good].tolist())]
    return _polygonal(shapely.union_all(inter[good])), pairs


def touching_pairs(regions, gap):
    """Pairs (i, j) of regions closer than gap (neighbours for hatch angle assignment)."""
    idx = [k for k, r in enumerate(regions) if r is not None and not r.is_empty]
    if len(idx) < 2:
        return []
    arr = np.array([regions[k] for k in idx], dtype=object)
    a, b = shapely.STRtree(arr).query(arr, predicate="dwithin", distance=gap)
    keep = a < b
    return [(idx[i], idx[j]) for i, j in zip(a[keep].tolist(), b[keep].tolist())]


def assign_styles(n, pairs, n_styles):
    """Greedy colouring: style index per region so that touching regions differ."""
    nb = [set() for _ in range(n)]
    for i, j in pairs:
        nb[i].add(j)
        nb[j].add(i)
    style = [-1] * n
    for i in sorted(range(n), key=lambda k: -len(nb[k])):
        used = {style[j] for j in nb[i] if style[j] >= 0}
        style[i] = next((s for s in range(n_styles) if s not in used), i % n_styles)
    return style


def nice_step(x):
    """Round x to 1, 2 or 5 times a power of ten."""
    if x <= 0:
        return 1.0
    e = math.floor(math.log10(x))
    f = x / 10 ** e
    m = 1 if f < 1.5 else 2 if f < 3.5 else 5 if f < 7.5 else 10
    return m * 10 ** e


HATCH_STEPS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)


def nice_hatch(x):
    """Round x to the nearest (on a log scale) of 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8 times a
    power of ten: within about 20 % of x, and the pattern changes only at those steps."""
    if x <= 0:
        return 1.0
    e = math.floor(math.log10(x))
    f = x / 10 ** e
    m = min(HATCH_STEPS, key=lambda s: abs(math.log(s / f)))
    return m * 10 ** e


def hatch_px_per_mm(P, angle_deg, max_stretch=3.0):
    """Screen pixels per model millimetre across hatch lines drawn at angle_deg in the
    plane's (u, v), where P (2x2) maps plane millimetres to screen pixels.

    Lines h apart in the model are h * |det P| / |P a| apart on screen (a the line
    direction): a plane seen at an angle squeezes the lines that run along the
    unshortened direction. Seen nearly edge-on that would go to zero, so the result
    is kept above (largest scale of P) / max_stretch."""
    P = np.asarray(P, dtype=float)
    t = math.radians(angle_deg)
    pa = float(np.linalg.norm(P @ np.array((math.cos(t), math.sin(t)))))
    smax = float(np.linalg.svd(P, compute_uv=False)[0])
    if smax <= 0:
        return 0.0
    across = abs(float(np.linalg.det(P))) / max(pa, 1e-12 * smax)
    return max(across, smax / max_stretch)


def screen_metric(P, max_stretch=3.0, step=2 ** 0.125, angle_step=5.0):
    """(A, key) for measuring screen pixels in a plane: A (2x2) maps plane millimetres
    to a frame where one unit is one screen pixel in every direction.

    Only distances matter, so the screen rotation of P is dropped (A = S·Vt from the
    SVD). The two scales are rounded to steps of about 9 % and the direction to
    5 degrees, so A, and anything cached under `key`, changes only now and then while
    the view turns or zooms. Seen nearly edge-on, the small scale is held at
    (large scale) / max_stretch."""
    _u, S, Vt = np.linalg.svd(np.asarray(P, dtype=float))
    if S[0] <= 0:
        return np.eye(2), (0, 0, 0)
    q = math.log(step)
    k1 = round(math.log(S[0]) / q)
    k2 = round(math.log(max(S[1], S[0] / max_stretch)) / q)
    ang = math.degrees(math.atan2(Vt[0, 1], Vt[0, 0])) % 180.0
    ka = 0 if k1 == k2 else int(round(ang / angle_step)) % int(round(180 / angle_step))
    s1, s2, t = math.exp(k1 * q), math.exp(k2 * q), math.radians(ka * angle_step)
    c, s = math.cos(t), math.sin(t)
    A = np.diag((s1, s2)) @ np.array(((c, s), (-s, c)))
    return A, (k1, k2, ka)


def outline_strip(region, A, width, max_miter=4.0):
    """Triangle strips covering a band `width` wide just inside the boundary of a
    polygonal region (plane millimetres), measured after mapping with A (from
    screen_metric: width in screen pixels). The cut-face outline is built as
    geometry because some OpenGL drivers (VMware SVGA in a VM) draw every line 1 px
    wide.

    Returns (V, counts): V (N, 2) holds, ring by ring, the boundary point and its
    inner point for each boundary vertex in turn (outer, inner, outer, inner, ...),
    closed by repeating the first pair; counts[i] is the number of points of ring i,
    one triangle strip each. Two points per boundary vertex keep the data small.

    Built directly, without polygon booleans or triangulation: the inner point lies
    along the mitre of the two edges at that vertex (held to max_miter x width at
    very sharp corners). Rings are oriented with the inside on the left. Linear in
    the number of boundary points; on thin walls the bands of both sides overlap,
    which does not show."""
    empty = (np.zeros((0, 2)), np.zeros(0, dtype=np.int64))
    if region is None or region.is_empty or width <= 0:
        return empty
    a = np.asarray(A, dtype=float)
    polys = shapely.get_parts(shapely.orient_polygons(region))     # outer CCW, holes CW
    rings = shapely.get_rings(polys)                               # (plain polygons only)
    coords, ring = shapely.get_coordinates(rings, return_index=True)
    if len(coords) < 4:
        return empty
    Q = coords @ a.T                                     # into screen pixels (rotation dropped)
    same = ring[1:] == ring[:-1]                         # closed rings: last point repeats the first
    p0, p1, er = Q[:-1][same], Q[1:][same], ring[:-1][same]
    d = p1 - p0
    L = np.linalg.norm(d, axis=1)
    ok = L > 1e-9 * max(1.0, float(np.abs(Q).max()))
    p0, er, d, L = p0[ok], er[ok], d[ok], L[ok]
    if not len(p0):
        return empty
    nrm = np.column_stack((-d[:, 1], d[:, 0])) / L[:, None]      # left normal: inside
    idx = np.arange(len(er))
    first = np.r_[True, er[1:] != er[:-1]]
    last = np.r_[er[1:] != er[:-1], True]
    start_of = np.maximum.accumulate(np.where(first, idx, 0))
    prev = idx - 1
    prev[first] = idx[last]                              # the ring's last edge comes before its first
    n0, n1 = nrm[prev], nrm
    denom = np.maximum(1.0 + np.einsum("ij,ij->i", n0, n1), 2.0 / max_miter ** 2)
    q0 = p0 + width * (n0 + n1) / denom[:, None]         # inner point at each edge's start vertex
    ends = idx[last]
    order = np.insert(idx, ends + 1, start_of[ends])     # each ring closed by its first vertex again
    V = np.stack((p0[order], q0[order]), axis=1).reshape(-1, 2)
    counts = 2 * (ends - start_of[ends] + 2)
    return V @ np.linalg.inv(a).T, counts


def strip_triangles(V, counts):
    """(m, 3, 2) triangles of triangle strips (V, counts) as outline_strip returns them."""
    out, i0 = [], 0
    for c in counts:
        S = V[i0:i0 + c]
        if c >= 3:
            out.append(np.stack((S[:-2], S[1:-1], S[2:]), axis=1))
        i0 += c
    return np.concatenate(out) if out else np.zeros((0, 3, 2))


# ----------------------------------------------------------------------
# Whole section of a set of meshes
# ----------------------------------------------------------------------
class MeshInstance:
    """One displayed mesh: welded local vertices V, triangles T, world matrix M (4x4)."""

    __slots__ = ("key", "part", "V", "T", "M", "lo", "hi")

    def __init__(self, key, part, V, T, M):
        self.key, self.part = key, part
        self.V, self.T = V, T
        self.M = np.asarray(M, dtype=float)
        if len(V):
            W = V @ self.M[:3, :3].T + self.M[:3, 3]
            self.lo, self.hi = W.min(axis=0), W.max(axis=0)
        else:
            self.lo = self.hi = np.zeros(3)

    def crosses(self, n, d):
        c = 0.5 * (self.lo + self.hi)
        r = 0.5 * float(np.abs(n) @ (self.hi - self.lo))
        return abs(float(n @ c) - d) <= r * (1 + 1e-9) + 1e-12

    def section(self, n, d, gap):
        """World loops (closed, open) of this mesh cut by the plane n.x = d."""
        A, t = self.M[:3, :3], self.M[:3, 3]
        nl = A.T @ n                                   # s = n.(A y + t) - d, affine in y
        s = self.V @ nl - (d - float(n @ t))
        closed, opened = plane_section(self.V, self.T, s)
        closed = [L @ A.T + t for L in closed]
        opened = [L @ A.T + t for L in opened]
        if opened:
            more, opened = join_open(opened, gap)
            closed += more
        return closed, opened


def section_loops(instances, origin, n, u, v, gap=0.0):
    """Cut every mesh instance with the plane: {part: (loops_2d, loops_3d)}.

    2D loops are in the plane's coordinates (see plane_anchor).
    """
    n = unit(n)
    d = float(n @ np.asarray(origin, float))
    anchor = plane_anchor(origin, n)
    by_part = {}
    for inst in instances:
        if not inst.crosses(n, d):
            continue
        closed, _ = inst.section(n, d, gap)
        if not closed:
            continue
        entry = by_part.setdefault(inst.part, ([], []))
        entry[0].extend(to_2d(L, anchor, u, v) for L in closed)
        entry[1].extend(closed)
    return by_part


def section_parts(instances, origin, n, u, v, gap=0.0, min_area=0.0):
    """section_loops with a region per part: {part: (region_2d, loops_3d)}."""
    out = {}
    for part, (loops2d, loops3d) in section_loops(instances, origin, n, u, v, gap).items():
        region = region_from_loops(loops2d, min_area)
        if not region.is_empty:
            out[part] = (region, loops3d)
    return out
