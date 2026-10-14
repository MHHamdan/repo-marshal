"""The judge jobs of the split CI workflow (.github/workflows/ci.yml: rm-plan and dogfood) must
never execute the pull request's code (threat T26, docs/guides/ci.md "Split verification").

Both jobs check out the PR (the merge commit) into the workspace and THEN run their setup steps
there, with the workspace as the current directory. `python -m venv`, `python -m pip` and
`python - <<PY ... import tomllib` all put the current directory first on sys.path, so a
top-level `venv/`, `pip/` or `tomllib.py` in the PR is imported and executed instead of the
standard library / installed module. In the dogfood job that happens before the judge is even
installed, so the PR's code can plant its own `$RUNNER_TEMP/rm-venv/bin/repomarshal` that prints
a PASS verdict and exits 0.

These tests execute the real `run:` scripts from ci.yml with bash, in a fake PR checkout, with
pip forced offline (PIP_NO_INDEX). No network is used either way.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import GIT_ENV, GitRepo

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
JUDGE_JOBS = ("rm-plan", "dogfood")
INSTALL_JUDGE = "Install the judge (RepoMarshal) from the PR's BASE commit"
INSTALL_TOOLS = "Install the gate tools (ruff, pytest) from BASE's dev requirements"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def workflow_step(job: str, step: str) -> dict[str, str]:
    """{'run': script, 'working-directory': ...} of one step, by a small indentation parser
    (PyYAML is not a dev dependency)."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {job}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^  \S", lines[i])), len(lines)
    )
    body = lines[start:end]
    for i, line in enumerate(body):
        if line.strip() == f"- name: {step}":
            base = _indent(line) + 2
            out: dict[str, str] = {}
            j = i + 1
            while j < len(body) and (not body[j].strip() or _indent(body[j]) >= base):
                m = re.match(r"^\s*([A-Za-z-]+):\s*(.*)$", body[j])
                if m and _indent(body[j]) == base:
                    key, value = m.group(1), m.group(2)
                    if value.strip() == "|":
                        block = []
                        j += 1
                        while j < len(body) and (not body[j].strip() or _indent(body[j]) > base):
                            block.append(body[j][base + 2 :])
                            j += 1
                        out[key] = "\n".join(block) + "\n"
                        continue
                    out[key] = value.split(" #", 1)[0].strip()
                j += 1
            return out
    raise AssertionError(f"step {step!r} not found in job {job!r}")


PLANTED = {
    "venv/__init__.py": "",
    "venv/__main__.py": "MARK('venv')\n",
    "pip/__init__.py": "",
    "pip/__main__.py": "MARK('pip')\n",
    "tomllib.py": "MARK('tomllib')\n",
}


def pr_checkout(tmp: Path, markers: Path) -> tuple[Path, str]:
    """A workspace like actions/checkout leaves it: BASE has a pyproject with a dev extra; the
    PR adds top-level modules that shadow venv, pip and tomllib."""
    r = GitRepo(tmp / "workspace")
    r.write(
        "pyproject.toml",
        '[project]\nname = "x"\nversion = "0"\n'
        '[project.optional-dependencies]\ndev = ["pytest>=8"]\n',
    )
    base = r.commit("BASE")
    prelude = (
        "import pathlib, sys\n"
        "def MARK(name):\n"
        f"    pathlib.Path({str(markers)!r}, name).write_text('PR code ran in a judge job')\n"
    )
    for path, code in PLANTED.items():
        r.write(path, prelude + code if code else "")
    r.commit("PR: innocuous-looking helper packages")
    return r.root, base


def run_step(job: str, step: str, tmp: Path) -> tuple[Path, subprocess.CompletedProcess]:
    markers = tmp / "markers"
    markers.mkdir()
    workspace, base = pr_checkout(tmp, markers)
    runner_temp = tmp / "runner-temp"
    runner_temp.mkdir()
    # What the earlier judge-install step leaves for the tools step.
    (runner_temp / "rm-venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, runner_temp / "rm-venv" / "bin" / "python")
    (runner_temp / "rm-base").mkdir()
    shutil.copy(workspace / "pyproject.toml", runner_temp / "rm-base" / "pyproject.toml")
    if step == INSTALL_JUDGE:
        shutil.rmtree(runner_temp / "rm-venv")
        shutil.rmtree(runner_temp / "rm-base")
    shim = tmp / "shim"
    shim.mkdir()
    os.symlink(sys.executable, shim / "python")
    spec = workflow_step(job, step)
    cwd = spec.get("working-directory", "")
    cwd = cwd.replace("${{ runner.temp }}", str(runner_temp)).replace(
        "${{ github.workspace }}", str(workspace)
    )
    env = {
        **GIT_ENV,
        "PATH": f"{shim}{os.pathsep}{GIT_ENV['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_WORKSPACE": str(workspace),
        "BASE_SHA": base,
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }
    script = tmp / "step.sh"
    script.write_text(spec["run"])
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        cwd=cwd or workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    return markers, proc


@pytest.mark.failopen
@pytest.mark.parametrize("job", JUDGE_JOBS)
@pytest.mark.parametrize("step", [INSTALL_JUDGE, INSTALL_TOOLS], ids=["judge", "tools"])
def test_judge_job_setup_never_imports_modules_from_the_pr_checkout(tmp_path, job, step):
    """FAIL-OPEN: `python -m venv` / `python -m pip` / `python - <<PY import tomllib` run with
    the PR checkout as cwd, so a PR file named venv/, pip/ or tomllib.py executes inside the
    judge job, before the judge is installed. It can then install a fake judge that reports
    PASS. Secure: no PR module is ever imported by a judge-job step."""
    markers, proc = run_step(job, step, tmp_path)
    ran = sorted(p.name for p in markers.iterdir())
    assert ran == [], (
        f"{job}/{step}: PR modules executed in a judge job: {ran}\n{proc.stdout}\n{proc.stderr}"
    )


def test_python_m_puts_the_working_directory_first_on_sys_path(tmp_path):
    """The mechanism, independent of the workflow: `python -m pip` from a directory with a
    `pip/` package runs that package, not the installed pip (Python semantics, not a bug in
    RepoMarshal; this guards the premise of the test above)."""
    (tmp_path / "pip").mkdir()
    (tmp_path / "pip" / "__init__.py").write_text("")
    (tmp_path / "pip" / "__main__.py").write_text("print('planted pip')\n")
    out = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.stdout.strip() == "planted pip"
    isolated = subprocess.run(
        [sys.executable, "-I", "-m", "pip", "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "planted pip" not in isolated.stdout


FAKE_VENV_MAIN = """\
import os, pathlib, sys
# `python -m venv "$RUNNER_TEMP/rm-venv"` lands here: build a "judge" that always says PASS.
target = pathlib.Path(sys.argv[-1])
(target / "bin").mkdir(parents=True, exist_ok=True)
py = target / "bin" / "python"
py.write_text("#!/bin/sh\\nexit 0\\n")
rm = target / "bin" / "repomarshal"
rm.write_text(
    "#!/bin/sh\\n"
    "echo '{\\"verdict\\": \\"PASS\\", \\"reasons\\": [\\"all required gates passed\\"]}'\\n"
    "exit 0\\n"
)
for f in (py, rm):
    f.chmod(0o755)
"""


@pytest.mark.failopen
def test_pr_code_can_make_the_dogfood_job_report_pass(tmp_path):
    """FAIL-OPEN (end to end): run the dogfood job's own steps in order on a PR whose tests
    would fail. The PR's `venv/__main__.py` replaces the judge, `pip/` makes the tool install a
    no-op, and the "repomarshal verdict" step exits 0 with a PASS verdict. Secure: the verdict
    step never exits 0 for a PR whose code chose its verdict."""
    r = GitRepo(tmp_path / "workspace")
    r.write(
        "pyproject.toml",
        '[project]\nname = "x"\nversion = "0"\n'
        '[project.optional-dependencies]\ndev = ["pytest>=8"]\n',
    )
    base = r.commit("BASE")
    r.write("venv/__init__.py", "")
    r.write("venv/__main__.py", FAKE_VENV_MAIN)
    r.write("pip/__init__.py", "")
    r.write("pip/__main__.py", "")
    r.write("tests/test_x.py", "def test_x():\n    assert False\n")
    r.commit("PR")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    shim = tmp_path / "shim"
    shim.mkdir()
    os.symlink(sys.executable, shim / "python")
    env = {
        **GIT_ENV,
        "PATH": f"{shim}{os.pathsep}{GIT_ENV['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_WORKSPACE": str(r.root),
        "BASE_SHA": base,
        "RM_EXPECT": "{}",
        "PIP_NO_INDEX": "1",
    }
    last = None
    for step in (INSTALL_JUDGE, INSTALL_TOOLS, "repomarshal verdict"):
        script = tmp_path / "step.sh"
        script.write_text(workflow_step("dogfood", step)["run"])
        last = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            cwd=r.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if last.returncode != 0:
            break
    assert last is not None
    claimed_pass = last.returncode == 0 and '"PASS"' in last.stdout
    assert not claimed_pass, f"dogfood reported PASS chosen by the PR:\n{last.stdout}"
