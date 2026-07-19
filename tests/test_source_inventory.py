import os

from tests import source_inventory


def test_parsed_python_source_reuses_an_unchanged_ast_and_invalidates_on_identity_change(tmp_path, monkeypatch):
    source_inventory.clear_python_source_inventory_cache()
    path = tmp_path / "sample.py"
    path.write_text("value = 1\n", encoding="utf-8")
    original_parse = source_inventory.ast.parse
    calls = []

    def parse(*args, **kwargs):
        calls.append(args[0])
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(source_inventory.ast, "parse", parse)
    first_source, first_tree = source_inventory.parsed_python_source(path)
    second_source, second_tree = source_inventory.parsed_python_source(path)

    assert first_source == second_source == "value = 1\n"
    assert first_tree is second_tree
    assert calls == ["value = 1\n"]

    before = path.stat()
    path.write_text("value = 2\n", encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns + 1, before.st_mtime_ns + 1))
    changed_source, changed_tree = source_inventory.parsed_python_source(path)

    assert changed_source == "value = 2\n"
    assert changed_tree is not first_tree
    assert calls == ["value = 1\n", "value = 2\n"]
