"""Actual loopback HTTP verifies serialization, retry handling and secret hygiene."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from evaluation.baselines.contracts import Message
from evaluation.baselines.runtime import MeteredServices, OpenAITransport
from evaluation.baselines.tests.test_runtime import Embedder, Tokenizer, maximum, response, settings
from evaluation.harness.budget import AccountedOperationError


@contextmanager
def local_api(statuses):
    requests = []
    replies = iter(statuses)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, self.headers.get("Authorization"), json.loads(body)))
            status, value = next(replies)
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_http_retry_serializes_frozen_request_and_accounts_both_attempts():
    with local_api([(429, {"error": "rate_limited"}), (200, response())]) as (endpoint, received):
        selected = settings().model_copy(update={"provider_attempts": 2, "send_seed": True})
        transport = OpenAITransport(
            "synthetic-loopback-key", timeout=3, endpoint=endpoint, allow_loopback_test_endpoint=True
        )
        services = MeteredServices(selected, Tokenizer(), Embedder(), transport)
        with services.scope(maximum()) as scope:
            services.complete((Message(role="user", content="fictional input"),))
    assert len(received) == 2 and scope.usage.output_tokens == 2050
    for path, authorization, body in received:
        assert path == "/v1/chat/completions" and authorization == "Bearer synthetic-loopback-key"
        assert body["model"] == selected.answer_model and body["seed"] == 42
        assert body["max_completion_tokens"] == 2048 and body["store"] is False
    assert "synthetic-loopback-key" not in json.dumps(services.operation_metrics())


def test_actual_http_rejection_retains_bound_without_response_or_secret_in_error():
    with local_api([(401, {"error": "private diagnostic"})]) as (endpoint, received):
        transport = OpenAITransport(
            "synthetic-loopback-key", timeout=3, endpoint=endpoint, allow_loopback_test_endpoint=True
        )
        services = MeteredServices(settings(), Tokenizer(), Embedder(), transport)
        with services.scope(maximum()) as scope:
            with pytest.raises(AccountedOperationError) as failure:
                services.complete((Message(role="user", content="fictional input"),))
    assert len(received) == 1 and scope.usage.output_tokens == 2048
    assert "private diagnostic" not in str(failure.value)
    assert "synthetic-loopback-key" not in str(failure.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.openai.com/v1/chat/completions",
        "https://example.com/v1/chat/completions",
        "https://api.openai.com/v1/chat/completions?key=hidden",
        "https://user@api.openai.com/v1/chat/completions",
        "http://127.0.0.1:1234/v1/chat/completions",
    ],
)
def test_provider_endpoint_does_not_redirect_credentials(endpoint):
    with pytest.raises(ValueError, match="unsupported_provider_endpoint"):
        OpenAITransport("synthetic-key", timeout=3, endpoint=endpoint)
