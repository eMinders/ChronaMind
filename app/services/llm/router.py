"""Dynamic multi-provider LLM router and unified gateway adapter.

Discovers available LLM providers from environment variables at runtime,
manages concrete adapters, and routes requests with automatic fallback.
Supports both direct completion dict generation and LangChain BaseChatModel instances.
"""

import abc
import os
from typing import (
    Any,
    Dict,
    List,
    Optional,
)
from typing_extensions import override

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import logger

# Optional provider imports (graceful degradation if SDKs are not installed)
try:
    from langchain_anthropic import ChatAnthropic  # pyright: ignore[reportMissingImports]

    _HAS_LANGCHAIN_ANTHROPIC = True
except ImportError:
    ChatAnthropic = None  # type: ignore[assignment,misc]
    _HAS_LANGCHAIN_ANTHROPIC = False

try:
    from anthropic import Anthropic  # pyright: ignore[reportMissingImports]

    _HAS_DIRECT_ANTHROPIC = True
except ImportError:
    Anthropic = None  # type: ignore[assignment,misc]
    _HAS_DIRECT_ANTHROPIC = False

try:
    from langchain_google_genai import ChatGoogleGenerativeAI  # pyright: ignore[reportMissingImports]

    _HAS_LANGCHAIN_GOOGLE = True
except ImportError:
    ChatGoogleGenerativeAI = None  # type: ignore[assignment,misc]
    _HAS_LANGCHAIN_GOOGLE = False

try:
    import google.generativeai as genai  # pyright: ignore[reportMissingImports]

    _HAS_DIRECT_GOOGLE = True
except ImportError:
    genai = None  # type: ignore[assignment]
    _HAS_DIRECT_GOOGLE = False


# ======================================================================
# 1. Base Adapter Interface
# ======================================================================


class BaseLLMAdapter(abc.ABC):
    """Standard interface that all downstream provider adapters must map to."""

    provider_name: str

    @abc.abstractmethod
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate a chat completion response dictionary.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            options: Optional invocation parameters (model, temperature, etc.).

        Returns:
            Dict containing 'content', 'provider', 'model', and 'usage'.
        """
        pass

    @abc.abstractmethod
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Return a LangChain-compatible BaseChatModel instance.

        Args:
            model: Specific model name override.
            temperature: Sampling temperature override.
            max_tokens: Max output tokens override.
            **kwargs: Extra provider-specific parameters.

        Returns:
            BaseChatModel instance ready for LangGraph and tool binding.
        """
        pass


def _convert_dict_messages_to_langchain(messages: List[Dict[str, str]]) -> List[BaseMessage]:
    """Convert dict-format messages to LangChain BaseMessage instances."""
    lc_messages: List[BaseMessage] = []
    for m in messages:
        role = m.get("role", "user").lower()
        content = m.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=content))
    return lc_messages


# ======================================================================
# 2. Concrete Provider Adapters
# ======================================================================


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter for official OpenAI endpoints."""

    provider_name = "openai"

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        """Initialize the OpenAI adapter.

        Args:
            api_key: The OpenAI API key.
            base_url: Optional custom API base URL.
        """
        self.api_key = api_key
        self.base_url = base_url or None

    @override
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        target_model = model or settings.DEFAULT_LLM_MODEL or "gpt-4o-mini"
        target_temp = temperature if temperature is not None else settings.DEFAULT_LLM_TEMPERATURE
        target_tokens = max_tokens or settings.MAX_TOKENS

        model_kwargs: Dict[str, Any] = {
            "model": target_model,
            "api_key": SecretStr(self.api_key),
            "temperature": target_temp,
            "max_completion_tokens": target_tokens,
        }
        if self.base_url:
            model_kwargs["base_url"] = self.base_url
        model_kwargs.update(kwargs)

        return ChatOpenAI(**model_kwargs)

    @override
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        model = options.get("model", "gpt-4o-mini")
        chat_model = self.get_chat_model(model=model, **{k: v for k, v in options.items() if k != "model"})
        lc_messages = _convert_dict_messages_to_langchain(messages)
        response = chat_model.invoke(lc_messages)

        usage = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata

        return {
            "content": str(response.content),
            "provider": self.provider_name,
            "model": model,
            "usage": usage,
        }


class GroqAdapter(BaseLLMAdapter):
    """Adapter for Groq cloud (using OpenAI wire-compatible endpoint)."""

    provider_name = "groq"
    DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, api_key: str):
        """Initialize the Groq adapter.

        Args:
            api_key: The Groq API key.
        """
        self.api_key = api_key

    @override
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        target_model = model or self.DEFAULT_MODEL
        target_temp = temperature if temperature is not None else settings.DEFAULT_LLM_TEMPERATURE
        target_tokens = max_tokens or settings.MAX_TOKENS

        return ChatOpenAI(
            model=target_model,
            api_key=SecretStr(self.api_key),
            base_url=self.DEFAULT_BASE_URL,
            temperature=target_temp,
            max_completion_tokens=target_tokens,
            **kwargs,
        )

    @override
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        model = options.get("model", self.DEFAULT_MODEL)
        chat_model = self.get_chat_model(model=model, **{k: v for k, v in options.items() if k != "model"})
        lc_messages = _convert_dict_messages_to_langchain(messages)
        response = chat_model.invoke(lc_messages)

        usage = getattr(response, "usage_metadata", None)
        return {
            "content": str(response.content),
            "provider": self.provider_name,
            "model": model,
            "usage": usage,
        }


class MistralAdapter(BaseLLMAdapter):
    """Adapter for Mistral AI (using OpenAI wire-compatible endpoint)."""

    provider_name = "mistral"
    DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
    DEFAULT_MODEL = "mistral-large-latest"

    def __init__(self, api_key: str):
        """Initialize the Mistral adapter.

        Args:
            api_key: The Mistral API key.
        """
        self.api_key = api_key

    @override
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        target_model = model or self.DEFAULT_MODEL
        target_temp = temperature if temperature is not None else settings.DEFAULT_LLM_TEMPERATURE
        target_tokens = max_tokens or settings.MAX_TOKENS

        return ChatOpenAI(
            model=target_model,
            api_key=SecretStr(self.api_key),
            base_url=self.DEFAULT_BASE_URL,
            temperature=target_temp,
            max_completion_tokens=target_tokens,
            **kwargs,
        )

    @override
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        model = options.get("model", self.DEFAULT_MODEL)
        chat_model = self.get_chat_model(model=model, **{k: v for k, v in options.items() if k != "model"})
        lc_messages = _convert_dict_messages_to_langchain(messages)
        response = chat_model.invoke(lc_messages)

        usage = getattr(response, "usage_metadata", None)
        return {
            "content": str(response.content),
            "provider": self.provider_name,
            "model": model,
            "usage": usage,
        }


class DeepSeekAdapter(BaseLLMAdapter):
    """Adapter for DeepSeek (using OpenAI wire-compatible endpoint)."""

    provider_name = "deepseek"
    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(self, api_key: str):
        """Initialize the DeepSeek adapter.

        Args:
            api_key: The DeepSeek API key.
        """
        self.api_key = api_key

    @override
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        target_model = model or self.DEFAULT_MODEL
        target_temp = temperature if temperature is not None else settings.DEFAULT_LLM_TEMPERATURE
        target_tokens = max_tokens or settings.MAX_TOKENS

        return ChatOpenAI(
            model=target_model,
            api_key=SecretStr(self.api_key),
            base_url=self.DEFAULT_BASE_URL,
            temperature=target_temp,
            max_completion_tokens=target_tokens,
            **kwargs,
        )

    @override
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        model = options.get("model", self.DEFAULT_MODEL)
        chat_model = self.get_chat_model(model=model, **{k: v for k, v in options.items() if k != "model"})
        lc_messages = _convert_dict_messages_to_langchain(messages)
        response = chat_model.invoke(lc_messages)

        usage = getattr(response, "usage_metadata", None)
        return {
            "content": str(response.content),
            "provider": self.provider_name,
            "model": model,
            "usage": usage,
        }


class OpenRouterAdapter(BaseLLMAdapter):
    """Adapter for OpenRouter aggregator."""

    provider_name = "openrouter"
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "openai/gpt-4o-mini"

    def __init__(self, api_key: str):
        """Initialize the OpenRouter adapter.

        Args:
            api_key: The OpenRouter API key.
        """
        self.api_key = api_key

    @override
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        target_model = model or self.DEFAULT_MODEL
        target_temp = temperature if temperature is not None else settings.DEFAULT_LLM_TEMPERATURE
        target_tokens = max_tokens or settings.MAX_TOKENS

        return ChatOpenAI(
            model=target_model,
            api_key=SecretStr(self.api_key),
            base_url=self.DEFAULT_BASE_URL,
            temperature=target_temp,
            max_completion_tokens=target_tokens,
            **kwargs,
        )

    @override
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        model = options.get("model", self.DEFAULT_MODEL)
        chat_model = self.get_chat_model(model=model, **{k: v for k, v in options.items() if k != "model"})
        lc_messages = _convert_dict_messages_to_langchain(messages)
        response = chat_model.invoke(lc_messages)

        usage = getattr(response, "usage_metadata", None)
        return {
            "content": str(response.content),
            "provider": self.provider_name,
            "model": model,
            "usage": usage,
        }


class OllamaAdapter(BaseLLMAdapter):
    """Adapter for local Ollama instances (OpenAI compatibility endpoint)."""

    provider_name = "ollama"
    DEFAULT_MODEL = "llama3.2"

    def __init__(self, base_url: str):
        """Initialize the Ollama adapter.

        Args:
            base_url: The Ollama server base URL.
        """
        self.base_url = f"{base_url.rstrip('/')}/v1"

    @override
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        target_model = model or self.DEFAULT_MODEL
        target_temp = temperature if temperature is not None else settings.DEFAULT_LLM_TEMPERATURE
        target_tokens = max_tokens or settings.MAX_TOKENS

        return ChatOpenAI(
            model=target_model,
            api_key=SecretStr("ollama"),
            base_url=self.base_url,
            temperature=target_temp,
            max_completion_tokens=target_tokens,
            **kwargs,
        )

    @override
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        model = options.get("model", self.DEFAULT_MODEL)
        chat_model = self.get_chat_model(model=model, **{k: v for k, v in options.items() if k != "model"})
        lc_messages = _convert_dict_messages_to_langchain(messages)
        response = chat_model.invoke(lc_messages)

        return {
            "content": str(response.content),
            "provider": self.provider_name,
            "model": model,
            "usage": getattr(response, "usage_metadata", None),
        }


class AnthropicAdapter(BaseLLMAdapter):
    """Adapter for Anthropic Claude."""

    provider_name = "anthropic"
    DEFAULT_MODEL = "claude-3-5-sonnet-latest"

    def __init__(self, api_key: str):
        """Initialize the Anthropic adapter.

        Args:
            api_key: The Anthropic API key.
        """
        self.api_key = api_key

    @override
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        if not _HAS_LANGCHAIN_ANTHROPIC:
            raise ImportError(
                "langchain-anthropic is not installed. Run 'uv add langchain-anthropic' to enable Anthropic support."
            )

        target_model = model or self.DEFAULT_MODEL
        target_temp = temperature if temperature is not None else settings.DEFAULT_LLM_TEMPERATURE
        target_tokens = max_tokens or settings.MAX_TOKENS

        return ChatAnthropic(  # type: ignore[misc]
            model=target_model,
            api_key=SecretStr(self.api_key),
            temperature=target_temp,
            max_tokens_to_sample=target_tokens,
            **kwargs,
        )

    @override
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        model = options.get("model", self.DEFAULT_MODEL)

        if _HAS_LANGCHAIN_ANTHROPIC:
            chat_model = self.get_chat_model(model=model, **{k: v for k, v in options.items() if k != "model"})
            lc_messages = _convert_dict_messages_to_langchain(messages)
            response = chat_model.invoke(lc_messages)
            return {
                "content": str(response.content),
                "provider": self.provider_name,
                "model": model,
                "usage": getattr(response, "usage_metadata", None),
            }

        if not _HAS_DIRECT_ANTHROPIC:
            raise ImportError(
                "Neither langchain-anthropic nor anthropic SDK is installed. "
                "Install via 'uv add langchain-anthropic' or 'uv add anthropic'."
            )

        client = Anthropic(api_key=self.api_key)  # type: ignore[misc]
        system_prompt = next((m["content"] for m in messages if m.get("role") == "system"), "")
        filtered = [m for m in messages if m.get("role") != "system"]

        res = client.messages.create(
            model=model,
            max_tokens=options.get("max_tokens", settings.MAX_TOKENS),
            system=system_prompt,
            messages=filtered,
            **{k: v for k, v in options.items() if k not in ["model", "max_tokens"]},
        )
        content_text = res.content[0].text if res.content else ""
        return {
            "content": content_text,
            "provider": self.provider_name,
            "model": model,
            "usage": res.usage.model_dump() if hasattr(res, "usage") else None,
        }


class GeminiAdapter(BaseLLMAdapter):
    """Adapter for Google Gemini."""

    provider_name = "gemini"
    DEFAULT_MODEL = "gemini-1.5-flash"

    def __init__(self, api_key: str):
        """Initialize the Gemini adapter.

        Args:
            api_key: The Google Gemini API key.
        """
        self.api_key = api_key

    @override
    def get_chat_model(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        if not _HAS_LANGCHAIN_GOOGLE:
            raise ImportError(
                "langchain-google-genai is not installed. Run 'uv add langchain-google-genai' to enable Google support."
            )

        target_model = model or self.DEFAULT_MODEL
        target_temp = temperature if temperature is not None else settings.DEFAULT_LLM_TEMPERATURE
        target_tokens = max_tokens or settings.MAX_TOKENS

        return ChatGoogleGenerativeAI(  # type: ignore[misc]
            model=target_model,
            google_api_key=self.api_key,
            temperature=target_temp,
            max_output_tokens=target_tokens,
            **kwargs,
        )

    @override
    def generate(
        self,
        messages: List[Dict[str, str]],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        model_name = options.get("model", self.DEFAULT_MODEL)

        if _HAS_LANGCHAIN_GOOGLE:
            chat_model = self.get_chat_model(model=model_name, **{k: v for k, v in options.items() if k != "model"})
            lc_messages = _convert_dict_messages_to_langchain(messages)
            response = chat_model.invoke(lc_messages)
            return {
                "content": str(response.content),
                "provider": self.provider_name,
                "model": model_name,
                "usage": getattr(response, "usage_metadata", None),
            }

        if not _HAS_DIRECT_GOOGLE:
            raise ImportError(
                "Neither langchain-google-genai nor google-generativeai is installed. "
                "Install via 'uv add langchain-google-genai'."
            )

        genai.configure(api_key=self.api_key)  # type: ignore[misc]
        gemini_history = []
        for msg in messages:
            role = "user" if msg.get("role") in ["user", "system"] else "model"
            gemini_history.append({"role": role, "parts": [msg.get("content", "")]})

        gen_model = genai.GenerativeModel(model_name)  # type: ignore[misc]
        chat = gen_model.start_chat(history=gemini_history[:-1])
        res = chat.send_message(gemini_history[-1]["parts"][0])
        return {
            "content": res.text,
            "provider": self.provider_name,
            "model": model_name,
            "usage": None,
        }


# ======================================================================
# 3. Dynamic LLM Router (Singleton & Runtime Discovery)
# ======================================================================


def _is_active_key(key: Optional[str]) -> bool:
    """Check if an API key is provided and is not an unpopulated placeholder."""
    if not key or not key.strip():
        return False
    clean = key.strip().lower()
    placeholder_indicators = (
        "placeholder",
        "replace-with",
        "your-",
        "sk-placeholder",
        "changeme",
        "todo",
    )
    return not any(indicator in clean for indicator in placeholder_indicators)


class DynamicLLMRouter:
    """Scans .env variables at runtime, mounts adapters, and dispatches requests.

    Provides automatic failover when a requested provider is missing credentials.
    """

    def __init__(self):
        """Initialize the dynamic LLM router and mount active providers."""
        self._adapters: Dict[str, BaseLLMAdapter] = {}
        self.default_provider: str = getattr(settings, "DEFAULT_PROVIDER", "openai").lower()
        self._discover_and_initialize_providers()

    def _discover_and_initialize_providers(self) -> None:
        """Scan settings and environment matrix to register available providers."""
        # 1. OpenAI (or OpenAI-compatible proxies such as Atlas Cloud)
        openai_key = getattr(settings, "OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        base_url = getattr(settings, "OPENAI_BASE_URL", "") or os.getenv("OPENAI_BASE_URL", "")
        if _is_active_key(openai_key):
            self._adapters["openai"] = OpenAIAdapter(api_key=openai_key.strip(), base_url=base_url)
            logger.info("llm_router_provider_mounted", provider="openai", custom_base_url=bool(base_url))
        elif openai_key:
            logger.debug("llm_router_skipping_placeholder", provider="openai")

        # 2. Groq
        groq_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
        if _is_active_key(groq_key):
            self._adapters["groq"] = GroqAdapter(api_key=groq_key.strip())
            logger.info("llm_router_provider_mounted", provider="groq")
        elif groq_key:
            logger.debug("llm_router_skipping_placeholder", provider="groq")

        # 3. Mistral
        mistral_key = getattr(settings, "MISTRAL_API_KEY", "") or os.getenv("MISTRAL_API_KEY", "")
        if _is_active_key(mistral_key):
            self._adapters["mistral"] = MistralAdapter(api_key=mistral_key.strip())
            logger.info("llm_router_provider_mounted", provider="mistral")
        elif mistral_key:
            logger.debug("llm_router_skipping_placeholder", provider="mistral")

        # 4. DeepSeek
        deepseek_key = getattr(settings, "DEEPSEEK_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
        if _is_active_key(deepseek_key):
            self._adapters["deepseek"] = DeepSeekAdapter(api_key=deepseek_key.strip())
            logger.info("llm_router_provider_mounted", provider="deepseek")
        elif deepseek_key:
            logger.debug("llm_router_skipping_placeholder", provider="deepseek")

        # 5. OpenRouter
        openrouter_key = getattr(settings, "OPENROUTER_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
        if _is_active_key(openrouter_key):
            self._adapters["openrouter"] = OpenRouterAdapter(api_key=openrouter_key.strip())
            logger.info("llm_router_provider_mounted", provider="openrouter")
        elif openrouter_key:
            logger.debug("llm_router_skipping_placeholder", provider="openrouter")

        # 6. Ollama (local server)
        ollama_url = getattr(settings, "OLLAMA_BASE_URL", "") or os.getenv("OLLAMA_BASE_URL", "")
        if ollama_url and ollama_url.strip() and not any(p in ollama_url.lower() for p in ("placeholder", "todo")):
            self._adapters["ollama"] = OllamaAdapter(base_url=ollama_url.strip())
            logger.info("llm_router_provider_mounted", provider="ollama", base_url=ollama_url)

        # 7. Anthropic
        anthropic_key = getattr(settings, "ANTHROPIC_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
        if _is_active_key(anthropic_key):
            self._adapters["anthropic"] = AnthropicAdapter(api_key=anthropic_key.strip())
            logger.info("llm_router_provider_mounted", provider="anthropic")
        elif anthropic_key:
            logger.debug("llm_router_skipping_placeholder", provider="anthropic")

        # 8. Google / Gemini
        gemini_key = (
            getattr(settings, "GEMINI_API_KEY", "")
            or os.getenv("GEMINI_API_KEY", "")
            or os.getenv("GOOGLE_API_KEY", "")
        )
        if _is_active_key(gemini_key):
            self._adapters["gemini"] = GeminiAdapter(api_key=gemini_key.strip())
            logger.info("llm_router_provider_mounted", provider="gemini")
        elif gemini_key:
            logger.debug("llm_router_skipping_placeholder", provider="gemini")

        # Graceful fallback if only placeholders exist in .env
        if not self._adapters:
            logger.warning(
                "no_active_llm_providers_detected",
                hint="Replace placeholder API keys in .env with your real credentials",
            )
            raw_key = (
                getattr(settings, "OPENAI_API_KEY", "")
                or os.getenv("OPENAI_API_KEY", "")
                or "sk-placeholder-unconfigured"
            )
            base_url = getattr(settings, "OPENAI_BASE_URL", "") or os.getenv("OPENAI_BASE_URL", "")
            self._adapters["openai"] = OpenAIAdapter(api_key=raw_key.strip(), base_url=base_url)

        # Fallback check for default provider
        if self._adapters and self.default_provider not in self._adapters:
            first_available = list(self._adapters.keys())[0]
            logger.warning(
                "default_provider_not_configured",
                configured_default=self.default_provider,
                fallback_to=first_available,
            )
            self.default_provider = first_available

    def list_available_providers(self) -> List[str]:
        """Return list of all registered provider names."""
        return list(self._adapters.keys())

    def is_provider_available(self, provider: str) -> bool:
        """Check if a specific provider is configured and available."""
        return provider.lower() in self._adapters

    def get_adapter(self, provider: Optional[str] = None) -> BaseLLMAdapter:
        """Retrieve the adapter for a given provider, or fall back to default."""
        target = (provider or self.default_provider).lower()
        if target not in self._adapters:
            if not self._adapters:
                raise RuntimeError(
                    "No LLM provider keys configured in .env. Please set at least one "
                    "provider key (e.g. OPENAI_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY)."
                )
            fallback = (
                self.default_provider if self.default_provider in self._adapters else list(self._adapters.keys())[0]
            )
            logger.warning("llm_router_provider_fallback", requested=target, using_fallback=fallback)
            return self._adapters[fallback]
        return self._adapters[target]

    def get_chat_model(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Obtain a LangChain BaseChatModel from the requested or default provider."""
        adapter = self.get_adapter(provider)
        return adapter.get_chat_model(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def route_request(
        self,
        messages: List[Dict[str, str]],
        provider: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dynamically dispatch completion request to the active provider adapter."""
        adapter = self.get_adapter(provider)
        return adapter.generate(messages=messages, options=options)


# Singleton instance exported for use across the application
llm_router = DynamicLLMRouter()
