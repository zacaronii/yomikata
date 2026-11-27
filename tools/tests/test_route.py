"""`yomikata route`: a way over a made-up room's collision, round a wall, not up a shelf too high to step onto,
and not through water."""

import struct

import pytest

from test_placements import rarc_of
from yomikata import route


def dzb_of(quads: list[tuple[str, list[tuple[float, float, float]]]]) -> bytes:
    """A made-up room.dzb: each quad (four corners, in order round it) two triangles in a group of its name."""
    points, tris, names = [], [], []
    for name, corners in quads:
        if name not in names:
            names.append(name)
        base = len(points)
        points += corners
        g = names.index(name)
        tris += [(base, base + 1, base + 2, 0, g), (base, base + 2, base + 3, 0, g)]
    at_points = 0x34
    at_tris = at_points + 12 * len(points)
    at_groups = at_tris + 10 * len(tris)
    at_names = at_groups + 0x34 * len(names)
    head = struct.pack(">12I", len(points), at_points, len(tris), at_tris, 0, 0, 0, 0, len(names), at_groups, 0, 0)
    body = bytearray(head.ljust(at_points, b"\0"))
    for p in points:
        body += struct.pack(">3f", *p)
    for t in tris:
        body += struct.pack(">5H", *t)
    text = bytearray()
    for name in names:
        body += struct.pack(">I", at_names + len(text)).ljust(0x34, b"\0")
        text += name.encode() + b"\0"
    return bytes(body + text)


def flat(x0, z0, x1, z1, y):
    """A floor facing up (corners counter-clockwise seen from above, with y up and z toward the viewer)."""
    return [(x0, y, z0), (x0, y, z1), (x1, y, z1), (x1, y, z0)]


def upright(x0, z0, x1, z1, y0, y1):
    """A wall standing on the line from (x0, z0) to (x1, z1), from y0 to y1."""
    return [(x0, y0, z0), (x1, y0, z1), (x1, y1, z1), (x0, y1, z0)]


ROOM = [("jimen", flat(0, 0, 1000, 1000, 0.0)),               # the ground
        ("kabe", upright(500, -10, 500, 800, 0.0, 200.0)),     # a wall across it, open at the far end
        ("dai", flat(600, 0, 1000, 300, 150.0)),               # a shelf too high to step onto
        ("umi", flat(-1000, 0, 0, 1000, -20.0))]               # water beside it, under the floor's level


def test_a_way_goes_round_the_wall_and_not_onto_the_shelf():
    mesh = route.dzb(dzb_of(ROOM))
    assert len(mesh.points) == 16 and {t[3] for t in mesh.triangles} == {"jimen", "kabe", "dai", "umi"}
    g = route.ground(mesh, (-1000, 0, 1000, 1000), cell=25, low=-1)
    found = route.way(g, (100, 0, 100), (550, 0, 100))
    assert "way" in found, found
    corners = found["way"]
    assert max(z for _, z, _ in corners) > 800                     # round the end of the wall
    assert abs(corners[-1][0] - 550) <= 25 and abs(corners[-1][1] - 100) <= 25
    assert all(h < 1 for _, _, h in corners)                       # on the ground, never the shelf
    assert len(corners) <= 6                                       # pulled straight: corners only, not 40 cells
    # Each straight stretch between the corners keeps off the wall's line but at its open end.
    last = (100, 100)
    for x, z, _ in corners:
        if (last[0] - 500) * (x - 500) < 0:
            t = (500 - last[0]) / (x - last[0])
            assert last[1] + t * (z - last[1]) > 800 - 25
        last = (x, z)
    # The shelf's top: no way up a step of 150, but there is one when steps that high are walked.
    assert "why" in route.way(g, (100, 0, 100), (800, 150, 100))
    tall = route.ground(mesh, (-1000, 0, 1000, 1000), cell=25, up=200, low=-1)
    assert "way" in route.way(tall, (100, 0, 100), (800, 150, 100))
    # The water is no floor with `low` above it: a start in it reaches the ground only within `reach`.
    assert route.way(g, (-500, -20, 500), (100, 0, 100), reach=100)["why"].startswith("no floor")
    wet = route.ground(mesh, (-1000, 0, 1000, 1000), cell=25, low=None)
    assert "way" in route.way(wet, (-500, -20, 500), (100, 0, 100), reach=100)


def test_an_archive_s_collision_and_targets():
    archive = rarc_of({"room.dzb": dzb_of(ROOM), "room.dzr": b"\0\0\0\0"})
    mesh = route.from_archive(archive)
    rows = route.targets(mesh, (100, 0, 100), [{"name": "Ab1", "x": 550.0, "y": 0.0, "z": 100.0},
                                               {"name": "Up1", "x": 800.0, "y": 150.0, "z": 100.0}],
                         margin=800, cell=25, low=-1)
    assert rows[0]["name"] == "Ab1" and rows[0]["way"] and "why" in rows[1]
    with pytest.raises(route.RouteError):
        route.from_archive(rarc_of({"room.dzr": b"\0\0\0\0"}))
    with pytest.raises(route.RouteError):
        route.dzb(b"\0" * 8)
