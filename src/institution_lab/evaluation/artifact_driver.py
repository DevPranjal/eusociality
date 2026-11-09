"""Run agent-defined checks in the isolated evaluator, never a semantic judge."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/workspace")


def evaluate(task):
    checks = []
    for name in task["files"]:
        path = ROOT / name
        valid = (
            path.resolve().is_relative_to(ROOT.resolve())
            and path.is_file()
            and not path.is_symlink()
        )
        checks.append({"file": name, "passed": valid})
    for command in task["checks"]:
        try:
            result = subprocess.run(
                command["argv"],
                cwd=ROOT / "project",
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            checks.append(
                {
                    "command": command["argv"],
                    "passed": result.returncode == 0,
                    "exit_code": result.returncode,
                    "stdout": result.stdout[:10000],
                    "stderr": result.stderr[:10000],
                }
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
            checks.append(
                {
                    "command": command["argv"],
                    "passed": False,
                    "error": type(error).__name__ + ":" + str(error),
                }
            )
    return {
        "operation": task["id"],
        "passed": bool(checks) and all(c["passed"] for c in checks),
        "checks": checks,
        "acceptance": task["acceptance"],
        "semantic_criteria": "require_agent_peer_review_not_automatically_certified",
    }


if __name__ == "__main__":
    print(json.dumps({"evaluator_ready": True}), flush=True)
    print(json.dumps([evaluate(task) for task in json.load(sys.stdin)], sort_keys=True))
