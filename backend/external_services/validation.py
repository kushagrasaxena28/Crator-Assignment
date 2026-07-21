"""Validates action params against the action's STORED input schema — the single source of truth.

There is no second, hand-written validator that could drift from what the schema endpoint serves:
the same `Action.input_schema` JSON is both served to agents and validated against here.
"""

from jsonschema import Draft7Validator


def validate_params(input_schema: dict, params) -> list | None:
    """Return None if `params` satisfy the schema, else a list of {path, message} errors."""
    validator = Draft7Validator(input_schema)
    errors = sorted(validator.iter_errors(params), key=lambda error: list(error.absolute_path))
    if not errors:
        return None
    return [{"path": list(error.absolute_path), "message": error.message} for error in errors]
