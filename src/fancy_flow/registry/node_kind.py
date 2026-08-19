"""Declarations of authorable node types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from ..schema.graph import PortDescriptor

__all__ = ["UNSET", "ConfigField", "NodeKind"]

#: Sentinel distinguishing "no default declared" from an explicit ``None``.
UNSET: Final = object()


@dataclass(frozen=True, slots=True)
class ConfigField:
    """One field in a kind's config schema -- the form spec shared with the editor.

    ``type`` is one of: text, textarea, number, select, switch, json,
    expression, credential. Attributes irrelevant to a given type stay ``None``.
    """

    type: str
    key: str
    label: str
    required: bool = False
    default: Any = UNSET
    description: str | None = None
    options: tuple[dict[str, str], ...] = ()
    min: float | None = None
    max: float | None = None
    step: float | None = None
    placeholder: str | None = None
    example: str | None = None
    credential_type: str | None = None
    rows: int | None = None
    language: str | None = None

    @property
    def has_default(self) -> bool:
        return self.default is not UNSET

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> ConfigField:
        return ConfigField(
            type=str(raw.get("type", "text")),
            key=str(raw["key"]),
            label=str(raw.get("label", raw["key"])),
            required=bool(raw.get("required", False)),
            default=raw.get("default", UNSET),
            description=_opt_str(raw.get("description")),
            options=_normalize_options(raw.get("options")),
            min=_opt_float(raw.get("min")),
            max=_opt_float(raw.get("max")),
            step=_opt_float(raw.get("step")),
            placeholder=_opt_str(raw.get("placeholder")),
            example=_opt_str(raw.get("example")),
            credential_type=_opt_str(raw.get("credentialType")),
            rows=int(raw["rows"]) if raw.get("rows") is not None else None,
            language=_opt_str(raw.get("language")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "key": self.key, "label": self.label}
        if self.required:
            out["required"] = True
        if self.has_default:
            out["default"] = self.default
        extras = {
            "description": self.description,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "placeholder": self.placeholder,
            "example": self.example,
            "credentialType": self.credential_type,
            "rows": self.rows,
            "language": self.language,
        }
        for key, value in extras.items():
            if value is not None:
                out[key] = value
        if self.options:
            out["options"] = [dict(o) for o in self.options]
        return out


@dataclass(frozen=True, slots=True)
class NodeKind:
    """An authorable node type -- its shape, ports and config schema.

    ``inputs`` / ``outputs`` are nullable to preserve the "not declared" vs
    "declared empty" distinction the engine reads.
    """

    name: str
    category: str
    label: str
    description: str | None = None
    icon: str | None = None
    accent: str | None = None
    config_schema: tuple[ConfigField, ...] = ()
    default_config: dict[str, Any] = field(default_factory=dict)
    inputs: tuple[PortDescriptor, ...] | None = None
    outputs: tuple[PortDescriptor, ...] | None = None
    aliases: tuple[str, ...] = ()
    #: Declares that this kind halts the run to wait for a person, and what for
    #: -- ``approval``, ``input``, or a node's own (``signature``, ``payment``).
    #: Only a declaration; the executor still emits the pause. Its value is
    #: that it is readable WITHOUT running the graph, so a host learns it needs
    #: a resume path before the first run parks itself forever.
    pauses_for_human: str | None = None
    #: What re-running this node costs -- ``none``, ``idempotent``, or
    #: ``unsafe-to-replay``. A durable run RETRIES; ``unsafe-to-replay`` is the
    #: node saying a second attempt is not a repeat of the first (git_pr_open
    #: opens a second pull request). Only the durable driver consults it.
    side_effects: str | None = None

    def ids(self) -> list[str]:
        """Every id this kind answers to, canonical first.

        Anything keyed by kind name -- executor bindings, node-type maps, policy
        allowlists -- must key on ALL of these: a host that bound an executor
        under the bare name has to keep working, or a rename is a breaking
        change in disguise.
        """
        seen: dict[str, None] = {self.name: None}
        for alias in self.aliases:
            seen.setdefault(alias, None)
        return list(seen)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> NodeKind:
        return NodeKind(
            name=str(raw["name"]),
            category=str(raw.get("category", "custom")),
            label=str(raw.get("label", raw["name"])),
            description=_opt_str(raw.get("description")),
            icon=_opt_str(raw.get("icon")),
            accent=_opt_str(raw.get("accent")),
            config_schema=tuple(ConfigField.from_dict(f) for f in raw.get("configSchema", ())),
            default_config=dict(raw.get("defaultConfig") or {}),
            inputs=_ports(raw, "inputs"),
            outputs=_ports(raw, "outputs"),
            aliases=tuple(str(a) for a in (raw.get("aliases") or ())),
            pauses_for_human=_opt_str(raw.get("pausesForHuman")),
            side_effects=_opt_str(raw.get("sideEffects")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "category": self.category,
            "label": self.label,
        }
        for key, value in (
            ("description", self.description),
            ("icon", self.icon),
            ("accent", self.accent),
        ):
            if value is not None:
                out[key] = value
        if self.config_schema:
            out["configSchema"] = [f.to_dict() for f in self.config_schema]
        if self.default_config:
            out["defaultConfig"] = self.default_config
        if self.inputs is not None:
            out["inputs"] = [p.to_dict() for p in self.inputs]
        if self.outputs is not None:
            out["outputs"] = [p.to_dict() for p in self.outputs]
        if self.aliases:
            out["aliases"] = list(self.aliases)
        if self.pauses_for_human is not None:
            out["pausesForHuman"] = self.pauses_for_human
        if self.side_effects is not None:
            out["sideEffects"] = self.side_effects
        return out


def _ports(raw: dict[str, Any], key: str) -> tuple[PortDescriptor, ...] | None:
    value = raw.get(key, UNSET)
    if value is UNSET or not isinstance(value, (list, tuple)):
        return None
    return tuple(PortDescriptor.from_dict(p) for p in value)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _normalize_options(options: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(options, (list, tuple)):
        return ()
    out = []
    for opt in options:
        if isinstance(opt, dict) and "value" in opt:
            out.append({"value": str(opt["value"]), "label": str(opt.get("label", opt["value"]))})
    return tuple(out)
