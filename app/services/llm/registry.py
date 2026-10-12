"""LLM model registry with dynamic multi-provider discovery and pre-initialized instances."""

from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import settings
from app.core.logging import logger
from app.services.llm.router import llm_router


# Default model catalogs per provider to populate the circular fallback chain
_PROVIDER_CATALOGS: Dict[str, List[str]] = {
    "openai": ["gpt-5.6-luna", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-4o-mini", "gpt-4o"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
    "anthropic": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"],
    "gemini": ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"],
    "mistral": ["mistral-large-latest", "mistral-small-latest", "codestral-latest"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "openrouter": ["openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet"],
    "ollama": ["llama3.2", "mistral"],
}


class LLMRegistry:
    """Registry of available LLM models with dynamic provider resolution.

    Maintains a list of active LLM configurations discovered from .env
    and provides methods to retrieve them by name or index.
    """

    LLMS: List[Dict[str, Any]] = []

    @classmethod
    def _build_registry(cls) -> None:
        """Dynamically construct the LLMS fallback catalog from active providers."""
        cls.LLMS.clear()
        available_providers = llm_router.list_available_providers()

        if not available_providers:
            logger.warning("llm_registry_no_providers_available")
            return

        default_provider = llm_router.default_provider
        # Prioritize the default provider's models first, followed by others
        ordered_providers = [default_provider] + [p for p in available_providers if p != default_provider]

        # Ensure the configured DEFAULT_LLM_MODEL is at index 0 if specified
        target_default_model = getattr(settings, "DEFAULT_LLM_MODEL", "").strip()

        seen_models = set()
        if target_default_model:
            try:
                base_llm = llm_router.get_chat_model(provider=default_provider, model=target_default_model)
                cls.LLMS.append(
                    {
                        "name": target_default_model,
                        "provider": default_provider,
                        "llm": base_llm,
                    }
                )
                seen_models.add(target_default_model)
            except Exception as e:
                logger.warning("failed_to_initialize_default_model", model=target_default_model, error=str(e))

        for provider in ordered_providers:
            catalog = _PROVIDER_CATALOGS.get(provider, [])
            for model_name in catalog:
                if model_name in seen_models:
                    continue
                try:
                    chat_model = llm_router.get_chat_model(provider=provider, model=model_name)
                    cls.LLMS.append(
                        {
                            "name": model_name,
                            "provider": provider,
                            "llm": chat_model,
                        }
                    )
                    seen_models.add(model_name)
                except Exception:
                    # Ignore models that fail pre-initialization (e.g. missing optional package)
                    continue

        # Fallback if catalog is still empty
        if not cls.LLMS and available_providers:
            first_provider = available_providers[0]
            chat_model = llm_router.get_chat_model(provider=first_provider)
            cls.LLMS.append(
                {
                    "name": "default",
                    "provider": first_provider,
                    "llm": chat_model,
                }
            )

    @classmethod
    def get(cls, model_name: str, **kwargs: Any) -> BaseChatModel:
        """Get an LLM by name with optional argument overrides.

        Supports direct model names or provider-prefixed names ('groq/llama-3.3-70b-versatile').

        Args:
            model_name: Name of the model to retrieve.
            **kwargs: Optional arguments to override default model configuration.

        Returns:
            BaseChatModel instance.
        """
        if not cls.LLMS:
            cls._build_registry()

        # Check for provider prefix (e.g. 'anthropic/claude-3-5-sonnet')
        target_provider: Optional[str] = None
        target_name = model_name
        if "/" in model_name:
            prefix, rest = model_name.split("/", 1)
            if prefix.lower() in llm_router.list_available_providers():
                target_provider = prefix.lower()
                target_name = rest

        # Check existing catalog entry
        model_entry = next((e for e in cls.LLMS if e["name"] == target_name or e["name"] == model_name), None)

        if model_entry and not kwargs and not target_provider:
            logger.debug("using_default_llm_instance", model_name=model_name)
            return model_entry["llm"]

        provider = target_provider or (model_entry["provider"] if model_entry else None)
        logger.debug(
            "resolving_llm_from_router",
            model_name=model_name,
            provider=provider or "default",
            custom_args=list(kwargs.keys()),
        )
        return llm_router.get_chat_model(
            provider=provider,
            model=target_name,
            **kwargs,
        )

    @classmethod
    def get_all_names(cls) -> List[str]:
        """Return all registered model names in order.

        Returns:
            List of model name strings.
        """
        if not cls.LLMS:
            cls._build_registry()
        return [e["name"] for e in cls.LLMS]

    @classmethod
    def get_model_at_index(cls, index: int) -> Dict[str, Any]:
        """Return the model entry at a specific index, wrapping to 0 if out of range.

        Args:
            index: Index into LLMS.

        Returns:
            Model entry dict.
        """
        if not cls.LLMS:
            cls._build_registry()
        if 0 <= index < len(cls.LLMS):
            return cls.LLMS[index]
        return cls.LLMS[0] if cls.LLMS else {"name": "default", "llm": llm_router.get_chat_model()}


# Build initial catalog on module load
LLMRegistry._build_registry()
