"""Installed node packages register themselves through one declared seam.

The defect this pins is an ABSENCE. Python shipped no discovery convention, so
a host wanting to find installed node packages had to invent one -- and one did,
scanning ``pkgutil.iter_modules()`` for ``fancy_*`` and guessing at a ``.flow``
submodule. That puts the convention in the host, where the next host writes a
different one, and 23 packages were about to ship against it.

These tests fix the shape so it cannot drift: the group name, the attribute, the
registration order, and -- the part most likely to be quietly dropped in a later
refactor -- that a broken package is REPORTED rather than skipped.
"""

from __future__ import annotations

import sys
import types
from importlib.metadata import EntryPoint

import pytest

from fancy_flow.discovery import (
    ENTRY_POINT_GROUP,
    REGISTER_ATTR,
    DiscoveryResult,
    load_installed_kinds,
)
from fancy_flow.executors import ExecutorRegistry
from fancy_flow.registry import NodeKind, NodeKindRegistry


def _install_module(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)

    for key, value in attrs.items():
        setattr(module, key, value)

    sys.modules[name] = module


@pytest.fixture(autouse=True)
def _clean_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def _patch_entry_points(monkeypatch, entries: list[EntryPoint]) -> None:
    monkeypatch.setattr("fancy_flow.discovery._group_entry_points", lambda: entries)


def test_the_group_name_is_stable():
    # Pinned as a literal. It is the one string 23 packages put in their
    # pyproject.toml, and renaming it silently unregisters all of them --
    # the host boots, the editor is empty, and nothing errors.
    assert ENTRY_POINT_GROUP == "fancy_flow.nodes"
    assert REGISTER_ATTR == "register"


def test_registers_kinds_and_executors_in_one_pass(monkeypatch):
    seen: dict[str, object] = {}

    def register(kinds: NodeKindRegistry, executors: ExecutorRegistry) -> None:
        kind = NodeKind(
            name="@particle-academy/probe_kind",
            category="io",
            label="Probe",
            aliases=("probe_kind",),
        )
        kinds.register(kind)
        executors.bind("@particle-academy/probe_kind", lambda ctx: None)
        seen["kinds"] = kinds
        seen["executors"] = executors

    _install_module("probe_pkg_flow", register=register)
    _patch_entry_points(
        monkeypatch,
        [EntryPoint(name="probe", value="probe_pkg_flow", group=ENTRY_POINT_GROUP)],
    )

    kinds = NodeKindRegistry()
    executors = ExecutorRegistry(kinds=kinds)

    result = load_installed_kinds(kinds, executors)

    assert result.ok
    assert result.loaded == ("probe",)

    # The whole point of one seam: the kind is authorable AND runnable, so the
    # state where a kind reaches the editor with nothing behind it is not
    # reachable by forgetting a second hook.
    assert kinds.get("@particle-academy/probe_kind") is not None
    assert executors.has_kind("@particle-academy/probe_kind")

    # And it filled the registries it was HANDED, not private ones -- bindings
    # made into a registry the runner never consults are bindings that do
    # nothing, silently.
    assert seen["kinds"] is kinds
    assert seen["executors"] is executors


def test_a_broken_package_is_reported_not_skipped(monkeypatch):
    def register(kinds, executors):
        raise RuntimeError("its own import blew up")

    _install_module("broken_pkg_flow", register=register)
    _install_module("fine_pkg_flow", register=lambda kinds, executors: None)

    _patch_entry_points(
        monkeypatch,
        [
            EntryPoint(name="broken", value="broken_pkg_flow", group=ENTRY_POINT_GROUP),
            EntryPoint(name="fine", value="fine_pkg_flow", group=ENTRY_POINT_GROUP),
        ],
    )

    result = load_installed_kinds(NodeKindRegistry(), ExecutorRegistry())

    # A swallowed failure turns "this connector is broken" into "this connector
    # is absent", and absent reads as fine. The good one still loads.
    assert result.loaded == ("fine",)
    assert not result.ok
    assert "broken" in result.failed
    assert isinstance(result.failed["broken"], RuntimeError)
    assert "FAILED" in result.summary()


def test_a_module_without_register_is_a_named_failure(monkeypatch):
    _install_module("shapeless_pkg_flow", RUNNABLE_KINDS=[])

    _patch_entry_points(
        monkeypatch,
        [EntryPoint(name="shapeless", value="shapeless_pkg_flow", group=ENTRY_POINT_GROUP)],
    )

    result = load_installed_kinds(NodeKindRegistry(), ExecutorRegistry())

    assert not result.ok
    failure = result.failed["shapeless"]
    assert isinstance(failure, AttributeError)
    # The message has to name the seam, or the author has to read our source to
    # find out what they were meant to expose.
    assert REGISTER_ATTR in str(failure)


def test_strict_raises_instead_of_collecting(monkeypatch):
    def register(kinds, executors):
        raise RuntimeError("nope")

    _install_module("boom_pkg_flow", register=register)
    _patch_entry_points(
        monkeypatch,
        [EntryPoint(name="boom", value="boom_pkg_flow", group=ENTRY_POINT_GROUP)],
    )

    with pytest.raises(RuntimeError, match="nope"):
        load_installed_kinds(NodeKindRegistry(), ExecutorRegistry(), strict=True)


def test_no_installed_packages_is_a_clean_empty_result(monkeypatch):
    _patch_entry_points(monkeypatch, [])

    result = load_installed_kinds(NodeKindRegistry(), ExecutorRegistry())

    assert result == DiscoveryResult()
    assert result.ok
    assert "0 node package(s) registered" in result.summary()
