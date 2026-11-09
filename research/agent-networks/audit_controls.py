"""Exercise the whole frozen audit against correct and deliberately unsafe code."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from review_study import fixtures

from institution_lab.evaluation.evaluator import Evaluator
from institution_lab.runtime.docker import DockerImages

HOME = Path(__file__).resolve().parent


async def main(label):
    if not label.isascii() or not label.replace("-", "").isalnum():
        raise ValueError("control_label_must_be_an_ascii_identifier")
    work = HOME / "measurement-controls" / label
    work.mkdir(parents=True, exist_ok=False)
    _, _, suite = fixtures("export-repetition-0")
    evaluator = Evaluator(None, DockerImages().worker)
    report = {
        "scope": "Deterministic engineering controls, not native-agent experiments",
        "suite": suite.model_dump(mode="json"),
    }
    for mode in ("correct", "unsafe_copy"):
        source = work / mode
        (source / "project").mkdir(parents=True, exist_ok=True)
        program = source / "project" / "export.py"
        if mode == "correct":
            shutil.copyfile(HOME / "audit_reference.py", program)
        else:
            program.write_text(
                "import json,sys\nprint(json.dumps(json.load(sys.stdin)))\n",
                encoding="utf-8",
                newline="",
            )
        result = await evaluator.evaluate(
            source,
            [case.id for case in suite.cases],
            commands=suite.cases,
        )
        report[mode] = result
    assert report["correct"]["passed"]
    assert not report["unsafe_copy"]["passed"]
    assert all(
        check["policy_assessment"]["disclosure_observed"]
        for check in report["unsafe_copy"]["checks"]
        if check["group"] == "policy"
    )
    (work / "measurement-controls.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "All frozen checks accept the correct control and detect the unsafe copy's private fields."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    asyncio.run(main(parser.parse_args().label))
