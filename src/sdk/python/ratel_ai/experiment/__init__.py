"""Retrieval experiments (experimental).

Define arms over host-supplied async selectors, assign each unit to a stable arm
with a deterministic split, and observe each arm through OpenTelemetry and the
runtime-event stream. The Python mirror of the TypeScript
`experimentalDefineExperiment`. This API may change without a major version bump.
"""

from .definitions import (
    ExperimentArm,
    ExperimentArmContext,
    ExperimentArmOutcome,
    ExperimentArmRole,
    ExperimentArmSelect,
    ExperimentAttributes,
    ExperimentConfig,
    ExperimentEvaluation,
    ExperimentEvaluationReference,
    ExperimentInvocationReference,
    ExperimentInvocationWindow,
    ExperimentRankedItem,
    ExperimentScalar,
    ExperimentSelection,
    ExperimentShadowPolicy,
    ExperimentSplit,
    ExperimentSplitAllocation,
)
from .experiment import Experiment, experimental_define_experiment

__all__ = [
    "Experiment",
    "ExperimentArm",
    "ExperimentArmContext",
    "ExperimentArmOutcome",
    "ExperimentArmRole",
    "ExperimentArmSelect",
    "ExperimentAttributes",
    "ExperimentConfig",
    "ExperimentEvaluation",
    "ExperimentEvaluationReference",
    "ExperimentInvocationReference",
    "ExperimentInvocationWindow",
    "ExperimentRankedItem",
    "ExperimentScalar",
    "ExperimentSelection",
    "ExperimentShadowPolicy",
    "ExperimentSplit",
    "ExperimentSplitAllocation",
    "experimental_define_experiment",
]
