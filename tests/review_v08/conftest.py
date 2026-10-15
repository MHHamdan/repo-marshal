"""Adversarial review of v0.8: SSH-signed waiver codes against self-granted waivers (threat T40):
waiversig.py (message, verify), the signed-code checks in waivers.py (_Signing, _sig_from,
_label_waivers) and policy.py (completion.waiver_signers).

Attacker: a local coding agent holding the maintainer's own `gh` login or token (its labels and
comments carry `performed_via_github_app: null`), able to push, edit the PR, and run any process
as the maintainer, but without the maintainer's signing key (hardware key). BASE and its
allowed-signers file are trusted.

Tests marked `failopen` demonstrate a confirmed fail-open path: they assert the correct
(fail-closed) behaviour and therefore FAIL on the code they were written against. Unmarked tests
are regression guards for attacks that are handled correctly ("defended" in their docstring) or
for legitimate use that must keep working ("usability").

No test touches the network: repositories are local, GitHub is a local HTTP stub, signatures are
made and checked with the real ssh-keygen and throwaway ed25519 keys.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "failopen: demonstrates a confirmed fail-open path (fails until fixed)"
    )
