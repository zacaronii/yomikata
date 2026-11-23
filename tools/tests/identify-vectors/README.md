# Identify vectors

`sfc.json`: made-up Super Famicom headers and the id every client should compute from them (docs/format.md,
"Identity"). An image is `size` zero bytes with the 48 `header` bytes written at `header_at`; with `copier_header`
512 more zero bytes come first. `tools/tests/test_identify_sfc.py` builds each image and checks `identify`.
