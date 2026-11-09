import importlib.util
from pathlib import Path

from institution_lab.project import COAUTHOR, Project, git

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "contributor_evidence",
    ROOT / "research" / "agent-networks" / "contributor_analysis.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_audit_cache_key_covers_helpers_and_inputs_not_only_entrypoint(tmp_path):
    source = tmp_path / "source"
    (source / "project").mkdir(parents=True)
    (source / "inputs").mkdir()
    (source / "project" / "export.py").write_text("import helper\n")
    helper = source / "project" / "helper.py"
    helper.write_text("VALUE = 1\n")
    fixture = source / "inputs" / "record.json"
    fixture.write_text('{"id":"one"}\n')
    original = MODULE.source_digest(source)
    helper.write_text("VALUE = 2\n")
    assert MODULE.source_digest(source) != original
    helper.write_text("VALUE = 1\n")
    assert MODULE.source_digest(source) == original
    fixture.write_text('{"id":"two"}\n')
    assert MODULE.source_digest(source) != original


def test_historical_audit_reads_recorded_revision_not_current_files(tmp_path):
    project = Project(tmp_path / "run", None)
    project.initialize(None)
    program = project.canonical / "project" / "export.py"
    program.write_text("print('original')\n", newline="")
    git(project.canonical, "add", "-A")
    git(project.canonical, "commit", "-m", "Controlled original version", "-m", COAUTHOR)
    old = git(project.canonical, "rev-parse", "HEAD")
    program.write_text("print('changed')\n", newline="")
    git(project.canonical, "add", "-A")
    git(project.canonical, "commit", "-m", "Controlled later version", "-m", COAUTHOR)
    snapshot = MODULE.historical_source(project.root, old, tmp_path / "historical")
    assert (snapshot / "project" / "export.py").read_text() == "print('original')\n"
    assert program.read_text() == "print('changed')\n"
