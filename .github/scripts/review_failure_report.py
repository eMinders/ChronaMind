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
#: An absolute POSIX path of one or more segments, or a Windows drive path. Applied per whitespace-separated token, so
#: it cannot reach across words; a token carrying a URL scheme is left alone entirely (see ``_without_paths``).
_POSIX_PATH = re.compile(r"(?<![\w:])(?:/[\w.@+-]+)+")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\(?:[\w.@+-]+\\)*[\w.@+-]+")
#: A token that is a URL, not a bare path: redacting inside it corrupts the address and protects nothing extra.
_URL_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")

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
    """Absolute paths replaced with ``<path>``, token by token.

    The run's own text can name a file, and this script's output reaches a public pull request's step summary. Two
    things a single pattern got wrong (re-review of #305): it redacted *inside* a URL -- `https://example.com/v1.2`
    became `https:/<path>` -- and it required two segments, so `/etc` survived. A token that carries a URL scheme is
    therefore left whole, and a one-segment absolute path counts. A bare filename or an excerpt of prose is still not
    redacted: it cannot be told from ordinary words, and the record says so rather than claiming more
    (`evidence/REVIEW-264-T46-independent.md`).
    """
    tokens = []
    for token in text.split(" "):
        if token.lower().startswith("file://"):
            # a `file:` URL *is* a local path: keep the scheme so the reader sees what it was, drop the location
            tokens.append("file://<path>")
            continue
        if _URL_SCHEME.search(token):
            tokens.append(token)
            continue
        tokens.append(_WINDOWS_PATH.sub("<path>", _POSIX_PATH.sub("<path>", token)))
    return " ".join(tokens)


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
