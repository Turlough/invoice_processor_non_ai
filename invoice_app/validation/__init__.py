"""Validation package: config-driven line-item validation rules."""

from .project_validations import ProjectValidations
from .strategies import PROJECT_VALIDATION_REGISTRY, ValidationContext

__all__ = [
    "ProjectValidations",
    "ValidationContext",
    "PROJECT_VALIDATION_REGISTRY",
]
