"""Say *why* a review run failed, without publishing the transcript or the prompt.

Why this exists. Run `45846436962` (head `7c4ad14`) failed on all three attempts, and the docs-only run `45852260361`
after it, with

    Claude result reported subtype success with is_error:true (run did not complete successfully)
    Action failed with error: Claude execution failed: result is_error:true

and nothing else: `show_full_output` is false, so the action printed the *shape* of the failure and not the message.
The comment on `#264` then quoted the only line in the log that looked like an error --

    # ("Internal error: directory mismatch for directory .../tsconfig.json")

-- which is a comment in `anthropics/claude-code-action`'s own `run:` block, echoed by the runner's `##[group]Run`
before anything runs, and present in every **successful** run too (`45845609418`, nine turns, `is_error: false`). A
failure nobody can read is how a red job gets a wrong root cause written on its issue.

So on failure this prints five whitelisted fields of the run's own `result` object and a capped excerpt of its
`result` text -- and nothing from `messages`, `content` or the prompt, which is what `show_full_output: true` would
publish. The record stays a diagnosis rather than a transcript. What that guarantee is, exactly: the metadata is
whitelisted, the excerpt is capped and line-collapsed so it cannot forge a runner command, and absolute paths in it are
replaced with `<path>`. A bare filename or an excerpt of the run's prose is *not* redacted -- it cannot be told from
ordinary words -- so the excerpt is the run's own text, and the record says so instead of claiming more. The text is the model's or the CLI's, not ours, so
it is printed inside a `stop-commands` fence: the runner reads `::cmd::` at the start of a line *and* `##[cmd]`
anywhere in one (`actions/runner`, `ActionCommand.TryParse`), and collapsing line breaks alone stops only the first. It exits 0 whatever it finds: a report must never decide the job's conclusion, and
the job has already failed by the time it runs.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

#: Where the action writes its execution output (it names the path in the log: "Log saved to ...").
DEFAULT_OUTPUT = "claude-execution-output.json"
#: The last object of a run, and the only one this script reads. `result` is the message the run ended with.
RESULT_TYPE = "result"
#: The fields printed. Deliberately a whitelist: `messages`, `content`, `toolUseResult` and the prompt are never read,
#: so enabling this cannot become `show_full_output` by another name.
SAFE_FIELDS = ("subtype", "is_error", "num_turns", "duration_ms", "total_cost_usd")
#: How much of the error text is kept. Enough to carry "Invalid API key" or "429"; not enough to carry a turn.
MAX_ERROR_CHARS = 300
#: An absolute path (POSIX, or Windows with a drive) of two or more segments: redacted from the run's own text.
_PATH = re.compile(r"(?:[A-Za-z]:)?(?:[/\\][\w.@+-]+){2,}[/\\]?")

#: The causes that produce `num_turns: 1` with no cost and no model usage -- a run that died before the first answer.
#: Named here so the next reader does not repeat the investigation that produced this file.
CAUSES = (
    "the credential is missing, expired or revoked: secrets.CLAUDE_CODE_OAUTH_TOKEN",
    "the account is over its usage limit for this period",
    "the model in `--model` is not available to that account (vars.CLAUDE_REVIEW_MODEL, else the workflow default)",
)


def _objects(text: str) -> Iterator[Mapping[str, Any]]:
    """Every JSON object in ``text``.

    The pinned action writes **one pretty-printed document** -- ``JSON.stringify(messages, null, 2)`` in
    ``base-action/src/execution-file.ts`` -- so the whole file is parsed first, and only a file that is not one JSON
    document falls back to one object per line. Reading it line by line, as this did, found no object at all in the
    producer's real format and reported `no result` for a file that ended with the failure's own diagnosis (`#264`).
    """
    try:
        document = json.loads(text)
    except ValueError:
        document = None
    if isinstance(document, list | dict):
        yield from _flatten(document)
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        yield from _flatten(value)


def _flatten(value: object) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    elif isinstance(value, Mapping):
        yield value


def result_of(objects: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The run's ``result`` object, or ``None`` when the file holds none (the action died before it wrote one)."""
    for value in reversed(objects):
        if value.get("type") == RESULT_TYPE:
            return value
    return None


def _without_paths(text: str) -> str:
    """Absolute paths replaced with ``<path>``.

    The run's own text can name a file, and this script's output reaches a public pull request's step summary. A path is
    the part worth removing mechanically; a bare filename or an excerpt of prose is not distinguishable from ordinary
    words, so the record says plainly that the excerpt is the run's own text (`evidence/REVIEW-264-T46-independent.md`).
    """
    return _PATH.sub("<path>", text)


def report(result: Mapping[str, Any] | None) -> str:
    """The one-paragraph diagnosis printed on failure, built only from the whitelisted fields."""
    if result is None:
        return (
            "The execution file carries no result object, so this run never reported how it ended: it may have failed "
            "before the model answered, or after work had started and before the closing result was written. The "
            "action's own log is the remaining evidence. Review this pull request by hand and record it on the issue "
            "(`.claude/skills/review-pr/SKILL.md` is the procedure)."
        )
    fields = ", ".join(f"{name}={result.get(name)!r}" for name in SAFE_FIELDS)
    # every line break, not only `\n`: the runner also ends a line at `\r`, and a line starting `::` is a workflow command
    error = _without_paths(" ".join(str(result.get("result") or "").split())[:MAX_ERROR_CHARS])
    # what the run is known to have done is what it reports; a failed run that billed turns reviewed something
    lines = [f"The review run failed ({fields})."]
    if error:
        lines.append(f"The run's own last message: {error}")
    if result.get("num_turns") == 1 and not result.get("total_cost_usd"):
        lines.append(
            "No turn was billed, so this is the first call to the model, not a review that went wrong. Likely: "
        )
        lines.extend(f"  - {cause}" for cause in CAUSES)
    lines.append(
        "Check `gh run view <id> --log` for the action's own output before writing a cause on the issue: this log line "
        "(`directory mismatch for directory .../tsconfig.json`) is a comment in the action's `run:` block, present in "
        "successful runs too, and is not the error (`#264`)."
    )
    return "\n".join(lines)


def fenced(body: str, token: str) -> str:
    """``body`` between ``::stop-commands::<token>`` and ``::<token>::``: the runner executes no workflow command in it.

    ``token`` must not occur in ``body`` (a fresh random one never does in practice; the guard makes it certain).
    """
    if not token or token in body:
        raise ValueError("stop-commands token must be non-empty and absent from the body")
    return f"::stop-commands::{token}\n{body}::{token}::\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Print the diagnosis of a failed run, and append it to the run's summary when the runner sets one.

    The path is an argument or `$RUNNER_TEMP/``claude-execution-output.json``. A missing or unreadable file is not an
    error: the action writes it after it has output to write, so its absence is itself the finding. Always returns 0 —
    the job has already failed, and a report must not change what the run means.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    path = Path(arguments[0]) if arguments else Path(os.environ.get("RUNNER_TEMP", ".")) / DEFAULT_OUTPUT
    try:
        objects = list(_objects(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        objects = []
    message = report(result_of(objects))
    # the annotation carries only the whitelisted fields; the run's own text stays below, inside the fence
    print(f"::error::{message.splitlines()[0]}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    body = f"### The review did not run\n\n{message}\n"
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(body)
    sys.stdout.write(fenced(body, secrets.token_hex(16)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
