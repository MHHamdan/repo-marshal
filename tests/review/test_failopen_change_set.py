"""Fail-open paths in what the change set and the signals see."""

from __future__ import annotations

import pytest

from helpers import GitRepo
from repomarshal.model import Verdict

AUTH_FILE = "backend/app/auth/sso.py"


@pytest.mark.failopen
@pytest.mark.parametrize("how", ["info-exclude", "gitignore"])
def test_ignored_new_auth_module_evades_security_gates(webapp, how):
    """A new auth module hidden by an ignore rule the agent controls (`.git/info/exclude`, or a
    `.gitignore` edit in the change) is outside the tree digest and the change set. The plan
    loses security.auth_path, so security_scan/integration are never required, yet the code is
    on disk and imported by the visible edit. Visible, the same change is UNKNOWN."""
    w = webapp
    w.repo.write(AUTH_FILE, "def login(user):\n    return True\n")
    w.routine_backend_change()
    if how == "info-exclude":
        (w.root / ".git" / "info").mkdir(exist_ok=True)
        (w.root / ".git" / "info" / "exclude").write_text("sso.py\n")
    else:
        w.repo.write(".gitignore", "sso.py\n")
    res = w.done()
    assert res.verdict != Verdict.PASS, res.reasons


@pytest.mark.failopen
def test_embedded_git_repository_hides_its_content(webapp):
    """A nested git repository becomes a gitlink: its files (here an auth module) never reach the
    signals, and uncommitted edits inside it never change the tree digest."""
    w = webapp
    nested = GitRepo(w.root / "backend" / "app" / "ext")
    nested.write("auth/login.py", "def login(user):\n    return True\n")
    nested.commit("vendored")
    res = w.done()
    assert res.verdict != Verdict.PASS, res.reasons


@pytest.mark.failopen
@pytest.mark.parametrize("how", ["info-attributes", "over-2000-lines"])
def test_hidden_added_lines_drop_the_secrets_gate(webapp, how):
    """A hard-coded password in a code file normally fires security.sensitive_identifier and
    native:secrets-diff FAILs. If the added lines are invisible to the signal (the file is made
    "binary" via `.git/info/attributes`, or the secret sits past the 2000-line cap) the signal
    silently disappears, the secrets gate is SKIPPED and the verdict is PASS."""
    w = webapp
    secret = 'password = "hunter2hunter2hunter2"\n'
    if how == "info-attributes":
        w.append("backend/app/api/routes.py", "\n" + secret)
        (w.root / ".git" / "info").mkdir(exist_ok=True)
        (w.root / ".git" / "info" / "attributes").write_text("*.py -diff\n")
    else:
        w.repo.write("backend/app/big.py", "x = 1\n" * 2000 + secret)
    res = w.done()
    assert res.verdict != Verdict.PASS, res.reasons


def test_visible_secret_fails(webapp):
    """Guard / control for the test above."""
    webapp.append("backend/app/api/routes.py", '\npassword = "hunter2hunter2hunter2"\n')
    res = webapp.done()
    assert res.verdict == Verdict.FAIL, res.reasons


@pytest.mark.failopen
def test_rename_out_of_scope_skips_the_source_scope(webapp):
    """Moving backend/app/api/routes.py into frontend/ removes a backend module, but the signal
    only lists the new path, so every backend gate is SKIPPED ("no changed files under
    backend/"). The broken backend suite never runs; the frontend gates pass: PASS."""
    w = webapp
    w.repo.git("mv", "backend/app/api/routes.py", "frontend/src/routes.py")
    w.script_stub("python", "echo 'ImportError: app.api.routes'\nexit 1")
    res = w.done()
    assert res.verdict == Verdict.FAIL, res.reasons


@pytest.mark.failopen
@pytest.mark.parametrize("path", ["frontend/src/docs/messages.json", "frontend/src/pages/help.mdx"])
def test_non_doc_file_classified_as_docs_only(webapp, path):
    """Any file under a `docs/` segment, and any `.mdx` (compiled to a React component), is
    classified docs.changed only. That suppresses coverage.unclassified, so only
    native:docs-links runs while the (failing) frontend suite is never required."""
    w = webapp
    w.repo.write(path, '{"greeting": "hi"}\n' if path.endswith(".json") else "# Help\n")
    w.stub("npm", exit_code=1, output="1 failed")
    res = w.done()
    assert res.verdict == Verdict.FAIL, res.reasons
