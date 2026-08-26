"""Declarations of authorable node types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final

from ..schema.graph import PortDescriptor

__all__ = ["UNSET", "ConfigField", "EmitsRelation", "NodeKind", "OutputShape"]

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


#: A kind's output shape: a static field list, or a callable of the node's own
#: config. The callable form is the important one -- see ``NodeKind.output_shape``.
OutputShape = list[dict[str, Any]] | Callable[[dict[str, Any]], list[dict[str, Any]] | None]

#: How a kind's output relates to its input. See ``NodeKind.emits``.
#:
#: Every value is TOP-LEVEL by construction -- a relation cannot describe a
#: value nested under a key, which is why ``wait`` declares a field list.
EmitsRelation = str | Callable[[dict[str, Any]], str | None]


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
    #: The FIELDS this kind emits -- not its ports. ``{{ in.text }}`` is a
    #: field; ``outputs`` is where an edge attaches. Different questions, and
    #: only this one answers "does that field exist".
    #:
    #: Three states, and the third is why it is nullable:
    #:   ``None``   -- NOT DECLARED. Nobody has said. Unknown.
    #:   ``[]``     -- declares that it emits no fields.
    #:   a list     -- ``[{"path": "text", "type": "string"}, ...]``
    #:
    #: Collapsing ``None`` into ``[]`` is the bug this field was added to fix:
    #: a consumer reading "no shape" as "emits nothing" refuses a legitimate
    #: ``{{ in.title }}``, and a false rejection cannot be complied with.
    #:
    #: A CALLABLE is a first-class form, not an escape hatch -- a ``user_input``
    #: emits the keys its author defined and a ``system_event`` its event's
    #: payload, and no static list knows either. Read it through
    #: :meth:`output_shape_for`, never directly, so both forms resolve the same
    #: way and a caller cannot handle only the one it met first.
    output_shape: OutputShape | None = None
    #: How this kind's output RELATES to its input, when the relation is what is
    #: knowable rather than a field list.
    #:
    #: ``output_shape`` answers *which fields*; this answers *where they come
    #: from*. Separate because they are separate questions, and one field
    #: carrying sometimes-a-list-sometimes-a-keyword is one a reader handles
    #: only in the form it met first.
    #:
    #:   ``"input"``            emits its input unchanged
    #:   ``"inputs-merged"``    the union of every input's fields
    #:   ``"expression:<key>"`` the shape the expression in THAT config key
    #:                          names -- the key is part of the value, because a
    #:                          consumer hardcoding "the field called
    #:                          expression" has copied our knowledge one level
    #:                          down, which is the thing this removes
    #:   a callable of config   for a kind whose relation depends on its config
    #:
    #: **A relation with no destination can only express a TOP-LEVEL merge.**
    #: ``wait`` returns ``{"waited": …, "duration": …, "input": …}`` -- it NESTS
    #: its input under a key, so a relation there would make a reader accept
    #: ``{{ in.<any inbound field> }}`` at top level, which resolves to nothing
    #: at run time. Read the executor and ask *merge or nest* before assigning
    #: one; under-claiming is free.
    emits: EmitsRelation | None = None

    #: What :meth:`to_dict` writes for a callable-backed shape. A callable
    #: cannot cross a JSON boundary; dropping it would make the manifest say
    #: "no outputShape", which reads as "emits nothing" -- the failure this
    #: field exists to prevent, reintroduced at the serialisation seam.
    DYNAMIC_OUTPUT_SHAPE: ClassVar[str] = "dynamic"

    def output_shape_for(self, config: dict[str, Any]) -> list[dict[str, Any]] | None:
        """The fields emitted for ``config``, or ``None`` when undeclared."""
        if self.output_shape is None:
            return None
        if callable(self.output_shape):
            return self.output_shape(config)
        return list(self.output_shape)

    def emits_for(self, config: dict[str, Any]) -> str | None:
        """The relation for ``config``, or ``None`` when none was declared."""
        if self.emits is None:
            return None
        if callable(self.emits):
            return self.emits(config)
        return self.emits

    def expression_config_key(self, config: dict[str, Any]) -> str | None:
        """The config key an ``expression:`` relation names, or ``None``.

        ``transform`` reads ``config["expression"]``; ``variable`` reads
        ``config["value"]``. A consumer must not assume either.

        NOTE the limit, which over-permits when missed: an expression's shape is
        knowable only when the whole string is a SINGLE reference. Interpolating
        several yields a string with no addressable fields.
        """
        relation = self.emits_for(config)
        prefix = "expression:"
        return relation[len(prefix) :] if relation and relation.startswith(prefix) else None

    def has_dynamic_output_shape(self) -> bool:
        """True when the shape depends on config and cannot be serialised.

        A manifest reader needs this to tell "config-dependent, ask the
        runtime" from "nothing declared" -- the same absent-vs-empty
        distinction, one level down.
        """
        return callable(self.output_shape)

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
            emits=raw.get("emits"),
            # A manifest saying DYNAMIC comes back as a callable yielding None:
            # "a shape exists, and this process cannot resolve it". Keeping the
            # marker string would push that decision onto every caller, and the
            # caller that forgets reads it as a field list.
            output_shape=(
                (lambda _config: None)
                if raw.get("outputShape") == NodeKind.DYNAMIC_OUTPUT_SHAPE
                else raw.get("outputShape")
            ),
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
        if self.emits is not None and not callable(self.emits):
            out["emits"] = self.emits
        if self.output_shape is not None:
            out["outputShape"] = (
                NodeKind.DYNAMIC_OUTPUT_SHAPE
                if callable(self.output_shape)
                else list(self.output_shape)
            )
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
