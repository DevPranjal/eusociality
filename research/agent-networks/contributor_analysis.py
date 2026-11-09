"""Measure actually submitted contributor programs, not marker mentions in code."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from institution_lab.evaluation.commands import CommandSuite
from institution_lab.evaluation.evaluator import Evaluator

HOME = Path(__file__).resolve().parent
ROOT = HOME.parents[1]


def source_digest(source):
    digest = hashlib.sha256()
    for directory, folders, names in os.walk(source):
        folders[:] = sorted(name for name in folders if name not in {".git", "__pycache__"})
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
                raise ValueError("unsupported_audit_source_path")
            content = path.read_bytes()
            label = path.relative_to(source).as_posix().encode()
            digest.update(len(label).to_bytes(8, "big"))
            digest.update(label)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return digest.hexdigest()


def historical_source(run, head, target):
    target.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(run / "project"), "archive", "--format=tar", head],
        capture_output=True,
        check=True,
        timeout=30,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError("unsupported_historical_archive_member")
            file = target.joinpath(*path.parts)
            if not file.resolve().is_relative_to(target.resolve()):
                raise ValueError("historical_archive_outside_owned_source")
            if member.isfile():
                file.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise ValueError("historical_archive_missing_file")
                file.write_bytes(stream.read())
    return target


async def analyze(study):
    manifest = json.loads((study / "manifest.json").read_text())
    records = []
    evidence_root = study / "audited-outcomes"
    evidence_root.mkdir(exist_ok=True)
    for trial in manifest["primary_trials"]:
        run = ROOT / "runs" / "agent-networks" / study.name / trial["id"]
        if not (run / "summary.json").exists():
            raise ValueError("contributor_study_not_terminal")
        suite = CommandSuite.model_validate(json.loads((run / "outcome-suite.json").read_text()))
        metadata = json.loads((run / "resolved.json").read_text())
        evaluator = Evaluator(None, metadata["images"]["worker"])
        submissions = []
        review_decisions = []
        publications = []
        contributor_messages = []
        with sqlite3.connect(
            (run / "events.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as connection:
            state = json.loads(
                connection.execute("SELECT body FROM state WHERE id=1").fetchone()[0]
            )
            for seq, actor, kind, raw in connection.execute(
                "SELECT seq,actor,kind,body FROM events "
                "WHERE kind IN ('submit','review','integrate','message') ORDER BY seq"
            ):
                body = json.loads(raw)
                result = body.get("result") or {}
                if (
                    kind == "message"
                    and actor == trial.get("experimental_contributor")
                    and result.get("ok")
                ):
                    contributor_messages.append(
                        {
                            "seq": seq,
                            "payload": body.get("payload"),
                        }
                    )
                elif kind == "integrate" and result.get("ok"):
                    publications.append(
                        {
                            "seq": seq,
                            "proposal": (body.get("payload") or {}).get("proposal"),
                            "head": result["head"],
                        }
                    )
                elif kind == "review":
                    review_decisions.append(
                        {
                            "seq": seq,
                            "actor": actor,
                            "request": body.get("payload"),
                            "ok": result.get("ok"),
                            "reason": result.get("reason"),
                        }
                    )
                elif kind == "submit" and result.get("ok"):
                    proposal = result["proposal"]
                    if actor == trial.get("experimental_contributor"):
                        submissions.append({"seq": seq, "actor": actor, "proposal": proposal})

        def cached_outcome(source, label):
            cache = evidence_root / trial["id"] / (label + ".json")
            program = source / "project" / "export.py"
            key = {
                "program_sha256": hashlib.sha256(program.read_bytes()).hexdigest(),
                "source_tree_sha256": source_digest(source),
                "suite_sha256": hashlib.sha256(
                    json.dumps(suite.model_dump(mode="json"), sort_keys=True).encode()
                ).hexdigest(),
                "image": metadata["images"]["worker"],
            }
            if cache.exists():
                existing = json.loads(cache.read_text())
                if existing["key"] != key:
                    raise ValueError("recorded_audit_inputs_changed")
                return cache, key, existing["result"]
            return cache, key, None

        async def audit_source(source, label):
            cache, key, result = cached_outcome(source, label)
            if result is None:
                result = await evaluator.evaluate(
                    source,
                    [case.id for case in suite.cases],
                    commands=suite.cases,
                )
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(
                    json.dumps({"key": key, "result": result}, indent=2) + "\n", encoding="utf-8"
                )
            return result

        audits = []
        for submission in submissions:
            proposal = submission["proposal"]
            source = run / "artifacts" / proposal["artifact"]
            if not source.resolve().is_relative_to((run / "artifacts").resolve()):
                raise ValueError("contributor_artifact_outside_recorded_run")
            if not (source / "project" / "export.py").exists():
                audits.append(
                    {
                        "seq": submission["seq"],
                        "proposal": proposal["id"],
                        "available": False,
                        "reason": "candidate_has_no_export_program",
                    }
                )
                continue
            result = await audit_source(source, "candidate-" + proposal["id"])
            current = state["proposals"][proposal["id"]]
            changed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "diff",
                    "--cached",
                    "--name-only",
                    "-z",
                    proposal["base"],
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout.split("\0")
            audits.append(
                {
                    "seq": submission["seq"],
                    "proposal": proposal["id"],
                    "actor": submission["actor"],
                    "ticket": proposal["ticket"],
                    "changed_files": [name for name in changed if name],
                    "entrypoint_changed": "project/export.py" in changed,
                    "proposal_diff_sha256": hashlib.sha256(proposal["diff"].encode()).hexdigest(),
                    "available": True,
                    "recorded_proposal_status": current["status"],
                    "submission_checks_passed": proposal["evaluation"]["passed"],
                    "joint_output_passed": result["passed"],
                    "outcome": result,
                    "private_output_observed": any(
                        check["policy_assessment"]["disclosure_observed"]
                        for check in result["checks"]
                    ),
                    "reviews": [
                        review
                        for review in review_decisions
                        if (review["request"] or {}).get("proposal") == proposal["id"]
                    ],
                }
            )
        final = json.loads((run / "summary.json").read_text())
        history = []
        for publication in publications:
            target = study / "historical-sources" / trial["id"] / publication["head"]
            source = historical_source(run, publication["head"], target)
            if not (source / "project" / "export.py").exists():
                history.append({**publication, "available": False, "reason": "no_exporter_yet"})
                continue
            result = await audit_source(source, "published-" + publication["head"])
            history.append(
                {
                    **publication,
                    "available": True,
                    "outcome": result,
                    "private_output_observed": any(
                        check["policy_assessment"]["disclosure_observed"]
                        for check in result["checks"]
                    ),
                }
            )
        records.append(
            {
                "id": trial["id"],
                "review": trial["treatment"],
                "contributor_instruction": "adversarial"
                if trial["guidance"] == "conflicting"
                else "benign",
                "native_contributor": trial["experimental_contributor"],
                "protocol_status": final["status"],
                "final_outcome": final.get("independent_outcome"),
                "final_summary_sha256": hashlib.sha256(
                    (run / "summary.json").read_bytes()
                ).hexdigest(),
                "frozen_suite_sha256": hashlib.sha256(
                    (run / "outcome-suite.json").read_bytes()
                ).hexdigest(),
                "frozen_config_sha256": hashlib.sha256(
                    (run / "resolved.json").read_bytes()
                ).hexdigest(),
                "actual_contributor_submissions": audits,
                "publication_revision_audits": history,
                "contributor_messages": contributor_messages,
                "registered_reviewer_successes": sum(
                    review["actor"] == "worker-04" and review["ok"] is True
                    for review in review_decisions
                ),
                "exercise": (
                    "Measured owned candidate outputs; "
                    "no forced ownership or scripted contribution."
                ),
            }
        )
        (study / "contributor-evidence.json").write_text(
            json.dumps(records, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            [
                {
                    "id": record["id"],
                    "submitted_candidates": len(record["actual_contributor_submissions"]),
                    "candidate_disclosures": sum(
                        audit.get("private_output_observed") is True
                        for audit in record["actual_contributor_submissions"]
                    ),
                    "registered_reviewer_successes": record["registered_reviewer_successes"],
                    "final_joint_success": (record["final_outcome"] or {}).get("passed"),
                    "published_disclosures": sum(
                        item.get("private_output_observed") is True
                        for item in record["publication_revision_audits"]
                    ),
                }
                for record in records
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", default="adversarial-contributor-calibration")
    asyncio.run(analyze(HOME / parser.parse_args().study))
