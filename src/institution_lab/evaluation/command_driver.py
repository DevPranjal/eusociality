"""Execute supplied command inputs; expected outcomes never enter this container."""

import json
import subprocess
import sys
from pathlib import Path


def evaluate(case):
    raw = None
    truncated = False
    try:
        process = subprocess.run(
            case["argv"],
            input=json.dumps(case["input"]),
            text=True,
            capture_output=True,
            cwd=Path("/workspace/project"),
            timeout=20,
            check=False,
        )
        raw = process.stdout
        if len(raw.encode("utf-8")) > 100000:
            truncated = True
            raw = raw[:100000]
            raise ValueError("audit_command_output_limit")
        outputs = json.loads(process.stdout)
        return {
            "operation": case["id"],
            "outputs": outputs,
            "exit_code": process.returncode,
            "failure": None,
            "stderr": process.stderr[:2000],
            "stdout": raw,
            "output_truncated": False,
        }
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError) as error:
        return {
            "operation": case["id"],
            "outputs": None,
            "exit_code": None,
            "failure": type(error).__name__ + ":" + str(error),
            "stdout": raw,
            "output_truncated": truncated,
        }


if __name__ == "__main__":
    print(json.dumps({"evaluator_ready": True}), flush=True)
    print(json.dumps([evaluate(case) for case in json.load(sys.stdin)], sort_keys=True))
