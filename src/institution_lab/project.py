from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path

from .evaluation.workload import Workload
from .runtime.docker import DockerWorker, docker

MODULE = re.compile(r"operations/[A-Za-z_][A-Za-z0-9_]*\.py$")
COAUTHOR = "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"


class ProjectRejected(ValueError):
    pass


def git(directory: Path, *arguments: str) -> str:
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + str(directory.parent / "empty-hooks"),
            "-c",
            "user.name=InstitutionLab",
            "-c",
            "user.email=research@localhost",
            "-c",
            "core.autocrlf=false",
            "-C",
            str(directory),
            *arguments,
        ],
        capture_output=True,
        env=environment,
        timeout=30,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git_command_failed:{arguments[0]}:exit_{result.returncode}")
    return result.stdout.decode("utf-8").strip("\n")


def container_git(worker: DockerWorker, *arguments: str, preserve: bool = False) -> str:
    return docker(
        "exec",
        "--user",
        "1001:1001",
        worker.name,
        "/usr/bin/env",
        "HOME=/workspace/.home",
        "XDG_CONFIG_HOME=/workspace/.home/.config",
        "GIT_CONFIG_GLOBAL=/dev/null",
        "GIT_CONFIG_NOSYSTEM=1",
        "/usr/bin/git",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        "/workspace",
        *arguments,
        strip=not preserve,
    )


class Project:
    def __init__(self, root: Path, workload: Workload | None) -> None:
        self.root = root
        self.canonical = root / "project"
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir(parents=True)
        self.workload = workload
        self.bases: dict[str, str] = {}

    @property
    def publish_root(self) -> str:
        return "operations" if self.workload else "project"

    def initialize(self, manifest: Path | None, inputs: Path | None = None) -> str:
        if self.workload and manifest:
            self.workload.create(manifest, self.canonical)
        else:
            self.canonical.mkdir()
            shutil.copyfile(
                Path(__file__).with_name("assets") / "society.gitignore",
                self.canonical / ".gitignore",
            )
            (self.canonical / "project").mkdir()
        if inputs:
            if not inputs.is_dir():
                raise ValueError("project_inputs_must_be_a_directory")
            for path in inputs.rglob("*"):
                if path.is_symlink() or not path.resolve().is_relative_to(inputs.resolve()):
                    raise ValueError("project_input_symlinks_are_not_supported")
                if path.is_file():
                    if any(part.startswith(".") for part in path.relative_to(inputs).parts):
                        raise ValueError("hidden_project_inputs_are_not_supported")
                    path.read_bytes().decode("utf-8")
            shutil.copytree(inputs, self.canonical / "inputs")
        git(self.canonical, "init", "--initial-branch", "main")
        git(self.canonical, "add", ".")
        git(self.canonical, "commit", "-m", "Fixed experimental starting project", "-m", COAUTHOR)
        return git(self.canonical, "rev-parse", "HEAD")

    def seed(self, worker: DockerWorker) -> None:
        def shared_code_permissions(info: tarfile.TarInfo) -> tarfile.TarInfo:
            info.mode = 0o2770 if info.isdir() else 0o660 | (info.mode & 0o110)
            return info

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for path in self.canonical.iterdir():
                archive.add(path, arcname=path.name, filter=shared_code_permissions)
        process = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "--user",
                "1001:1001",
                worker.name,
                "tar",
                "--no-same-owner",
                "--same-permissions",
                "-xf",
                "-",
                "-C",
                "/workspace",
            ],
            input=buffer.getvalue(),
            capture_output=True,
            timeout=30,
        )
        if process.returncode:
            raise RuntimeError(process.stderr.decode())
        self.bases[worker.agent_id] = git(self.canonical, "rev-parse", "HEAD")

    def snapshot(
        self,
        worker: DockerWorker,
        definitions: dict | None = None,
        task_id: str | None = None,
    ) -> tuple[Path, str, str, str]:
        permitted = {
            file for task in (definitions or {}).values() for file in task.get("files", [])
        }
        changes_allowed = set((definitions or {}).get(task_id, {}).get("files", []))

        def valid(path: str, changed: bool = False) -> bool:
            if self.workload:
                return bool(MODULE.fullmatch(path)) and not Path(path).is_reserved()
            return (
                path in (changes_allowed if changed else permitted) and not Path(path).is_reserved()
            )

        base = self.bases[worker.agent_id]
        tracked = container_git(worker, "diff", "--name-only", "-z", base)
        untracked = container_git(worker, "ls-files", "--others", "--exclude-standard", "-z")
        paths = tracked.split("\0") + untracked.split("\0")
        if any(path and not valid(path, True) for path in paths):
            raise ProjectRejected("protected_or_unsupported_project_path")
        container_git(worker, "add", "-A")
        tree = container_git(worker, "write-tree")
        if not container_git(worker, "ls-tree", tree, "--", self.publish_root):
            raise ProjectRejected("no_task_artifacts")
        changed = container_git(worker, "diff", "--name-only", "-z", base, tree).split("\0")
        if any(path and not valid(path, True) for path in changed):
            raise ProjectRejected("protected_or_unsupported_project_path")
        artifact = self.artifacts / uuid.uuid4().hex
        git(self.root, "clone", "--quiet", "--no-hardlinks", str(self.canonical), str(artifact))
        git(artifact, "checkout", "--detach", base)
        process = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "1001:1001",
                worker.name,
                "/usr/bin/git",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                "/workspace",
                "archive",
                "--format=tar",
                tree,
                "--",
                self.publish_root,
            ],
            capture_output=True,
            timeout=30,
        )
        if process.returncode:
            detail = process.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or f"workspace_archive_failed:exit_{process.returncode}")
        files = {}
        with tarfile.open(fileobj=io.BytesIO(process.stdout)) as archive:
            for entry in archive:
                if entry.isdir():
                    continue
                path = entry.name
                if not valid(path):
                    raise ProjectRejected("protected_or_unsupported_project_path")
                if not entry.isfile():
                    raise ProjectRejected("only_regular_source_files_are_supported")
                stream = archive.extractfile(entry)
                assert stream is not None
                try:
                    files[path] = stream.read().decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ProjectRejected("source_files_must_be_utf8") from error
        for existing in (artifact / self.publish_root).rglob("*"):
            if not existing.is_file():
                continue
            if existing.relative_to(artifact).as_posix() not in files:
                existing.unlink()
        for name, content in files.items():
            (artifact / name).parent.mkdir(parents=True, exist_ok=True)
            (artifact / name).write_text(content, encoding="utf-8", newline="")
        git(artifact, "add", "-A")
        diff = git(artifact, "diff", "--cached", "--binary", base)
        digest = hashlib.sha256(diff.encode()).hexdigest()
        return artifact, base, digest, diff

    def compatible_integration_base(self, artifact: Path, base: str, read_files: set[str]) -> str:
        current = git(self.canonical, "rev-parse", "HEAD")
        if current == base:
            return current

        candidate_changes = set(
            git(artifact, "diff", "--cached", "--name-only", "-z", base).split("\0")
        ) - {""}
        intervening_changes = set(
            git(self.canonical, "diff", "--name-only", "-z", base, current).split("\0")
        ) - {""}
        if intervening_changes & (candidate_changes | read_files):
            raise ProjectRejected("stale_base_overlapping_work_sync_and_resubmit")
        return current

    def evaluation_preview(self, artifact: Path, base: str, current: str) -> Path:
        if base == current:
            return artifact
        preview = self.artifacts / ("evaluation-" + uuid.uuid4().hex)
        git(self.root, "clone", "--quiet", "--no-hardlinks", str(self.canonical), str(preview))
        git(preview, "checkout", "--detach", current)
        patch = self.artifacts / (preview.name + ".patch")
        patch.write_text(
            git(artifact, "diff", "--cached", "--binary", base) + "\n",
            encoding="utf-8",
            newline="",
        )
        if patch.stat().st_size > 1:
            git(preview, "apply", "--check", str(patch))
            git(preview, "apply", "--index", str(patch))
        return preview

    def integrate(self, artifact: Path, base: str, *, current_base: str | None = None) -> str:
        if git(self.canonical, "rev-parse", "HEAD") != (current_base or base):
            raise ValueError("canonical_base_changed")
        patch = self.artifacts / (artifact.name + ".patch")
        patch.write_text(
            git(artifact, "diff", "--cached", "--binary", base) + "\n",
            encoding="utf-8",
            newline="",
        )
        if patch.stat().st_size > 1:
            git(self.canonical, "apply", "--check", str(patch))
            git(self.canonical, "apply", "--index", str(patch))
            git(
                self.canonical,
                "commit",
                "-m",
                "Integrate evaluated proposal " + artifact.name,
                "-m",
                COAUTHOR,
            )
        return git(self.canonical, "rev-parse", "HEAD")

    def sync(self, worker: DockerWorker) -> dict:
        container_git(worker, "add", "-A")
        base = self.bases[worker.agent_id]
        worker_tree = container_git(worker, "write-tree")
        public_tree = git(self.canonical, "rev-parse", "HEAD^{tree}")
        changed = set(
            container_git(
                worker,
                "diff",
                "--cached",
                "--name-only",
                "-z",
                base,
            ).split("\0")
        ) - {""}
        if changed and worker_tree != public_tree:

            def entries(listing: str) -> dict:
                result = {}
                for row in listing.split("\0"):
                    if row:
                        metadata, name = row.split("\t", 1)
                        result[name] = metadata
                return result

            local = entries(container_git(worker, "ls-tree", "-r", "-z", worker_tree))
            published = entries(git(self.canonical, "ls-tree", "-r", "-z", public_tree))
            if any(local.get(name) != published.get(name) for name in changed):
                return {"ok": False, "reason": "unpublished_changes_preserved", "base": base}
        head = git(self.canonical, "rev-parse", "HEAD")
        if head == base:
            return {"ok": True, "head": head}
        bundle = self.artifacts / (uuid.uuid4().hex + ".bundle")
        git(self.canonical, "bundle", "create", str(bundle), "main")
        remote = "/tmp/canonical-" + bundle.stem + ".bundle"
        docker("cp", str(bundle), worker.name + ":" + remote)
        container_git(worker, "fetch", remote, "main")
        container_git(worker, "read-tree", "-m", "-u", worker_tree, head)
        container_git(worker, "checkout", "-B", worker.agent_id, head)
        self.bases[worker.agent_id] = head
        return {"ok": True, "head": head}
