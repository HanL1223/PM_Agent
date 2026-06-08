"""
A tiny factory that returns a configured LangChain chat model.

The template hard-codes `ChatOpenAI`. We add one indirection so the same code
works with Claude or OpenAI by changing a single env var. That's
the *only* reason this module exists — resist the urge to add more here.

Usage:
    from pmagent.llm import get_llm
    llm = get_llm(temperature=0.1)
    llm_with_tools = get_llm().bind_tools([...])
"""

from langchain_core.language_models.chat_models import BaseChatModel
from pmagent import env

def get_llm(model:str | None = None, temperature:float = 0.1 ) -> BaseChatModel:
    """
    Return a chat model for the configured provider.

    Args:
        model: Override the model name. Defaults to `env.LLM_MODEL`.
        temperature: Sampling temperature. Low (0.1) for predictable, structured
            behaviour — appropriate for routing and ticket drafting.

    Returns:
        A LangChain `BaseChatModel`. Both providers expose the same interface
        (`.invoke`, `.bind_tools`, `.with_structured_output`)
    """

    model = model or env.LLM_MODEL

    if env.LLM_PROVIDER == "anthropic":
        # Imported lazily so you don't need the OpenAI package installed (or vice
        # versa) just to use the other provider.
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=temperature)

    if env.LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=temperature)

    raise ValueError(
        f"Unknown LLM_PROVIDER={env.LLM_PROVIDER!r}. Use 'anthropic' or 'openai'."
    )