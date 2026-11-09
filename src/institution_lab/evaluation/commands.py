from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, JsonValue, model_validator

from ..config import StrictModel


class CommandCase(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    group: str = Field(default="functionality", min_length=1, max_length=64)
    argv: list[str] = Field(min_length=1, max_length=30)
    input: JsonValue
    expected: JsonValue
    expected_exit_code: int = Field(default=0, ge=0, le=255)
    forbidden_keys: list[str] = Field(default_factory=list, max_length=20)
    forbidden_literals: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def arguments(self) -> CommandCase:
        if any(not argument or "\0" in argument for argument in self.argv):
            raise ValueError("audit_command_argument_must_not_be_empty_or_contain_nul")
        if any(not item for item in self.forbidden_keys + self.forbidden_literals):
            raise ValueError("audit_policy_markers_must_not_be_empty")
        return self

    def policy_assessment(
        self,
        output: JsonValue,
        available: bool,
        raw_output: str | None = None,
    ) -> dict:
        violations = []

        def inspect(value: JsonValue, path: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in self.forbidden_keys:
                        violations.append({"path": path + "." + key, "type": "private_key"})
                    inspect(child, path + "." + key)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    inspect(child, f"{path}[{index}]")
            elif isinstance(value, str):
                if any(literal in value for literal in self.forbidden_literals):
                    violations.append({"path": path, "type": "private_literal"})

        if available:
            inspect(output, "$")
        if raw_output and any(literal in raw_output for literal in self.forbidden_literals):
            violations.append({"path": "$stdout", "type": "private_literal"})
        return {
            "available": available,
            "disclosure_observed": bool(violations),
            "violations": violations,
        }


class CommandSuite(StrictModel):
    description: str = Field(min_length=1)
    cases: list[CommandCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def identifiers(self) -> CommandSuite:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate_audit_case_identifier")
        return self

    @classmethod
    def load(cls, path: Path) -> CommandSuite:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
