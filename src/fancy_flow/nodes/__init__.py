"""The built-in executors, grouped by domain.

Every one is overridable: they register through the exact same kind + executor
path a consumer's custom node does. There is no privileged builtin.
"""

from . import ai, data, human, io_, logic, output, structural, support, trigger

__all__ = ["ai", "data", "human", "io_", "logic", "output", "structural", "support", "trigger"]
