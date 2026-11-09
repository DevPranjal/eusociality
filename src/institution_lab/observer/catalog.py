from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from ..store import Store
from .view import ReadView


class Catalog:
    def __init__(self, selected: Path, root: Path | None = None) -> None:
        self.selected = selected.resolve()
        self.root = (root or selected).resolve()
        self.views: dict[str, ReadView] = {}

    def identifier(self, run: Path) -> str:
        relative = run.resolve().relative_to(self.root).as_posix()
        return "current" if relative == "." else relative

    @property
    def default(self) -> str:
        return self.identifier(self.selected)

    def path(self, identifier: str | None) -> Path:
        key = identifier or self.default
        path = self.root if key == "current" else (self.root / key).resolve()
        if not path.is_relative_to(self.root) or not (path / "events.sqlite").is_file():
            raise FileNotFoundError("unknown_run")
        return path

    def view(self, identifier: str | None) -> ReadView:
        path = self.path(identifier)
        key = self.identifier(path)
        if key not in self.views:
            self.views[key] = ReadView(path)
        return self.views[key]

    def runs(self) -> list[dict]:
        result = []
        databases = []
        for directory, folders, filenames in os.walk(self.root):
            if "events.sqlite" in filenames:
                databases.append(Path(directory) / "events.sqlite")
                folders.clear()
        for database in databases:
            path = database.parent.resolve()
            if not path.is_relative_to(self.root) or not database.resolve().is_relative_to(
                self.root
            ):
                continue
            store = Store(database, readonly=True)
            try:
                with store.connect() as connection:
                    row = connection.execute("SELECT body FROM state WHERE id=1").fetchone()
                    recorded = connection.execute("SELECT min(time) FROM events").fetchone()[0]
                state = json.loads(row["body"]) if row else None
                config_path = path / "resolved.json"
                if not config_path.resolve().is_relative_to(path):
                    raise ValueError("configuration_outside_run")
                config = json.loads(config_path.read_text()) if config_path.exists() else {}
            except (sqlite3.Error, ValueError, OSError) as error:
                result.append(
                    {
                        "id": self.identifier(path),
                        "name": path.name,
                        "status": "unreadable",
                        "error": str(error),
                        "recorded_at": None,
                    }
                )
                continue
            tickets = {
                key: task
                for key, task in (state or {}).get("tickets", {}).items()
                if task["status"] != "withdrawn"
            }
            result.append(
                {
                    "id": self.identifier(path),
                    "name": path.name,
                    "status": state["status"] if state else "setup_failed",
                    "population": len((state or {}).get("agents", {})),
                    "verified": sum(ticket["status"] == "complete" for ticket in tickets.values()),
                    "total": len(tickets),
                    "coordination": config.get("coordination"),
                    "enforcement": config.get("enforcement"),
                    "started_at": config.get("started_at"),
                    "recorded_at": recorded,
                    "intervened": bool((state or {}).get("interventions")),
                }
            )
        return sorted(
            result,
            key=lambda item: item.get("started_at") or item.get("recorded_at") or 0,
            reverse=True,
        )
