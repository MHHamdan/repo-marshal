"""BASE anchoring tests (owner: lead; fail-open review #2)."""

import json

from helpers import GitRepo
from repomarshal import anchor


def _repo(tmp_path):
    r = GitRepo(tmp_path / "r")
    r.write("a.txt", "1")
    base = r.commit("base")
    r.git("switch", "-q", "-c", "feature")
    r.write("a.txt", "2")
    work = r.commit("work")
    return r, base, work


def test_first_resolution_is_remembered_and_moving_main_cannot_narrow(tmp_path):
    r, base, work = _repo(tmp_path)
    sha, how = anchor.effective_base(r.root, None)
    assert sha == base and how == "automatic"
    anchor.remember(r.root, sha, explicit=False)
    r.git("update-ref", "refs/heads/main", work)  # the agent fast-forwards main onto its work
    sha2, how2 = anchor.effective_base(r.root, None)
    assert sha2 == base and how2.startswith("remembered")


def test_explicit_base_wins_and_replaces_memory(tmp_path):
    r, base, work = _repo(tmp_path)
    anchor.remember(r.root, base, explicit=False)
    sha, how = anchor.effective_base(r.root, work)
    assert sha == work and how == "explicit"
    anchor.remember(r.root, work, explicit=True)
    state = json.loads((r.root / anchor.STATE_FILE).read_text())
    assert state["feature"] == work


def test_first_value_wins_without_explicit(tmp_path):
    r, base, work = _repo(tmp_path)
    anchor.remember(r.root, base, explicit=False)
    anchor.remember(r.root, work, explicit=False)
    assert json.loads((r.root / anchor.STATE_FILE).read_text())["feature"] == base


def test_corrupt_or_deleted_state_only_restores_automatic(tmp_path):
    r, base, _ = _repo(tmp_path)
    (r.root / ".repomarshal" / "state").mkdir(parents=True)
    (r.root / anchor.STATE_FILE).write_text("{not json")
    assert anchor.effective_base(r.root, None) == (base, "automatic")
    (r.root / anchor.STATE_FILE).write_text(json.dumps({"feature": "f" * 40}))
    assert anchor.effective_base(r.root, None) == (base, "automatic")
