"""Manufacturing constraint + DFM (Phase 5)."""
from phone_designer.manufacturing.budget import ManufacturingBudget
from phone_designer.manufacturing.processes import (
    ProcessDefinition,
    ProcessRegistry,
    registry as process_registry,
)
from phone_designer.manufacturing.string_eval import safe_eval

__all__ = [
    "ProcessDefinition",
    "ProcessRegistry",
    "process_registry",
    "ManufacturingBudget",
    "safe_eval",
]
