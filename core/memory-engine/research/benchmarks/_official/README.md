# Pinned official scoring subsets

These modules retain exact AST bodies from the official upstream scoring
functions. `source_manifest.json` records immutable repository commits, original
file SHA-256 values, selected functions, archive paths and license hashes. Tests
verify the original archived bytes and function AST equality, then use synthetic
hand-calculated and differential examples. No dataset text is included.

The complete original files in `sources/*.txt` are provenance data, never imported
or executed as modules. Only the reviewed function subsets are executable:

- LongMemEval: `get_anscheck_prompt` from commit
  `9e0b455f4ef0e2ab8f2e582289761153549043fc`, under its retained MIT license.
  OpenAI, backoff, file/CLI processing and inference are excluded. The project
  wrapper takes an explicitly injected local judge for software tests. It
  preserves the official case-insensitive `yes` substring label; this is not a
  stricter or validated project LLM judge.
- LoCoMo: `normalize_answer`, `f1_score`, `f1` and
  `eval_question_answering` from commit
  `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`. The repository's CC BY-NC 4.0
  license and attribution are retained in `LOCOMO_LICENSE.txt`; these files are
  separate third-party material, not MIT own-suite generators. Unused BERTScore,
  ROUGE, provider, file/CLI and other task code are excluded. The count-only
  console `print` is bound to a no-op within this module only; global stdout is
  not redirected. The selected scoring function bodies are otherwise unchanged.

The LoCoMo subset uses NumPy, `regex`, and the exact NLTK 3.8.1 Porter class retained in `porter.py` under its Apache-2.0 license. The exact small `StemmerI` class is also retained, avoiding full-toolkit import side effects and dependencies. Current Porter methods differ from the legacy scorer's algorithm, so substituting them would silently change results. Tests compare the retained class AST against the hash-pinned original; no algorithm rewrite is claimed. The subset
does not load a model or download an NLTK corpus. Dependencies are managed by the
isolated engine environment, not the upstream broad Conda export. The project
wrapper validates strings/categories and returns official QA values at full
precision. Legacy missing-context recall values are discarded; project evidence
metrics preserve missingness separately. Both modules use narrow vendored-code
lint exclusions because stylistic rewrites would obscure exact source parity.

Synthetic scorer tests establish software compatibility only. Actual providers,
judge validation, research freezes and scientific benchmark runs remain gated
project work.
