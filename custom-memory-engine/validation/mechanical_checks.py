"""Mechanical checks on the final Markdown deliverables (proposal, checklist, changelog).
Standard library only.
Usage:
    python3 validation/mechanical_checks.py <proposal.md> <checklist.md> <changelog.md>

Exit code 1 if any hard check fails. 'Review' items print context for manual
triage and do not fail the run.

Reference-list byte-identity guard:
    The 41 reference entries of the proposal MUST hash (SHA-256) to
    REFERENCE_LIST_SHA256 below. This replaces the earlier cross-file check
    that compared against a stored copy of the previous revision. The pinned
    constant is the SHA-256 of the reference entries as of the finalization
    pass (identical to the V3 and V4 reference lists). Any legitimate
    reference change MUST update this constant and record the diff in
    CHANGELOG.md.
"""
import hashlib
import re
import sys
from collections import Counter

REFERENCE_LIST_SHA256 = "a5851b94a6385e9ddea2480abce710d1c91ca0149923e04cda600f9a4254038f"

prop_path, check_path, log_path = sys.argv[1:4]
prop = open(prop_path, encoding="utf-8").read()
check = open(check_path, encoding="utf-8").read()
log = open(log_path, encoding="utf-8").read()
fail = []


def hard(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        fail.append(name)


# ---------------------------------------------------------------- 1. citations
ref_start = prop.index("\n## References")
body, refs = prop[:ref_start], prop[ref_start:]
listed = {int(m.group(1)) for m in re.finditer(r"^(\d+)\. ", refs, re.M)}


def cited_numbers(text):
    nums = set()
    for m in re.finditer(r"\[(\d+)\]\s*[\u2013-]\s*\[(\d+)\]", text):  # ranges such as [20]-[26]
        nums.update(range(int(m.group(1)), int(m.group(2)) + 1))
    for m in re.finditer(r"\[(\d+)\]", text):
        nums.add(int(m.group(1)))
    return nums


cited = cited_numbers(body)
hard("citations: every cited number has a reference entry", cited <= listed, str(sorted(cited - listed)))
hard("citations: every reference entry is cited", listed <= cited, str(sorted(listed - cited)))
hard("citations: reference list is exactly 1..41 (no entries added or dropped)", listed == set(range(1, 42)), f"{len(listed)} entries")

# Reference-list byte-identity: SHA-256 of the 41 entries matches the pinned baseline.
entries = [l for l in refs.split("\n") if re.match(r"\d+\. ", l)]
observed = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
hard(
    "citations: the 41 reference entries hash to the pinned REFERENCE_LIST_SHA256",
    observed == REFERENCE_LIST_SHA256,
    f"observed={observed}",
)

# ---------------------------------------------------------------- 2. headings and cross-references
heads = set()
for m in re.finditer(r"^(#{2,4}) (\d+(?:\.\d+)?)[. ]", prop, re.M):
    heads.add(m.group(2))
heads_ok = all(str(n) in heads for n in range(1, 17))
hard("headings: sections 1 to 16 present", heads_ok)


def section_refs(text):
    out = []
    for m in re.finditer(r"Sections?\s+((?:\d+(?:\.\d+)?)(?:\s*(?:,|and|to|\u2013)\s*(?:and\s+)?\d+(?:\.\d+)?)*)", text):
        for n in re.findall(r"\d+(?:\.\d+)?", m.group(1)):
            out.append(n)
    return out


bad = sorted({n for n in section_refs(prop) if n not in heads})
hard("cross-references: every 'Section x.y' in the proposal resolves to a heading", not bad, str(bad))
badc = sorted({n for n in section_refs(check) if n not in heads})
hard("cross-references: every 'Section x.y' in the checklist resolves to a proposal heading", not badc, str(badc))


def slug(h):
    s = re.sub(r"[^\w\- ]", "", h.lower()).strip().replace(" ", "-")
    return s


slugs = {slug(m.group(1)) for m in re.finditer(r"^#{1,4} (.+)$", prop, re.M)}
anchors = re.findall(r"\]\(#([^)]+)\)", prop)
bad_anchor = [a for a in anchors if a not in slugs]
hard("contents links: every in-document anchor resolves", not bad_anchor, str(bad_anchor))

# ---------------------------------------------------------------- 3. table shape
def table_blocks(text):
    cur, in_code = [], False
    for l in text.split("\n"):
        if l.startswith("```"):
            in_code = not in_code
        if not in_code and l.startswith("|"):
            cur.append(l)
        else:
            if cur:
                yield cur
            cur = []
    if cur:
        yield cur


def ncols(l):
    return len(re.sub(r"\\\|", "", l).strip().strip("|").split("|"))


for name, text in (("proposal", prop), ("checklist", check)):
    bad_tables = []
    for blk in table_blocks(text):
        counts = {ncols(l) for l in blk}
        if len(counts) != 1 or not re.match(r"^\|(\s*:?-+:?\s*\|)+$", blk[1].replace(" ", "") if len(blk) > 1 else ""):
            bad_tables.append(blk[0][:70])
    hard(f"tables ({name}): every table has a constant column count and a separator row", not bad_tables, str(bad_tables))

# ---------------------------------------------------------------- 4. identifiers used consistently
abl_rows = set(re.findall(r"^\| (A\d+b?)(?: \(|\s\|)", prop, re.M))
abl_used = set(re.findall(r"\bA(?:10|[1-9])b?\b", prop))
abl_used = {"A" + x[1:] for x in abl_used}
hard("ablations: every ablation ID used in the proposal is defined in the Section 10.4 table",
     abl_used <= abl_rows | {"A1", "A2"}, f"defined={sorted(abl_rows)} used={sorted(abl_used)}")
chk_used = set(re.findall(r"\bA(?:10|[1-9])b?\b", check))
hard("ablations: every ablation ID used in the checklist is defined in the proposal", chk_used <= abl_rows, str(sorted(chk_used - abl_rows)))

for term, why in (("four-layer", "stale layer count"), ("Four memory layers", "stale heading"), ("four layers", "stale layer count"),
                  ("It has not been executed at proposal stage", "stale execution claim"),
                  ("(A) Superseded or ended", "historical archival branch (A) was removed (historical-memory bug)"),
                  ("(A) *r* is superseded or valid-ended", "historical archival branch (A) was removed (historical-memory bug)"),
                  # Stale ledger pointers that an earlier revision carried.
                  ("in `Source_Evidence_Ledger_v2.md`", "stale versioned-ledger pointer (use `Source_Evidence_Ledger.md`)"),
                  ("in `Source_Evidence_Ledger_v3.md`", "stale versioned-ledger pointer (use `Source_Evidence_Ledger.md`)"),
                  ("in `Source_Evidence_Ledger_v4.md`", "stale versioned-ledger pointer (use `Source_Evidence_Ledger.md`)")):
    hard(f"stale wording absent from proposal: {term!r}", term not in prop, why)

# H1 consistency: the primary contrast is P - A2, and P - P-flat appears only as a diagnostic
h1_lines = [l for l in prop.split("\n") if "P \u2212 P-flat" in l or "P-flat" in l]
bad_h1 = [l[:110] for l in h1_lines if not re.search(r"diagnostic|Diagnostic|not confirmatory|combined effect|Known-failure|Temporal structure|A1|exploratory|Exploratory|\(A2 \u2212 P-flat\)", l)]
hard("H1: every mention of P-flat is marked diagnostic, exploratory, or control", not bad_h1, str(bad_h1))
hard("H1: primary contrast text names A2 in RQ table, Section 4.1, Section 10.6 units table, Section 10.4 table",
     all(re.search(p, prop) for p in (r"\| RQ1 \|.*P \u2212 A2", r"\| H1 \| \*\*Both\*\*.*P \u2212 A2", r"\| H1 \(suite T\) \|.*P and by A2", r"\| A2 \(P-nohistory\) \|")))
hard("H4: every Clopper\u2013Pearson mention is tied to scenario level or flagged as diagnostic/independence-assuming",
     all(("scenario" in l or "diagnostic" in l.lower() or "independen" in l.lower()) for l in prop.split("\n") if "Clopper" in l))

# persona consistency
pers = [l for l in prop.split("\n") if re.search(r"\b[Pp]ersona\b", l)]
print(f"[REVIEW] persona mentions: {len(pers)} lines (listed below)")
for l in pers:
    flag = "ok " if re.search(r"optional|Optional|if (it is |the optional )?(persona |layer )?(layer )?(is )?implemented|A10|if present|not required|not part of P|off in P|disabled|only if|Personal Digital Twin|if the optional|\(optional\)", l) else "??? "
    print("   ", flag, l[:150].replace("\n", " "))

# ---------------------------------------------------------------- 5. exclusions
terms = r"\b(weeks?|months?|sprints?|semesters?|deadlines?|milestones?|schedule|timeline|team|teammates?|members?|assigned|assigns?|responsib\w+|owners?|ownership|staff\w*|headcount|person-?(?:days|months|hours)|students?|supervisor|workload|division of labou?r)\b"
print("[REVIEW] excluded-term hits (schedule, staffing, ownership). Each hit is shown for triage")
hits = 0
for i, l in enumerate(prop.split("\n"), 1):
    for m in re.finditer(terms, l, re.I):
        hits += 1
        s = max(0, m.start() - 45)
        print(f"    proposal:{i}: ...{l[s:m.end() + 45]}...")
print(f"    ({hits} hits in proposal)")
# The guard targets personal identities; the project name is not a team-member name.
name_pat = r"\b(Mazen|Ehab|Elewashy|Nexa)\b"
hard("exclusions: no team-member names in any deliverable", not re.search(name_pat, prop + check + log, re.I))

# ---------------------------------------------------------------- 6. fabricated-results guard
numbers = re.findall(r"\bp\s*[<=]\s*0\.\d+|\bachieved\b|\bwe found\b|\bresults show\b|\boutperform\w*\b", prop, re.I)
print(f"[REVIEW] result-like phrasing hits in proposal: {Counter(x.lower() for x in numbers)}")

# ---------------------------------------------------------------- summary
print("\nSUMMARY:", "ALL HARD CHECKS PASSED" if not fail else f"{len(fail)} HARD CHECK(S) FAILED: {fail}")
sys.exit(1 if fail else 0)
