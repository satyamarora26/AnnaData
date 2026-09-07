import os

from google import genai
from langchain_google_genai import ChatGoogleGenerativeAI

from config import (
    GEMINI_API_KEY,
    TEXT_MODELS,
    MEDIA_MODEL,
    LLM_MAX_RETRIES,
    LLM_LAST_RESORT_RETRIES,
)

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. This is the one credential AnnaData cannot "
        "run without. Copy .env.example to .env and add a key from "
        "https://aistudio.google.com/apikey"
    )

# langchain_google_genai reads GOOGLE_API_KEY from the environment.
os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY


def _build(model: str, retries: int):
    """One model in the chain, with its retry budget bound to every call.

    The retry budget must be bound as a *call* kwarg, not passed to the
    constructor: langchain_google_genai's _chat_with_retry reads max_retries
    from the call kwargs and otherwise silently defaults to 6 attempts with
    exponential backoff. That default spends ~60s stalling on a dead or
    rate-limited model before the next one is even tried; bound this way the
    same failure hands over in well under a second.
    """
    return ChatGoogleGenerativeAI(model=model).bind(
        max_retries=retries,
        wait_exponential_max=2.0,
    )


# Fastest model first. Earlier entries fail over to later ones; the last gets a
# larger retry budget because there is nothing after it to catch a transient
# blip.
_chain = [_build(m, LLM_MAX_RETRIES) for m in TEXT_MODELS[:-1]]
_chain.append(_build(TEXT_MODELS[-1], LLM_LAST_RESORT_RETRIES))

# with_fallbacks returns a Runnable, so `llm.invoke(...)` and `prompt | llm`
# both keep working. A failure on the primary transparently retries the next
# model rather than surfacing to the farmer.
llm = _chain[0].with_fallbacks(_chain[1:]) if len(_chain) > 1 else _chain[0]

print(f"LLM chain: {' -> '.join(TEXT_MODELS)}  (media: {MEDIA_MODEL})")


def get_llm():
    """Return the global LLM instance (primary with fallbacks)."""
    return llm


client = genai.Client(api_key=GEMINI_API_KEY)


def get_client():
    """Returns the GenAI client instance."""
    return client
