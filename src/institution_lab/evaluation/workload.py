from __future__ import annotations

import shutil
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from ..config import StrictModel


class Case(StrictModel):
    input: Any
    expected: Any


class Ticket(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    scope: str
    dependencies: list[str]
    cases: list[Case] = Field(min_length=1)

    def public(self) -> dict:
        return {**self.model_dump(exclude={"cases"}), "module": f"operations/{self.id}.py"}


class Workload(StrictModel):
    description: str
    scaffold: str
    tickets: list[Ticket]

    @model_validator(mode="after")
    def dependency_graph(self) -> Workload:
        names = {ticket.id for ticket in self.tickets}
        if len(names) != len(self.tickets):
            raise ValueError("duplicate_ticket")
        for ticket in self.tickets:
            if not set(ticket.dependencies) <= names:
                raise ValueError("unknown_dependency")
        list(TopologicalSorter({t.id: t.dependencies for t in self.tickets}).static_order())
        return self

    def create(self, manifest: Path, destination: Path) -> None:
        shutil.copytree(manifest.parent / self.scaffold, destination)
        for path in destination.rglob("*"):
            if path.is_file():
                path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        template = (manifest.parent / "stub.py.template").read_text(encoding="utf-8")
        for ticket in self.tickets:
            (destination / "operations" / f"{ticket.id}.py").write_text(
                template, encoding="utf-8", newline="\n"
            )

    @classmethod
    def load(cls, path: Path) -> Workload:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
