"""DuckDuckGo search tool for LangGraph.

Up to 10 web results, built directly on ``ddgs``. It used to come from ``langchain-community``, which is sunset and
unmaintained; that package only wrapped this same ``ddgs`` call. Name, description, input, search defaults and output
format are unchanged, so prompts and traces that mention ``duckduckgo_results_json`` still match. Search errors are
returned to the model as the tool result (``handle_tool_error``) instead of failing the graph.
"""

from ddgs import DDGS
from langchain_core.tools import StructuredTool

MAX_RESULTS = 10


def _search(query: str) -> tuple[str, list[dict[str, str]]]:
    """Run the query; return the model-facing text and the raw results as the artifact."""
    raw = DDGS().text(
        query, region="wt-wt", safesearch="moderate", timelimit="y", max_results=MAX_RESULTS, backend="auto"
    )
    results = [{"snippet": r["body"], "title": r["title"], "link": r["href"]} for r in raw or []]
    text = ", ".join(", ".join(f"{key}: {value}" for key, value in result.items()) for result in results)
    return text, results


duckduckgo_search_tool = StructuredTool.from_function(
    func=_search,
    name="duckduckgo_results_json",
    description=(
        "A wrapper around Duck Duck Go Search. "
        "Useful for when you need to answer questions about current events. "
        "Input should be a search query."
    ),
    response_format="content_and_artifact",
    handle_tool_error=True,
)
