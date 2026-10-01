"""Non-browser contracts for public facades retained during package splits."""

from __future__ import annotations

import importlib

from tests.helpers.package_compatibility import SUBSYSTEM_COMPATIBILITY


def _resolve(path: str):
    module_name, attribute = path.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), attribute)


def test_split_subsystem_public_facades_keep_exact_object_identity():
    for facade_name, exports in SUBSYSTEM_COMPATIBILITY.items():
        facade = importlib.import_module(facade_name)
        for public_name, owner_path in exports.items():
            assert getattr(facade, public_name) is _resolve(owner_path), (facade_name, public_name)


def test_split_subsystem_owners_do_not_define_pytest_nodes():
    for exports in SUBSYSTEM_COMPATIBILITY.values():
        for owner_path in exports.values():
            owner = importlib.import_module(owner_path.rsplit(".", 1)[0])
            assert not any(name.startswith("test_") for name in vars(owner)), owner.__name__


def test_test_modules_do_not_import_pytest_nodes_from_other_modules():
    for module_name in ("tests.test_app", "tests.test_browser_layout"):
        module = importlib.import_module(module_name)
        imported = {
            name: value.__module__
            for name, value in vars(module).items()
            if name.startswith("test_")
            and callable(value)
            and getattr(value, "__module__", module_name) != module_name
        }
        assert imported == {}
