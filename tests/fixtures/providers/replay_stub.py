"""Replays a recorded provider output (stand-in for `skillspector` or `npx` in unit tests).

Controlled by environment variables, all optional:
    REPLAY_LOG        append one JSON line {argv, cwd, env} per invocation
    REPLAY_VERSION    printed for `<tool> --version` (exit 0)
    REPLAY_STDOUT     file whose bytes are written to stdout
    REPLAY_REPORT     file copied to $PLAYWRIGHT_JSON_OUTPUT_FILE
    REPLAY_STDERR     text written to stderr
    REPLAY_SLEEP      seconds to sleep first (timeouts)
    REPLAY_EXIT       exit code (default 0)
"""

import json
import os
import shutil
import sys
import time

ENV_KEYS = (
    "CI",
    "PLAYWRIGHT_JSON_OUTPUT_FILE",
    "PLAYWRIGHT_JSON_OUTPUT_DIR",
    "SKILLSPECTOR_OSV_TIMEOUT",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
)


def main() -> int:
    env = os.environ
    if env.get("REPLAY_LOG"):
        with open(env["REPLAY_LOG"], "a") as fh:
            entry = {
                "argv": sys.argv[1:],
                "cwd": os.getcwd(),
                "env": {k: env.get(k) for k in ENV_KEYS},
            }
            fh.write(json.dumps(entry) + "\n")
    if sys.argv[1:] == ["--version"]:
        print(env.get("REPLAY_VERSION", "unknown"))
        return 0
    if env.get("REPLAY_SLEEP"):
        time.sleep(float(env["REPLAY_SLEEP"]))
    if env.get("REPLAY_STDERR"):
        sys.stderr.write(env["REPLAY_STDERR"] + "\n")
    if env.get("REPLAY_STDOUT"):
        with open(env["REPLAY_STDOUT"], "rb") as fh:
            sys.stdout.buffer.write(fh.read())
    if env.get("REPLAY_REPORT") and env.get("PLAYWRIGHT_JSON_OUTPUT_FILE"):
        shutil.copyfile(env["REPLAY_REPORT"], env["PLAYWRIGHT_JSON_OUTPUT_FILE"])
    return int(env.get("REPLAY_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main())
