import json

from jsonschema import Draft202012Validator

import layout_vectors
from layout_vectors import HERE, agrees, layouts_of, replay
from test_layout import REPO, entry_with

VECTORS = {p.name: p.read_text(encoding="utf-8") for p in sorted(HERE.glob("*.json"))}


def test_the_files_are_what_the_generator_writes():
    assert VECTORS == layout_vectors.files()            # if not: python tools/tests/layout_vectors.py


def test_the_reference_reader_reads_every_vector():
    assert len(VECTORS) > 20
    for name, text in VECTORS.items():
        v = json.loads(text)
        assert v["name"] + ".json" == name
        assert agrees(replay(v), v["expected"]), name


def test_every_vector_is_a_layout_the_schema_accepts():
    schema = Draft202012Validator(json.loads((REPO / "schema" / "entry.schema.json").read_text(encoding="utf-8")))
    for name, text in VECTORS.items():
        v = json.loads(text)
        for lay in layouts_of(v):
            problems = [e.message for e in schema.iter_errors(entry_with(lay, **v["entry"]))]
            assert not problems, (name, problems)
