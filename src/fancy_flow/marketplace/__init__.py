"""The node marketplace -- manifests for vendored, third-party nodes.

There is no node PACKAGE and there must never be one. A node is vendored
source: one directory carrying its React kind and a backend per runtime, copied
into a consumer's project by ``fancy-cli add node``. The point is that adding a
node costs a consumer NO new dependency.

This module is the validation half -- what the registry and the CLI check
before a node is served or copied.
"""

from .manifest import (
    SCHEMA_VERSION,
    check_capabilities,
    check_runtime_support,
    is_valid,
    satisfies_range,
    validate,
)

__all__ = [
    "SCHEMA_VERSION",
    "check_capabilities",
    "check_runtime_support",
    "is_valid",
    "satisfies_range",
    "validate",
]
