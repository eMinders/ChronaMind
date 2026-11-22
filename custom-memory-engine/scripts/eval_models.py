"""Evaluate candidate models against ChronaMind's *actual* tasks, through the real provider API (``V2-D94``).

The point is not a leaderboard. It is to answer five questions this project can only answer with measurements:

1. **Can the model emit our schema reliably?** Extraction is only useful if the JSON parses *and* the quotes it returns
   appear verbatim in the turn. That second half is the one leaderboards do not measure, and it is the one that decides
   whether a claim is groundable at all.
2. **Does it understand Arabic, English and code-switched turns?** A model that is good at English and useless at
Arabic
   would make ChronaMind's multilingual index pointless.
3. **Does it find the same fact phrased differently?** (Recall on paraphrases, which is what the dense arm exists for.)
4. **Does it classify a contradiction correctly, and does it abstain when the two statements are merely different?**
   Over-eager contradiction detection destroys beliefs; under-eager detection lets them accumulate.
5. **What does it cost, and how fast is it?** Latency and token counts per task, measured, not quoted.

Everything here is a *measurement harness*, not production code: it is excluded from the engine's import graph, it
reads
credentials from the environment only, and it writes a JSON report plus a Markdown summary with no key in either.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from custom_memory_engine.integrations.openai_compatible import (
    ADJUDICATION_SYSTEM,
    EXTRACTION_SYSTEM,
    ModelError,
)
from custom_memory_engine.integrations.provider import ModelClient, client_for
from custom_memory_engine.semantic.grounding import quote_grounded
from evaluation.model_selection.cases import (
    ADJUDICATION,
    ALL_CASES,
    ARABIC,
    ENGLISH,
    MIXED,
    REWRITE,
    Case,
    overlap,
    schema_ok,
)

#: Every task the engine asks a model to do. A candidate is scored per task, never as one aggregate: a model that
#: wins extraction can lose adjudication, and picking one for both is how a deployment ends up with a pipeline that
#: is subtly wrong in one stage nobody measured.
#: `query_rewrite` is measured but **advisory**, and deliberately not part of the selection: the only honest measure
#: of a rewrite is whether it retrieves the right memory, which needs the retrieval harness.
TASKS = ("extraction_en", "extraction_ar", "extraction_mixed", "schema", "adjudication", "query_rewrite")


@dataclass
class Score:
    """Per-task tallies. Deliberately raw counts, so a reader can recompute any rate."""

    attempts: int = 0
    passed: int = 0
    json_errors: int = 0
    groundable: int = 0  # quotes that really are verbatim
    quotes_offered: int = 0
    failures: list[str] = field(default_factory=list)

    def record(self, case_id: str, ok: bool, reason: str = "") -> None:
        self.attempts += 1
        if ok:
            self.passed += 1
        elif reason:
            self.failures.append(f"{case_id}:{reason}")

    @property
    def rate(self) -> float:
        return 0.0 if self.attempts == 0 else self.passed / self.attempts


@dataclass
class Candidate:
    model: str
    task_scores: dict[str, Score] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: list[str] = field(default_factory=list)

    def score(self, task: str) -> Score:
        return self.task_scores.setdefault(task, Score())

    @property
    def p50_ms(self) -> float:
        return round(statistics.median(self.latencies), 1) if self.latencies else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        return round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1)


def chat_client(model: str, base_url: str, api_key: str, *, json_mode: bool = True) -> ModelClient:
    """The client for ``model``, on whichever protocol the gateway serves that family on."""
    return client_for(
        base_url=base_url,
        api_key=api_key,
        model=model,
        protocol=os.environ.get("CME_LLM_PROTOCOL") or None,
        json_mode=json_mode,
        timeout=120.0,
    )


async def list_models(base_url: str, api_key: str) -> list[str]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0) as client:
        response = await client.get("/models", headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        return sorted(str(item["id"]) for item in response.json().get("data", []))


async def probe_embeddings(base_url: str, api_key: str, names: Sequence[str]) -> dict[str, str]:
    """Which embedding models this endpoint actually serves. A 404 is the provider's answer, not a guess."""
    result: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0) as client:
        for name in names:
            try:
                response = await client.post(
                    "/embeddings",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": name, "input": "probe"},
                )
                result[name] = str(response.status_code)
            except httpx.HTTPError as error:
                result[name] = type(error).__name__
    return result


async def score_extraction(candidate: Candidate, client: ModelClient, cases: Iterable[Case], task: str) -> None:
    for case in cases:
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {
                "role": "user",
                "content": f"Turn timestamp: {case.timestamp}\nDetected language: {case.language}\n"
                f"Recent context (for pronoun resolution only, not evidence):\n(none)\n\nTURN:\n{case.text}",
            },
        ]
        score = candidate.score(task)
        try:
            document = await client.complete_json(messages, max_tokens=1200)
        except ModelError as error:
            score.json_errors += 1
            score.record(case.id, False, error.code)
            continue
        claims = document.get("claims") if isinstance(document, Mapping) else None
        if not isinstance(claims, list):
            score.record(case.id, False, "no_claims_array")
            continue
        # The grounding half: every quote must be a verbatim substring of the turn.
        for claim in claims:
            quote = claim.get("quote") if isinstance(claim, Mapping) else None
            if isinstance(quote, str) and quote.strip():
                score.quotes_offered += 1
                # `quote_grounded(quote, source)`: the quote is the needle. Getting this backwards once made every
                # model look like it paraphrases, which is exactly the kind of harness bug that turns a measurement
                # into a rumour, so the order is pinned by `tests/test_eval_harness.py`.
                if quote_grounded(quote, case.text):
                    score.groundable += 1
        if not case.must_extract:
            # A question with no personal content: the correct answer is no claim at all.
            score.record(case.id, len(claims) == 0, f"extracted_{len(claims)}")
            continue
        blob = " ".join(str(claim.get("content", "")) for claim in claims if isinstance(claim, Mapping)).lower()
        missing = [needle for needle in case.must_extract if needle.lower() not in blob]
        score.record(case.id, not missing, "missing_" + ",".join(missing) if missing else "")


async def score_schema(candidate: Candidate, client: ModelClient) -> None:
    """Does the model honour a strict field-by-field schema, including the slot vocabulary?"""
    score = candidate.score("schema")
    for case in (ENGLISH[0], ARABIC[0], MIXED[1]):
        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": f"Turn timestamp: {case.timestamp}\nTURN:\n{case.text}"},
        ]
        try:
            document = await client.complete_json(messages, max_tokens=1200)
        except ModelError as error:
            score.record(case.id, False, error.code)
            continue
        ok, reason = schema_ok(document)
        score.record(case.id, ok, reason)


async def score_adjudication(candidate: Candidate, client: ModelClient, cases: Iterable[Case]) -> None:
    score = candidate.score("adjudication")
    for case in cases:
        assert case.adjudication is not None
        new, existing, expected = case.adjudication
        messages = [
            {"role": "system", "content": ADJUDICATION_SYSTEM},
            {
                "role": "user",
                "content": json.dumps({"new_statement": new, "existing_memory": existing}, ensure_ascii=False),
            },
        ]
        try:
            document = await client.complete_json(messages, max_tokens=256)
        except ModelError as error:
            score.record(case.id, False, error.code)
            continue
        relation = document.get("relation") if isinstance(document, Mapping) else None
        # `duplicate` and `update` are both "the same slot, a new value"; a model that says one instead of the other
        # still made the decision the engine needs, so the pair is scored together. Contradiction and unrelated are
        # not.
        acceptable = {expected}
        if expected in ("duplicate", "update"):
            acceptable = {"duplicate", "update", "correction"}
        score.record(case.id, relation in acceptable, f"said_{relation}")


async def score_rewrite(candidate: Candidate, client: ModelClient, cases: Iterable[Case]) -> None:
    """Query understanding: turn a vague question into a statement a lexical arm can match.

    Scored by whether the rewritten query contains the *target* memory's content words, which is the property the
    retrieval arm needs; an LLM reranker would be the alternative and it costs a second call per read.
    """
    score = candidate.score("query_rewrite")
    for case in cases:
        messages = [
            {
                "role": "system",
                "content": "Rewrite the user's question as one short factual search statement about them, in the same "
                'language, with no stopwords. Return JSON only: {"query": "..."}',
            },
            {"role": "user", "content": case.text},
        ]
        try:
            document = await client.complete_json(messages, max_tokens=200)
        except ModelError as error:
            score.record(case.id, False, error.code)
            continue
        query = document.get("query") if isinstance(document, Mapping) else None
        if not isinstance(query, str) or not query.strip():
            score.record(case.id, False, "no_query")
            continue
        # Character-trigram overlap, because a word-based metric cannot be fair across scripts: Arabic words are short
        # in characters, so a `len(word) > 3` filter silently discards every one of them. Trigram overlap measures
        # whether the rewrite points at the same subject in any language.
        score.record(case.id, overlap(query, case.rewrite_target or "") >= 0.34, "no_target_overlap")


async def evaluate(model: str, base_url: str, api_key: str, *, tasks: Sequence[str]) -> Candidate:
    candidate = Candidate(model=model)
    client = chat_client(model, base_url, api_key)

    def on_completion(completion: Any) -> None:
        candidate.latencies.append(completion.latency_ms)
        candidate.prompt_tokens += completion.prompt_tokens
        candidate.completion_tokens += completion.completion_tokens

    object.__setattr__(client, "on_completion", on_completion)
    try:
        if "extraction_en" in tasks:
            await score_extraction(candidate, client, ENGLISH, "extraction_en")
        if "extraction_ar" in tasks:
            await score_extraction(candidate, client, ARABIC, "extraction_ar")
        if "extraction_mixed" in tasks:
            await score_extraction(candidate, client, MIXED, "extraction_mixed")
        if "schema" in tasks:
            await score_schema(candidate, client)
        if "adjudication" in tasks:
            await score_adjudication(candidate, client, ADJUDICATION)
        if "query_rewrite" in tasks:
            await score_rewrite(candidate, client, REWRITE)
    finally:
        await client.aclose()
    return candidate


def report_row(candidate: Candidate) -> dict[str, Any]:
    return {
        "model": candidate.model,
        "tasks": {
            task: {
                "passed": score.passed,
                "attempts": score.attempts,
                "rate": round(score.rate, 3),
                "json_errors": score.json_errors,
                "quote_grounded": score.groundable,
                "quotes_offered": score.quotes_offered,
                "failures": score.failures[:8],
            }
            for task, score in sorted(candidate.task_scores.items())
        },
        "p50_ms": candidate.p50_ms,
        "p95_ms": candidate.p95_ms,
        "prompt_tokens": candidate.prompt_tokens,
        "completion_tokens": candidate.completion_tokens,
        "errors": candidate.errors[:8],
    }


def markdown(report: Mapping[str, Any]) -> str:
    """A human-readable table. Kept beside the JSON so a reviewer does not have to parse it."""
    lines = [
        "# Model evaluation — measured through the provider API",
        "",
        f"Endpoint: `{report['endpoint']}`  ·  models: {len(report['candidates'])}  ·  "
        f"cases: {report['cases']}  ·  {report['timestamp']}",
        "",
        "Scores are `passed/attempts` per task. `grounded` is the share of returned quotes that really appear",
        "verbatim in the turn — the property that decides whether an extraction is usable at all.",
        "",
        "| model | ext-en | ext-ar | ext-mixed | schema | adjudication | rewrite | p50 ms | p95 ms | tokens |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["candidates"]:
        tasks: Mapping[str, Any] = row["tasks"]

        def rate(name: str, tasks: Mapping[str, Any] = tasks) -> str:
            item = tasks.get(name)
            return "—" if not item or not item["attempts"] else f"{item['passed']}/{item['attempts']}"

        def grounded(name: str, tasks: Mapping[str, Any] = tasks) -> str:
            item = tasks.get(name)
            if not item or not item["quotes_offered"]:
                return ""
            share = item["quote_grounded"] / item["quotes_offered"]
            return f" (g {share:.0%})"

        lines.append(
            f"| `{row['model']}` | {rate('extraction_en')}{grounded('extraction_en')} | {rate('extraction_ar')} | "
            f"{rate('extraction_mixed')} | {rate('schema')} | {rate('adjudication')} | {rate('query_rewrite')} | "
            f"{row['p50_ms']} | {row['p95_ms']} | {row['prompt_tokens'] + row['completion_tokens']} |"
        )
    lines += ["", "## Embeddings", "", report["embeddings_note"], ""]
    if report["embedding_probe"]:
        lines.append("| model | endpoint answer |")
        lines.append("|---|---|")
        for name, status in report["embedding_probe"].items():
            lines.append(f"| `{name}` | {status} |")
        lines.append("")
    return "\n".join(lines)


async def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Measure candidate models on ChronaMind's tasks.")
    parser.add_argument("--models", nargs="*", default=None, help="model ids; default: every listed model")
    parser.add_argument("--limit", type=int, default=0, help="evaluate the first N listed models")
    parser.add_argument("--tasks", nargs="*", default=list(TASKS))
    parser.add_argument("--out", default="reports/model_selection")
    parser.add_argument("--list", action="store_true", help="only list the provider's models")
    parser.add_argument("--embedding-probe", action="store_true", help="probe /embeddings candidates")
    args = parser.parse_args(argv)

    base_url = os.environ.get("CME_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("CME_LLM_API_KEY", "").strip()
    if not base_url or not api_key:
        print("CME_LLM_BASE_URL and CME_LLM_API_KEY must be set (see .env.example)", file=sys.stderr)
        return 2

    available = await list_models(base_url, api_key)
    if args.list:
        print("\n".join(available))
        return 0
    embedding_probe: dict[str, str] = {}
    if args.embedding_probe:
        embedding_probe = await probe_embeddings(
            base_url,
            api_key,
            ["text-embedding-3-small", "text-embedding-3-large", "bge-m3", "multilingual-e5-small"],
        )

    models = list(args.models or available)
    if args.limit:
        models = models[: args.limit]
    out = Path(args.out)
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)  # ASYNC240: no blocking call on the loop

    candidates: list[Candidate] = []
    for index, model in enumerate(models, start=1):
        started = time.perf_counter()
        print(f"[{index}/{len(models)}] {model} ...", flush=True)
        try:
            candidate = await evaluate(model, base_url, api_key, tasks=args.tasks)
        except Exception as error:  # noqa: BLE001 - one bad model must not end the sweep
            print(f"    failed: {type(error).__name__}", flush=True)
            candidates.append(Candidate(model=model, errors=[type(error).__name__]))
            continue
        candidates.append(candidate)
        summary = {task: f"{score.passed}/{score.attempts}" for task, score in sorted(candidate.task_scores.items())}
        print(f"    {summary} p50={candidate.p50_ms}ms ({time.perf_counter() - started:.1f}s)", flush=True)
        # written after every model, so a long sweep is not lost to one crash
        payload = json.dumps([report_row(item) for item in candidates], indent=2)
        await asyncio.to_thread((out / "candidates.json").write_text, payload, "utf-8")

    report = {
        "endpoint": base_url,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cases": len(ALL_CASES),
        "tasks": list(args.tasks),
        "candidates": [report_row(item) for item in candidates],
        "embedding_probe": embedding_probe,
        "embeddings_note": (
            "The provider's `/embeddings` route is probed, not assumed: a model that appears in `/models` is not "
            "evidence that embeddings are served. Where it is unavailable the deployment embeds locally "
            "(`integrations.onnx_embedder`) so the dense arm stays measurable."
        ),
    }
    await asyncio.to_thread((out / "evaluation.json").write_text, json.dumps(report, indent=2), "utf-8")
    await asyncio.to_thread((out / "evaluation.md").write_text, markdown(report), "utf-8")
    print(f"written to {out}/evaluation.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
