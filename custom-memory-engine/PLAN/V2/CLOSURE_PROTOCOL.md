# Closure protocol — evidence before closure (W3, and every wave after)

An issue is closed **on a written verification record**, and the record must be checkable. W3's first three closures
showed two ways this fails in practice, and one of them was mine:

| What went wrong | Where | What caught it |
|---|---|---|
| The verification summary was written but never posted: `gh issue close` **silently ignores a body**, so the issue closed with only a plan comment | #199, #200 | A later integrity pass, by reading the issue |
| A cited test name was misspelt (`…_for_e036_stages`); the citation pointed at a test that does not exist | #200, #213 (both same root: typed by hand) | `scripts/check_evidence.py` (added with this document) |
| A verification line named a check that exists nowhere ("audit oracles", "Archival oracles 6 passed") | #212, #213 | Reading the repository, not the claim |
| A criterion was ticked with the evidence "**pending #200**" | #212 (in draft, correctly) | The draft state, which is the right use of it |

## The rule

**Evidence before closure.** Do not close an issue until every acceptance criterion has a row in a posted record, and
every row names something that can be checked: a test, a command with its output, or a file at a path.

## The procedure

1. **Write the record to a file first**, never type it into a command. `reports/closure/<issue>-<date>.md`, or `/tmp`
   if it is not worth keeping.
2. **Check its references:**
   ```bash
   uv run python scripts/check_evidence.py <record>
   ```
   It fails on a test name no `def` defines, a file that does not exist, and a `path::test` node that file does not
   define. It **skips** references inside a correction section (a heading containing "correction", "misspelt", "typo",
   "should be", or a `~~struck~~` span), so recording a correction does not trip it.
3. **Post it as a comment**, then close:
   ```bash
   gh issue comment <N> --body-file <record>     # this is what carries the body; `close` does not
   gh issue close <N> --reason completed
   ```
4. **Read the closure back** (`gh issue view <N> --json state`) — the same discipline as reading an admin change back.
   A silent no-op is the failure mode that produced the missing records here.

## What a row must contain

| Column | Required | Why |
|---|---|---|
| criterion number | yes | it must map to the issue's own §10 row |
| what proves it | yes | a test name, a command, or a file + line |
| **the result** | yes | "passed" alone is not a result; the count or the assertion is |
| control / mutation | where the criterion is a guarantee | a test that cannot fail proves nothing |

## Prose is not evidence

A line reading "audit oracles, bitemporal — pass" cannot be checked and was false. If a check has no runnable form,
say so and say what *was* run instead. Where a check does exist but was not run locally, say `CI: <job>` and do not
imply it was run by hand.

## Deferring a criterion

A gap may be deferred, but not silently:
- an unchecked §10 row in the posted record;
- an `E-<task>-F<n>` entry on the tracker (#73 for W3) stating what is missing, the module that would change, why
  configuration cannot fix it, the minimal proposal, and the blocked row;
- the issue **stays open** (`W7`), or — where the deferred part genuinely belongs to a later task — the record names
  that task and the tracker entry exists. `E-038-F5` was deferred this way and the deferral itself was wrong: it cited a
  premise ("wiring would demote every verified abstraction") that a one-line check of the schema would have refuted,
  because nothing writes `verified` at all. **A deferral needs the same verification as a closure.**

## Running it on PLAN documents

`--plan-document` exists and is **advisory**. A PLAN document is not a closure record: it cites audit reports that were
closed, and tests that are *specified but not yet written* (`DELETION_ORDER.md`'s F5/F7/F8/F9/F10 name the tests those
failure modes will need). Do not gate CI on a PLAN scan. Do gate a closure on the strict one.

Running it over `PLAN/V2/*.md` found one citation that *was* a defect: `V2-D55`'s Verification line cited
~~`test_known_v1_gaps_are_reported_not_silently_passed`~~, which the same commit deleted because the gaps it reported were
closed. That is corrected in `DECISIONS_V2.md`; the behaviour it was cited for is still enforced, by `validate`'s
`gap_required`/`owner_required` rules and `test_an_unenforced_row_must_say_what_is_missing_and_who_owns_it`.
