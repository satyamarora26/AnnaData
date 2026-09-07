"""
Rewriting a follow-up into a question that stands on its own.

Measured on the deployed system, the three model calls behind an answer cost
about the same as each other - roughly 3.3s to refine, 3.8s to extract, 3.5s to
compose. There is no dominant call to tune, so the only way to make the agent
meaningfully faster is to make one of them not happen.

This is the one that can sometimes be skipped, because it exists solely to
resolve a message against what came before. A question that already names its
own subject has nothing to resolve.

The skip is deliberately timid. Getting it wrong is how "yess" was once answered
with a greeting: the rewrite is what turns a bare affirmation back into the
question it is agreeing to, and losing that loses the thread. So refinement is
the default and is skipped only on positive evidence that the message stands
alone - it names a crop we recognise, and it is long enough to be a question
rather than a fragment. Anything else, including anything short, ambiguous, or
merely unfamiliar, is refined as before.
"""
import re

from langchain.prompts import ChatPromptTemplate

from knowledge import CROP_SYNONYMS
from utils import llm

# Every crop name the system knows, in the farmer's vocabulary and ours.
_CROP_WORDS = set(CROP_SYNONYMS) | set(CROP_SYNONYMS.values())

# Below this a message is a fragment, and a fragment usually leans on the turn
# before it: "kitna lagega" needs the crop from earlier, "beej kahan milega"
# needs to know which seed.
MIN_SELF_CONTAINED_WORDS = 5


def _names_a_crop(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.lower()))
    return bool(words & _CROP_WORDS)


def needs_refinement(query: str, history: list | None) -> bool:
    """Whether this message has to be resolved against the conversation.

    True whenever there is any doubt. The cost of a wrong False is a farmer
    losing the thread; the cost of a wrong True is three seconds.
    """
    if not history:
        return False                      # nothing to resolve against
    if not query:
        return True
    if len(query.split()) < MIN_SELF_CONTAINED_WORDS:
        return True                       # a fragment leans on what came before
    return not _names_a_crop(query)


def get_farming_query(query: str, history: list | None) -> str:
    """Rewrite the latest question as a standalone query using conversation history.

    With no history there is nothing to resolve, so the query is returned as-is.
    A rewrite failure also falls back to the original query rather than erroring.
    """
    if not needs_refinement(query, history):
        return query

    history_text = "\n".join(
        f"{h.get('role', 'user').capitalize()}: {h.get('content', '')}"
        for h in history
    )

    prompt = ChatPromptTemplate.from_template("""
    You are a farming assistant. A farmer is asking questions.
    Use the conversation history to understand context and rewrite the latest
    question as a complete, standalone query.

    Conversation so far:
    {history}

    Farmer's latest question:
    {query}

    Rewritten standalone query:
    """)

    try:
        response = (prompt | llm).invoke({"history": history_text, "query": query})
        return response.content.strip() or query
    except Exception as e:
        print(f"Query refinement failed, using original query: {e}")
        return query
