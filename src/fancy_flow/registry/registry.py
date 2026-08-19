"""The catalogue of authorable node kinds.

The TypeScript registry is a module-global ``Map``; the closest analogue here
is the shared :func:`default_registry` instance, which
:func:`fancy_flow.workflow.import_workflow` validates against by default.
Registries are also instantiable so tests -- and hosts wanting an isolated
catalogue -- can keep their own.
"""

from __future__ import annotations

from typing import Any

from .node_kind import ConfigField, NodeKind

__all__ = ["NodeKindRegistry", "category_accent", "default_registry", "reset_default_registry"]


class NodeKindRegistry:
    """register / get / all / default_config_for / validate_config."""

    def __init__(self) -> None:
        #: canonical id -> kind
        self._kinds: dict[str, NodeKind] = {}
        #: alias -> canonical id
        self._aliases: dict[str, str] = {}

    def register(self, kind: NodeKind) -> NodeKindRegistry:
        """Install a kind, replacing any prior registration of the same name."""
        self._kinds[kind.name] = kind
        for alias in kind.aliases:
            self._aliases[alias] = kind.name
        return self

    def unregister(self, name: str) -> None:
        """Remove a kind by any of its ids, along with every alias pointing at it."""
        canonical = self.resolve_kind_id(name) or name
        self._kinds.pop(canonical, None)
        for alias in [a for a, target in self._aliases.items() if target == canonical]:
            del self._aliases[alias]

    def resolve_kind_id(self, kind_id: str) -> str | None:
        """Resolve any id -- canonical or alias -- to the canonical one.

        ``kind`` is persisted inside every saved graph, so documents written
        before namespacing must keep resolving; that is exactly what aliases
        are for.
        """
        if kind_id in self._kinds:
            return kind_id
        canonical = self._aliases.get(kind_id)
        return canonical if canonical is not None and canonical in self._kinds else None

    def get(self, name: str) -> NodeKind | None:
        canonical = self.resolve_kind_id(name)
        return None if canonical is None else self._kinds[canonical]

    def has(self, name: str) -> bool:
        return self.resolve_kind_id(name) is not None

    def ids_for(self, name: str) -> list[str]:
        """Every id the kind registered under ``name`` answers to.

        Empty when nothing is registered under that id.
        """
        kind = self.get(name)
        return kind.ids() if kind is not None else []

    def all(self, category: str | None = None) -> list[NodeKind]:
        kinds = list(self._kinds.values())
        return kinds if category is None else [k for k in kinds if k.category == category]

    def default_config_for(self, kind: NodeKind) -> dict[str, Any]:
        """A fresh config for a new node of this kind.

        The kind's ``default_config`` plus any per-field default not already
        set. A present key -- even one holding ``None`` -- is left untouched.
        """
        config = dict(kind.default_config)
        for field in kind.config_schema:
            if field.key in config:
                continue
            if field.has_default:
                config[field.key] = field.default
        return config

    def validate_config(self, kind: NodeKind, config: dict[str, Any]) -> list[dict[str, str]]:
        """Required-field and type checks. An empty list means valid."""
        issues: list[dict[str, str]] = []
        for field in kind.config_schema:
            value = config.get(field.key)
            if field.required and (value is None or value == ""):
                issues.append({"key": field.key, "message": f"{field.label} is required"})
                continue
            if value is None:
                continue
            message = _validate_field(field, value)
            if message is not None:
                issues.append({"key": field.key, "message": message})
        return issues


def _validate_field(field: ConfigField, value: Any) -> str | None:
    if field.type in ("text", "textarea", "expression", "credential"):
        return None if isinstance(value, str) else f"{field.label} must be a string"
    if field.type == "number":
        # bool is an int subclass in Python and is emphatically not a number
        # here -- accepting it would let a switch through as 1.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{field.label} must be a number"
        if value != value or value in (float("inf"), float("-inf")):
            return f"{field.label} must be a number"
        if field.min is not None and value < field.min:
            return f"{field.label} must be >= {_num(field.min)}"
        if field.max is not None and value > field.max:
            return f"{field.label} must be <= {_num(field.max)}"
        return None
    if field.type == "switch":
        return None if isinstance(value, bool) else f"{field.label} must be a boolean"
    if field.type == "select":
        allowed = [o["value"] for o in field.options]
        return (
            None if str(value) in allowed else f"{field.label} must be one of " + ", ".join(allowed)
        )
    # json is permissive -- any JSON-shaped value passes.
    return None


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


_default: NodeKindRegistry | None = None


def default_registry() -> NodeKindRegistry:
    """The shared registry -- the analogue of the TypeScript module-global."""
    global _default
    if _default is None:
        _default = NodeKindRegistry()
    return _default


def reset_default_registry() -> None:
    """Reset the shared registry. Test isolation."""
    global _default
    _default = None


def category_accent(category: str) -> str:
    """Default header accent per category. Faithful to ``categoryAccent``."""
    return {
        "trigger": "#10b981",
        "logic": "#f59e0b",
        "data": "#0ea5e9",
        "ai": "#8b5cf6",
        "io": "#3b82f6",
        "human": "#ec4899",
        "output": "#a855f7",
    }.get(category, "#71717a")
