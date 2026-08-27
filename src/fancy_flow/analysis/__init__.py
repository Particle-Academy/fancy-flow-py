"""Static analyses over a workflow graph — things decidable without running it."""

from __future__ import annotations

from .graph_connectivity import check_graph_connectivity, may_float

__all__ = ["check_graph_connectivity", "may_float"]
