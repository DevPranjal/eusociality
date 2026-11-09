from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import load_config
from ..paths import relative_file
from ..project import git
from ..recording import redact
from ..runtime.docker import docker
from .view import ReadView

MAX_FILE_BYTES = 1_048_576


def contained_file(root: Path, value: str) -> Path:
    path = root / relative_file(value)
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("file_outside_recorded_source")
    if not path.is_file():
        raise FileNotFoundError("file_not_retained")
    return path


def source_root(
    view: ReadView,
    source: str,
    proposal: str | None,
    actor: str | None = None,
    snapshot: str | None = None,
    at: int | None = None,
) -> Path:
    roots = {
        "prompts": view.run / "prompts",
        "prompt-source": view.run / "prompt-source",
        "workload": view.run / "frozen-workload",
        "source": view.run / "source-snapshot",
        "sources": view.run / "sources",
    }
    if source in roots:
        root = roots[source]
        if not root.resolve().is_relative_to(view.run.resolve()):
            raise ValueError("source_outside_run")
        return root
    if source == "workspace" and actor:
        record = view.workspace(actor, snapshot, at)
        root = view.run / "workspaces" / relative_file(actor) / relative_file(record["id"])
        if not root.resolve().is_relative_to((view.run / "workspaces").resolve()):
            raise ValueError("invalid_workspace_root")
        return root
    if source == "snapshot" and proposal:
        item = view.snapshot(at)["state"].get("proposals", {}).get(proposal)
        if item is None:
            raise LookupError("unknown_proposal")
        artifact = relative_file(item["artifact"])
        root = (view.run / "artifacts" / artifact).resolve()
        if not root.is_relative_to((view.run / "artifacts").resolve()):
            raise ValueError("invalid_snapshot_root")
        return root
    raise ValueError("unknown_file_source")


def files(
    view: ReadView,
    source: str,
    proposal: str | None,
    at: int | None,
    actor: str | None = None,
    snapshot: str | None = None,
) -> dict:
    if source == "project":
        revision, cursor, exact = view.published_at(at)
        root = view.run / "project"
        if not root.is_dir():
            raise FileNotFoundError("project_not_retained")
        entries = []
        for row in git(root, "ls-tree", "-r", "-z", revision).split("\0") if revision else []:
            if not row:
                continue
            metadata, name = row.split("\t", 1)
            mode, kind, _ = metadata.split()
            entries.append({"path": name, "readable": kind == "blob" and mode != "120000"})
        return {
            "files": entries,
            "revision": revision,
            "source": "published-project",
            "cursor": cursor,
            "historical_exact": exact,
        }
    root = source_root(view, source, proposal, actor, snapshot, at)
    if not root.is_dir():
        raise FileNotFoundError("snapshot_not_retained")
    entries = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        excluded = {".git", "__pycache__", "node_modules", ".home", ".tmp"}
        if source != "workspace":
            excluded.add(".notes")
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_file() and path.resolve().is_relative_to(root.resolve()):
            entries.append({"path": relative.as_posix(), "readable": not path.is_symlink()})
    return {
        "files": sorted(entries, key=lambda item: item["path"]),
        "revision": proposal,
        "source": source,
    }


def file_content(
    view: ReadView,
    source: str,
    path: str,
    proposal: str | None,
    at: int | None,
    actor: str | None = None,
    snapshot: str | None = None,
) -> dict:
    name = relative_file(path)
    if source == "project":
        listing = files(view, source, proposal, at)
        if not any(item["path"] == name and item["readable"] for item in listing["files"]):
            raise FileNotFoundError("file_not_in_recorded_revision")
        root = view.run / "project"
        reference = f"{listing['revision']}:{name}"
        if int(git(root, "cat-file", "-s", reference)) > MAX_FILE_BYTES:
            raise ValueError("file_exceeds_viewer_limit")
        result = subprocess.run(
            ["git", "-C", str(root), "show", reference],
            capture_output=True,
            check=True,
            timeout=10,
        )
        content = result.stdout.decode("utf-8")
        revision = listing["revision"]
    else:
        root = source_root(view, source, proposal, actor, snapshot, at)
        file = contained_file(root, name)
        if file.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file_exceeds_viewer_limit")
        content = file.read_bytes().decode("utf-8")
        revision = proposal
    return {
        "path": name,
        "content": redact(content),
        "revision": revision,
        "language": language(name),
        "source": source,
    }


def language(name: str) -> str:
    return {
        ".py": "python",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".css": "css",
        ".html": "html",
    }.get(Path(name).suffix, "plaintext")


def run_configuration(view: ReadView) -> dict:
    path = view.run / "resolved.json"
    if not path.resolve().is_relative_to(view.run.resolve()):
        raise ValueError("configuration_outside_run")
    if not path.exists():
        raise FileNotFoundError("configuration_not_recorded")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    fields = {
        "schema_version",
        "name",
        "coordination",
        "enforcement",
        "population",
        "concurrency",
        "deadline_seconds",
        "max_activations",
        "recoveries",
        "keep_awake",
        "model_overrides",
        "initial_levels",
        "goal",
        "research",
        "resources",
        "agent_roles",
        "agent_parameters",
        "integration_mode",
        "scheduler_mode",
        "quiescent_rechecks",
        "project_inputs",
        "outcome_suite",
        "disclose_peer_roles",
        "runtime",
        "levels",
        "rules",
        "prompt_repository",
        "workload",
    }
    configuration = {key: value for key, value in metadata.items() if key in fields}
    return {"configuration": redact(configuration), "metadata": redact(metadata)}


def configuration_files(root: Path | None) -> list[str]:
    if root is None or not root.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.yaml")
        if path.resolve().is_relative_to(root.resolve()) and not path.is_symlink()
    )


def configuration_file(root: Path, path: str) -> dict:
    file = contained_file(root, path)
    if file.suffix not in {".yaml", ".yml"}:
        raise ValueError("not_a_configuration_file")
    return {
        "path": path,
        "yaml": redact(file.read_text(encoding="utf-8")),
        "configuration": load_config(file).model_dump(mode="json"),
    }


def systems(view: ReadView) -> dict:
    metadata = run_configuration(view)["metadata"]
    snapshot = view.snapshot()
    state = snapshot["state"]
    network = metadata.get("network_name")
    with view.lock:
        sessions = {
            actor: [
                item for item in view.activity.sessions[actor] if item["seq"] <= snapshot["cursor"]
            ]
            for actor in state.get("agents", {})
        }
    agents = []
    candidates = []
    for actor in state.get("agents", {}):
        recorded = sessions[actor]
        name = recorded[-1].get("container_name") if recorded else None
        item = {"actor": actor, "sessions": recorded, "container": name}
        if not name or not network:
            item.update(status="not_recorded", detail="Live container identity was not recorded.")
        elif name != f"{network}-{actor}":
            item.update(status="identity_mismatch", detail="Recorded identity does not match run.")
        else:
            candidates.append(item)
        agents.append(item)
    if candidates:
        template = (
            '{"name":{{json .Name}},"image":{{json .Config.Image}},'
            '"run":{{json (index .Config.Labels "institution-lab.run")}},'
            '"status":{{json .State.Status}},"running":{{json .State.Running}},'
            '"memory_bytes":{{json .HostConfig.Memory}},'
            '"nano_cpus":{{json .HostConfig.NanoCpus}}}'
        )
        try:
            retained = set(
                docker(
                    "container",
                    "ls",
                    "-a",
                    "--filter",
                    f"label=institution-lab.run={network}",
                    "--format",
                    "{{.Names}}",
                    timeout=10,
                ).splitlines()
            )
            names = [item["container"] for item in candidates if item["container"] in retained]
            inspected = (
                [
                    json.loads(line)
                    for line in docker(
                        "inspect", "--format", template, *names, timeout=10
                    ).splitlines()
                ]
                if names
                else []
            )
            by_name = {item["name"].removeprefix("/"): item for item in inspected}
            for item in candidates:
                name = item["container"]
                live = by_name.get(name)
                if name not in retained:
                    item.update(
                        status="removed", detail="Recorded container is no longer retained."
                    )
                elif live is None:
                    item.update(status="inspection_error", detail="No inspection result returned.")
                elif live["run"] != network:
                    item.update(status="identity_mismatch", detail="Live run label does not match.")
                else:
                    item.update(status=live["status"], live=live)
        except (
            RuntimeError,
            FileNotFoundError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as error:
            for item in candidates:
                item.update(status="inspection_error", detail=redact(str(error)))
    return {
        "agents": agents,
        "images": metadata.get("images", {}),
        "versions": metadata.get("versions", {}),
        "network": network,
        "live_not_replayed": True,
    }
