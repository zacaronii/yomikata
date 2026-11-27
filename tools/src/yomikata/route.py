"""A way over a game's own ground (`yomikata route`): from where the character stands to a place (placements.py
gives the places), as waypoints for walk_3d, worked out offline from the stage's collision, so that a walk goes
round the cliffs, walls and drops a straight line runs into.

Three parts, the first the only one that knows a game:

1. A reader gives the collision as a mesh: points (x, y up, z) and triangles of them, each with the name of the
   group it is in. The first reader is for the GameCube Zelda stages: a room's room.dzb (Wind Waker; Twilight
   Princess's .kcl is another form).
2. The mesh becomes a graph (`Ground`): the ground is cut into square cells; each triangle that faces up (its
   slope no steeper than `slope` degrees) gives the cells under it a floor at its height (a cell under a
   bridge has two), and each steeper one (a wall) shuts the floors in the cells it stands in whose head room it
   cuts. A floor joins the floors of the eight cells round it that are no more than `up` above it or `down`
   below it: steps and slopes are walked, a ledge is gone down only when it is low.
3. A* over the floors, from the one nearest the character to the one nearest the place; then the cells are
   pulled straight (a waypoint is kept only where a straight walk from the last one would leave the floors), so
   that walk_3d is given a few corners and not every cell.

Nothing of the game's text is read: group names are the makers' names for the parts of the ground.
"""

from __future__ import annotations

import fnmatch
import heapq
import math
import struct
from dataclasses import dataclass, field


class RouteError(ValueError):
    pass


@dataclass
class Mesh:
    points: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int, str]]          # three points and the group's name


def dzb(data: bytes) -> Mesh:
    """A GameCube Zelda room's collision (room.dzb): a header of six (count, offset) pairs (points, triangles, a
    tree of blocks, the tree, groups, properties); a point is three floats, a triangle three point numbers, its
    property and its group (u16 each); a group 0x34 bytes beginning with where its name is."""
    if len(data) < 0x30:
        raise RouteError("too short for a collision header")
    n_points, at_points, n_tris, at_tris = struct.unpack_from(">4I", data, 0)
    n_groups, at_groups = struct.unpack_from(">2I", data, 0x20)
    if at_points + 12 * n_points > len(data) or at_tris + 10 * n_tris > len(data) \
            or at_groups + 0x34 * n_groups > len(data):
        raise RouteError("the header's counts run past the end: not a .dzb")
    points = [struct.unpack_from(">3f", data, at_points + 12 * k) for k in range(n_points)]
    names = []
    for g in range(n_groups):
        at = struct.unpack_from(">I", data, at_groups + 0x34 * g)[0]
        end = data.find(b"\0", at)
        names.append(data[at:end].decode("ascii", "replace") if 0 <= at < len(data) and end > 0 else "")
    tris = []
    for k in range(n_tris):
        a, b, c, _, g = struct.unpack_from(">5H", data, at_tris + 10 * k)
        if max(a, b, c) >= n_points:
            raise RouteError(f"triangle {k} names a point past the last")
        tris.append((a, b, c, names[g] if g < len(names) else ""))
    return Mesh(points, tris)


def _normal_y(p, q, r) -> float:
    ux, uy, uz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
    vx, vy, vz = r[0] - p[0], r[1] - p[1], r[2] - p[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    size = math.sqrt(nx * nx + ny * ny + nz * nz)
    return ny / size if size else 0.0


@dataclass
class Ground:
    """Floors by cell: floors[(i, j)] the heights of the floors over the cell's middle (lowest first); `shut` the
    floors walls stand in."""
    cell: float
    up: float
    down: float
    floors: dict = field(default_factory=dict)
    shut: set = field(default_factory=set)
    edge: dict = field(default_factory=dict)         # floor -> cells from the nearest edge (0: at an edge)
    margin: int = 0

    def cell_of(self, x: float, z: float) -> tuple[int, int]:
        return math.floor(x / self.cell), math.floor(z / self.cell)

    def middle(self, i: int, j: int) -> tuple[float, float]:
        return (i + 0.5) * self.cell, (j + 0.5) * self.cell

    def open(self, node) -> bool:
        return node not in self.shut

    def nearest(self, x: float, z: float, y: float | None = None, reach: float = 0.0):
        """The open floor nearest (x, z) (and nearest the height y, when given), looking `reach` round."""
        ci, cj = self.cell_of(x, z)
        rings = max(0, int(math.ceil(reach / self.cell)))
        best, best_d = None, math.inf
        for i in range(ci - rings, ci + rings + 1):
            for j in range(cj - rings, cj + rings + 1):
                mx, mz = self.middle(i, j)
                flat = math.hypot(mx - x, mz - z)
                if flat > reach + self.cell:
                    continue
                for k, h in enumerate(self.floors.get((i, j), ())):
                    if not self.open((i, j, k)):
                        continue
                    d = flat + (abs(h - y) * 4 if y is not None else 0.0)
                    if d < best_d:
                        best, best_d = (i, j, k), d
        return best

    def height(self, node) -> float:
        return self.floors[(node[0], node[1])][node[2]]

    def step(self, h: float, i: int, j: int) -> int | None:
        """The open floor of cell (i, j) a character at height h walks onto (the highest it may), if any."""
        best = None
        for k, h2 in enumerate(self.floors.get((i, j), ())):
            if -self.down <= h2 - h <= self.up and self.open((i, j, k)):
                best = k
        return best

    def next_to(self, node):
        i, j, k = node
        h = self.height(node)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di or dj:
                    k2 = self.step(h, i + di, j + dj)
                    if k2 is not None:
                        m = (i + di, j + dj, k2)
                        # Near an edge (a drop, a wall) dearer: the way keeps to the middle of a narrow ramp.
                        dear = 1.0 + 3.0 * (self.margin - self.edge[m]) / self.margin if m in self.edge else 1.0
                        yield m, self.cell * math.hypot(di, dj) * dear

    def edges(self, margin: int) -> None:
        """Marks the floors within `margin` cells of an edge: a floor with a neighbour it can't step onto."""
        self.margin, self.edge = max(0, int(margin)), {}
        if not self.margin:
            return
        ring = []
        for (i, j), hs in self.floors.items():
            for k, h in enumerate(hs):
                node = (i, j, k)
                if self.open(node) and any(self.step(h, i + di, j + dj) is None
                                           for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj):
                    self.edge[node] = 0
                    ring.append(node)
        for d in range(1, self.margin):
            nxt = []
            for node in ring:
                i, j, k = node
                h = self.height(node)
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        k2 = self.step(h, i + di, j + dj) if di or dj else None
                        if k2 is not None and (i + di, j + dj, k2) not in self.edge:
                            self.edge[(i + di, j + dj, k2)] = d
                            nxt.append((i + di, j + dj, k2))
            ring = nxt

    def straight(self, a, b) -> bool:
        """Whether a straight walk from node a to node b keeps to open floors, a step at a time, and no nearer an
        edge than a and b are (when the edges are marked)."""
        (ax, az), (bx, bz) = self.middle(a[0], a[1]), self.middle(b[0], b[1])
        n = max(1, int(math.ceil(math.hypot(bx - ax, bz - az) / (self.cell / 3))))
        # Never nearer an edge than both ends are: along a narrow pier, the middle is kept.
        least = max(1, min(self.edge.get(a, self.margin), self.edge.get(b, self.margin)))
        h, last = self.height(a), (a[0], a[1])
        for s in range(1, n + 1):
            i, j = self.cell_of(ax + (bx - ax) * s / n, az + (bz - az) * s / n)
            if (i, j) == last:
                continue
            k = self.step(h, i, j)
            if k is None or ((i, j) != (b[0], b[1]) and self.edge.get((i, j, k), self.margin) < least):
                return False                                # off the floors, or nearer the edge than its ends
            h, last = self.floors[(i, j)][k], (i, j)
        return last == (b[0], b[1])


def ground(mesh: Mesh, box: tuple[float, float, float, float], cell: float = 25.0, slope: float = 50.0,
           up: float = 30.0, down: float = 60.0, head: tuple[float, float] = (25.0, 90.0), body: float = 20.0,
           skip: list[str] | None = None, merge: float = 10.0, low: float | None = None, margin: int = 3) -> Ground:
    """The floors and walls of `mesh` inside box (x0, z0, x1, z1), in cells of `cell`. A triangle in a group
    matching any of `skip` is left out (fnmatch patterns). A wall shuts a floor when it passes within `body` of
    the cell's middle and rises through the head room over it: from head[0] to head[1] above the floor. A floor
    lower than `low` isn't walked (under water: Wind Waker's sea is at 0). Floors within `margin` cells of an
    edge (a drop or a wall) cost more to walk, and a straight stretch isn't drawn along the edge itself: a
    character walked along a narrow ramp drifts, and one at its very edge walks off it."""
    g = Ground(float(cell), float(up), float(down))
    x0, z0, x1, z1 = box
    least = math.cos(math.radians(slope))
    walls: dict = {}
    pts = mesh.points

    def cells_under(p, q, r):
        """(i, j, height at the cell's middle) for the cells whose middle the triangle covers (seen from above),
        and the cell under its middle, so that a triangle smaller than a cell isn't lost."""
        lo_i, hi_i = math.floor(max(min(p[0], q[0], r[0]), x0) / cell), math.floor(min(max(p[0], q[0], r[0]), x1) / cell)
        lo_j, hi_j = math.floor(max(min(p[2], q[2], r[2]), z0) / cell), math.floor(min(max(p[2], q[2], r[2]), z1) / cell)
        det = (q[2] - r[2]) * (p[0] - r[0]) + (r[0] - q[0]) * (p[2] - r[2])
        out = []
        if abs(det) > 1e-9:
            for i in range(lo_i, hi_i + 1):
                for j in range(lo_j, hi_j + 1):
                    mx, mz = (i + 0.5) * cell, (j + 0.5) * cell
                    a = ((q[2] - r[2]) * (mx - r[0]) + (r[0] - q[0]) * (mz - r[2])) / det
                    b = ((r[2] - p[2]) * (mx - r[0]) + (p[0] - r[0]) * (mz - r[2])) / det
                    c = 1 - a - b
                    if a >= -1e-6 and b >= -1e-6 and c >= -1e-6:
                        out.append((i, j, a * p[1] + b * q[1] + c * r[1]))
        cx, cz = (p[0] + q[0] + r[0]) / 3, (p[2] + q[2] + r[2]) / 3
        if x0 <= cx <= x1 and z0 <= cz <= z1:
            out.append((math.floor(cx / cell), math.floor(cz / cell), (p[1] + q[1] + r[1]) / 3))
        return out

    def cells_along(p, q, r):
        """The cells whose middle a wall passes within `body` of, seen from above (a wall is near a line), each
        with the wall's lowest and highest there: points along its three sides, with their heights."""
        seen: dict = {}
        reach = int(math.ceil(body / cell))
        for u, v in ((p, q), (q, r), (r, p)):
            n = max(1, int(math.ceil(math.hypot(v[0] - u[0], v[2] - u[2]) / (min(cell, body) / 2))))
            for s in range(n + 1):
                x, z = u[0] + (v[0] - u[0]) * s / n, u[2] + (v[2] - u[2]) * s / n
                y = u[1] + (v[1] - u[1]) * s / n
                if not (x0 - body <= x <= x1 + body and z0 - body <= z <= z1 + body):
                    continue
                ci, cj = math.floor(x / cell), math.floor(z / cell)
                for i in range(ci - reach, ci + reach + 1):
                    for j in range(cj - reach, cj + reach + 1):
                        if math.hypot((i + 0.5) * cell - x, (j + 0.5) * cell - z) <= body:
                            lo, hi = seen.get((i, j), (y, y))
                            seen[(i, j)] = (min(lo, y), max(hi, y))
        return seen

    for a, b, c, name in mesh.triangles:
        if skip and any(fnmatch.fnmatch(name, s) for s in skip):
            continue
        p, q, r = pts[a], pts[b], pts[c]
        if max(p[0], q[0], r[0]) < x0 or min(p[0], q[0], r[0]) > x1 \
                or max(p[2], q[2], r[2]) < z0 or min(p[2], q[2], r[2]) > z1:
            continue
        ny = _normal_y(p, q, r)
        if ny >= least:
            for i, j, h in cells_under(p, q, r):
                if low is None or h >= low:
                    g.floors.setdefault((i, j), []).append(h)
        elif abs(ny) < least:                               # a wall (a ceiling, facing down, is no wall)
            for ij, span in cells_along(p, q, r).items():
                walls.setdefault(ij, []).append(span)
    for ij, hs in g.floors.items():
        hs.sort()
        kept = [hs[0]]
        for h in hs[1:]:
            if h - kept[-1] > merge:
                kept.append(h)
            else:
                kept[-1] = max(kept[-1], h)
        g.floors[ij] = kept
        for k, h in enumerate(kept):
            if any(lo < h + head[1] and hi > h + head[0] for lo, hi in walls.get(ij, ())):
                g.shut.add((ij[0], ij[1], k))
    g.edges(margin)
    return g


def path(g: Ground, start, goal) -> list:
    """A* from node start to node goal over the floors; [] when there is no way."""
    gx, gz = g.middle(goal[0], goal[1])

    def guess(n):
        mx, mz = g.middle(n[0], n[1])
        return math.hypot(gx - mx, gz - mz)
    came, cost, todo, seq = {start: None}, {start: 0.0}, [(guess(start), 0, start)], 1
    while todo:
        _, _, n = heapq.heappop(todo)
        if n == goal:
            out = []
            while n is not None:
                out.append(n)
                n = came[n]
            return out[::-1]
        for m, d in g.next_to(n):
            c = cost[n] + d
            if c < cost.get(m, math.inf):
                cost[m], came[m] = c, n
                heapq.heappush(todo, (c + guess(m), seq, m))
                seq += 1
    return []


def pulled(g: Ground, nodes: list) -> list:
    """The corners of a path of cells: from each corner kept, the farthest cell a straight walk reaches."""
    if len(nodes) <= 2:
        return list(nodes)
    out, at = [nodes[0]], 0
    while at < len(nodes) - 1:
        far = at + 1
        for k in range(len(nodes) - 1, at + 1, -1):
            if g.straight(nodes[at], nodes[k]):
                far = k
                break
        out.append(nodes[far])
        at = far
    return out


def way(g: Ground, frm: tuple, to: tuple, reach: float = 600.0, every: float = 150.0) -> dict:
    """The waypoints from frm to to (each x, z, or x, y, z): the floor nearest each end (within `reach`), A*,
    pulled straight, with a waypoint every `every` along a long stretch (a walk aimed at a far corner drifts;
    their height is None). Returns {"way": [[x, z, height], ...], "from": node, "to": node} or {"why": ...}."""
    fy = frm[1] if len(frm) == 3 else None
    ty = to[1] if len(to) == 3 else None
    a = g.nearest(frm[0], frm[-1], fy, reach)
    b = g.nearest(to[0], to[-1], ty, reach)
    if a is None or b is None:
        return {"why": f"no floor within {reach} of the {'start' if a is None else 'place'}"}
    cells = path(g, a, b)
    if not cells:
        return {"why": "no way over the floors from the start to the place", "from": list(a), "to": list(b)}
    corners = pulled(g, cells)
    out, last = [], g.middle(a[0], a[1])
    for n in corners[1:]:
        x, z = g.middle(n[0], n[1])
        h = g.height(n)
        parts = max(1, int(math.ceil(math.hypot(x - last[0], z - last[1]) / every))) if every else 1
        for s in range(1, parts):                           # a waypoint every `every` along a long stretch
            out.append([round(last[0] + (x - last[0]) * s / parts, 1), round(last[1] + (z - last[1]) * s / parts, 1),
                        None])
        out.append([round(x, 1), round(z, 1), round(h, 1)])
        last = (x, z)
    return {"way": out, "cells": len(cells), "corners": len(corners) - 1, "from": list(a), "to": list(b)}


def from_archive(data: bytes) -> Mesh:
    """The collision of every .dzb in a stage or room archive (RARC, Yaz0 or not), as one mesh."""
    from . import placements
    mesh = Mesh([], [])
    for name, body in sorted(placements.rarc(data).items()):
        if name.endswith(".dzb"):
            part = dzb(body)
            base = len(mesh.points)
            mesh.points += part.points
            mesh.triangles += [(a + base, b + base, c + base, g) for a, b, c, g in part.triangles]
    if not mesh.triangles:
        raise RouteError("no collision (.dzb) in the archive")
    return mesh


def targets(mesh: Mesh, frm: tuple, places: list[dict], around: float = 2000.0, **options) -> list[dict]:
    """meet's targets: each place ({name, x, z, and y when known}) with the way to it from frm (x, y, z), or why
    there is none. The ground is worked out over the box round them all, `around` wider."""
    xs = [frm[0]] + [p["x"] for p in places]
    zs = [frm[-1]] + [p["z"] for p in places]
    reach = options.pop("reach", 600.0)
    every = options.pop("every", 150.0)
    g = ground(mesh, (min(xs) - around, min(zs) - around, max(xs) + around, max(zs) + around), **options)
    out = []
    for p in places:
        found = way(g, frm, (p["x"], p["y"], p["z"]) if p.get("y") is not None else (p["x"], p["z"]), reach, every)
        row = {"name": p.get("name", ""), "x": p["x"], "z": p["z"]}
        out.append({**row, "way": found["way"]} if "way" in found else {**row, "why": found["why"]})
    return out
