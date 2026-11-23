"""`font` on made-up tiles: cutting, finding the base from a text buffer's cells, pairing halves, the skeleton."""

import numpy as np

from yomikata import font


def planar(tile: np.ndarray, bpp: int) -> bytes:
    out = bytearray(8 * bpp)
    for plane in range(bpp):
        for row in range(8):
            out[16 * (plane // 2) + 2 * row + plane % 2] = int(np.packbits((tile[row] >> plane) & 1)[0])
    return bytes(out)


def made_up(seed: int, bpp: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 1 << bpp, (8, 8)).astype(np.uint8)


def test_planar_and_packed_tiles_round_trip():
    for bpp in (2, 4):
        want = [made_up(n, bpp) for n in range(5)]
        vram = bytes(0x40) + b"".join(planar(t, bpp) for t in want)
        got = font.tiles(vram, 0x40, bpp, 5)
        assert all((g == w).all() for g, w in zip(got, want))
    t = made_up(9, 4)
    packed = bytes(int(t[r, 2 * i]) | int(t[r, 2 * i + 1]) << 4 for r in range(8) for i in range(4))
    assert (font.tiles(packed, 0, 4, 1, packed=True)[0] == t).all()


def test_the_base_is_found_from_the_cells_of_a_text_buffer():
    vram = bytearray(np.random.default_rng(1).integers(0, 256, 0x4000).astype(np.uint8).tobytes())
    base = 0x1800
    for n in range(0x60):
        tile = np.full((8, 8), 3, np.uint8) if n == 0x40 else made_up(100 + n, 2)
        vram[base + 16 * n:base + 16 * n + 16] = planar(tile, 2)
    cells = [0x40] * 20 + [0x41, 0x45, 0x52, 0x41, 0x58, 0x5F]
    found = font.find_base(bytes(vram), cells)
    assert found[0] == (base, 5)


def test_two_tile_characters_and_the_skeleton():
    glyphs = np.stack([np.full((8, 8), n, np.uint8) for n in range(64)])
    tall = font.pair(glyphs, 16)
    assert sorted(tall) == list(range(16)) + list(range(32, 48)) and tall[3].shape == (16, 8)
    assert (tall[3][:8] == 3).all() and (tall[3][8:] == 19).all()
    text = font.skeleton([0xC7, 0xC8, 0x40], 2, {0xC7: "だ"}, blanks={0x40}, attributes=[0, 0x1800])
    assert text.splitlines() == ["C700=だ", ";C800=", ";4000=    (blank)", "; the same with 0x1800 set",
                                 "C718=だ", ";C818=", ";4018=    (blank)"]
    assert font.known_pairs("== u16le: 2 codes\nC720=だ\nC800=な    conflict: also に\nnoise", 2, mask=0x3FF) == {0xC7: "だ", 0xC8: "な"}


def test_a_grid_in_a_ps1_texture_and_the_sheet():
    vram = bytearray(1024 * 512 * 2)
    row = 40
    vram[row * 2048 + 8] = 0x21                         # pixels 16 and 17 of that row: colours 1 and 2
    cells = font.grid(bytes(vram), 12, 36, 12, 12, 4, 2)
    assert cells.shape == (8, 12, 12) and cells[0][4, 4] == 1 and cells[0][4, 5] == 2 and cells[1].max() == 0
    img = font.sheet({0x100 + n: c for n, c in enumerate(cells)}, columns=4, scale=2)
    assert img.size[0] > 4 * 24 and img.size[1] > 2 * 24
