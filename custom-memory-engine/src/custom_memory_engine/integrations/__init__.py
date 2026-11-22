"""Adapters that connect the engine to the outside world, and the seam that keeps them optional (``V2-D94``).

Nothing in this package is imported by the engine's own modules: the engine takes a
:class:`~custom_memory_engine.semantic.contracts.StructuredExtractor`, an ``Embedder`` or a ``RelationAdjudicator``
and never learns which process, provider or file supplied it. That is
``V2-D88``'s rule ("the LLM owns understanding, deterministic code owns every check") expressed as a dependency
direction, and it is why these adapters can be swapped, dropped or vendored without touching a single engine module.

Two adapters exist, for two different reasons:

* :mod:`~custom_memory_engine.integrations.openai_compatible` speaks the OpenAI HTTP shape with ``httpx`` only. No
  provider SDK is imported anywhere in this project: a generic client over a documented wire format is smaller than an
  SDK, has no transitive dependency tree, and cannot pin the engine to one vendor or one model name.
* :mod:`~custom_memory_engine.integrations.onnx_embedder` runs a local sentence-embedding model with
  ``onnxruntime``. It exists because the embedding API is not always available (the provider this project was
  evaluated against returns ``404 Endpoint not supported`` for ``/embeddings``), and an engine whose only retrieval
  arms are lexical cannot answer a paraphrase.
"""

from __future__ import annotations

__all__: list[str] = []
