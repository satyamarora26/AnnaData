from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import List, Optional

from langchain.schema import HumanMessage

from utils import llm
from Address_Convertor import get_location
from Mandi_Price_Tool import get_state_data
from Query_Parser import extract_farm_info
from weather_tool import weather_openmeteo
from Soil_Tool import soil_tool
from Refined_Farmer_Query import get_farming_query
from Web_Crawler import query_kb, is_available as kb_available
import knowledge
import msp
import planner
import output_guards
import provenance
import re


# Where the year sits in the Indian cropping calendar. Advice is intensely
# seasonal and the model has no clock, so without this it recommends sowing
# windows that closed months ago - asked in late August about the next two
# months it will happily suggest mid-June.
CROP_CALENDAR = {
    1:  ("Rabi", "Rabi crops are growing. Sowing is over; irrigate and manage pests."),
    2:  ("Rabi", "Rabi crops are maturing. Sowing is over."),
    3:  ("Rabi harvest / Zaid sowing", "Rabi harvest begins. Zaid (summer) sowing starts."),
    4:  ("Zaid", "Zaid summer crops are sown and growing. Rabi harvest completes."),
    5:  ("Zaid", "Zaid crops growing under irrigation. Prepare fields for Kharif."),
    6:  ("Kharif sowing", "Monsoon arrives. Kharif sowing window is open now."),
    7:  ("Kharif sowing", "Kharif sowing and transplanting continue. Window closing."),
    8:  ("Kharif growing", "Kharif sowing is OVER. Crops are standing; focus on pest, nutrient and water management."),
    9:  ("Kharif growing", "Kharif crops are maturing. Sowing is OVER. Early harvest begins late in the month."),
    10: ("Kharif harvest / Rabi sowing", "Kharif harvest is underway. Rabi sowing window opens."),
    11: ("Rabi sowing", "Rabi sowing is the priority now - wheat, mustard, gram."),
    12: ("Rabi sowing", "Late Rabi sowing. Sow now or yields drop."),
}


def temporal_context() -> str:
    """Today's date and where it falls in the cropping year."""
    today = date.today()
    season, note = CROP_CALENDAR[today.month]
    return (
        f"TODAY'S DATE: {today:%d %B %Y}.\n"
        f"    Current season in India: {season}. {note}\n"
        f"    Indian cropping calendar: Kharif sown Jun-Jul and harvested Sep-Oct; "
        f"Rabi sown Oct-Dec and harvested Mar-Apr; Zaid sown Mar-Apr and harvested Jun.\n"
        f"    NEVER recommend a sowing window that has already passed this year. "
        f"If the farmer asks about a window that has closed, say so plainly and "
        f"give them the next one that is actually open."
    )


# How the answer should be shaped, per delivery channel. SMS callers used to
# append this instruction to the farmer's message, which meant the query parser
# saw it as part of the question and could mis-extract crop and location.
CHANNEL_STYLE = {
    "web": (
        "Keep the answer concise, ideally under 250 words. "
        "Format it clearly using markdown."
    ),
    # WhatsApp has no segment budget, so the SMS economy does not apply - but a
    # farmer reading on a phone still wants the answer, not an essay. Light
    # formatting is supported and markdown headers are not.
    "whatsapp": (
        "This answer is delivered on WhatsApp. "
        "Use plain text with short paragraphs. You may use *single asterisks* "
        "for emphasis on a key figure, but no headers, no bullet characters, "
        "no tables, no links and no markdown. "
        "Give the most useful, actionable steps in under 90 words, always with "
        "specific quantities. Finish every sentence you begin."
    ),
    "sms": (
        "This answer is delivered as an SMS to a basic phone. "
        "Use plain text ONLY: no markdown, no asterisks, no hash headers, "
        "no bullet characters, no tables, no links. "
        "Write two or three short sentences giving the single most useful, "
        "actionable step, and always include specific quantities. "
        "Always finish your final sentence - a complete short answer is much "
        "more useful to a farmer than a longer one that gets cut off."
    ),
}


# Which script a message is written in, decided in code rather than left to the
# model. Told to "match the farmer's language" it kept converting romanised
# Hinglish into Devanagari; told "reply in the Latin alphabet" it complies. The
# shared danda U+0964 sits in the Devanagari block but is used by Bengali and
# Gurmukhi too, so it must not decide anything.
SCRIPT_RANGES = (
    ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Bengali", 0x0980, 0x09FF),
    ("Gujarati", 0x0A80, 0x0AFF),
    ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F),
    ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F),
    ("Odia", 0x0B00, 0x0B7F),
    ("Devanagari", 0x0900, 0x0963),
)


def script_of(text: str) -> str:
    for name, lo, hi in SCRIPT_RANGES:
        if any(lo <= ord(c) <= hi for c in text or ""):
            return name
    return "Latin"


def script_instruction(query: str) -> str:
    script = script_of(query)
    if script == "Latin":
        return ("The farmer wrote in the LATIN alphabet. You MUST reply in the "
                "LATIN alphabet. If they wrote romanised Hindi or another Indian "
                "language (Hinglish, 'Kapas me sundi lag gayi hai'), reply the same "
                "way, in Latin letters. Do NOT reply in Devanagari or any other "
                "Indian script.")
    return (f"The farmer wrote in the {script} script. You MUST reply in the "
            f"{script} script, in that same language. Do not switch to Hindi or "
            f"English unless they did.")


# What one SMS segment holds. A message outside the GSM-7 alphabet is sent as
# UCS-2, where a segment carries 67 characters instead of 153 - so the same
# advice in Devanagari costs well over twice as much to deliver.
SEGMENT_CHARS = {"latin": 153, "other": 67}
SMS_SEGMENT_BUDGET = 3


def sms_char_budget(query: str) -> int:
    """How many characters an SMS answer may use, for this farmer's script.

    Asking the model to "keep it under 30 words" did not work: measured on real
    answers it produced 45 and 36, because counting its own output is not
    something it does reliably. A character ceiling computed here from the script
    and the segment budget is a number it can be given rather than asked to
    derive - the same reason the script itself is decided in code.
    """
    per_segment = SEGMENT_CHARS["latin" if script_of(query) == "Latin" else "other"]
    return per_segment * SMS_SEGMENT_BUDGET


# A message this short is a reply, not a new subject. Long enough to be a
# question in its own right and it no longer needs the turn before it.
SHORT_REPLY_WORDS = 6


def answers_our_question(query: str, history: Optional[List[dict]]) -> bool:
    """Whether this short message is answering something we just asked.

    Every reply the agent gives ends with a question where one would help, so a
    farmer's next message is usually an answer to it. "yess" was fixed by
    noticing that smalltalk carrying a topic is a contradiction, but that missed
    the negative case: asked whether he kept animals whose dung he could use, a
    farmer answered "nahin" and was greeted from scratch, twice in one
    conversation. A refusal is an answer, and it carries no topic of its own to
    give it away.

    So the signal is not the message, it is what preceded it: if our last turn
    asked something and this reply is short, it belongs to that question.
    """
    if not history or not query:
        return False
    if len(query.split()) > SHORT_REPLY_WORDS:
        return False
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            return "?" in (turn.get("content") or "")
    return False


def style_for(channel: str, query: str = "") -> str:
    channel = (channel or "web").lower()
    style = CHANNEL_STYLE.get(channel, CHANNEL_STYLE["web"])
    if channel == "sms" and query:
        budget = sms_char_budget(query)
        style += (f" Your entire reply must be at most {budget} characters. "
                  f"This is a hard limit: past it the message is cut off "
                  f"mid-sentence and the farmer loses the end of the advice.")
    return style


def extract_markdown_content(text: str) -> str:
    """Unwrap a ```markdown fenced block if the model emitted one."""
    match = re.search(r"```markdown\n([\s\S]*?)\n```", text)
    return match.group(1).strip() if match else text


def get_open_ended_answer(query: str, history: Optional[List[dict]], channel: str = "web") -> str:
    """
    query: str - the farmer's latest question
    history: list[dict] - [{"role": "user"/"assistant", "content": "..."}]
    """
    print("QUERY:", query)
    print("HISTORY:", history)

    history_text = "\n".join(
        f"{h.get('role', 'user').capitalize()}: {h.get('content', '')}"
        for h in (history or [])
    )

    style = style_for(channel, query)
    temporal = temporal_context()
    script_rule = script_instruction(query)

    prompt = f"""
    **Role and Goal:**
    You are a highly knowledgeable agronomist and expert agricultural advisor, specializing in Indian farming conditions. Your goal is to provide a detailed, scientifically valid, and practical answer to the farmer's question, using the provided conversation history for context.

    **Current date and season:**
    {temporal}

    **SCRIPT (this overrides every other instruction):**
    {script_rule}

    **Critical Instructions:**
    1.  **Content:** The advice must be accurate, practical for Indian conditions, and directly address the farmer's latest query.
    2.  **Format:** {style}
    3.  **Language (overrides everything above):** Identify the exact language and script of the farmer's latest question, and reply in that same one. Punjabi in Gurmukhi is answered in Gurmukhi, Bengali in Bengali, Telugu in Telugu, Marathi in Devanagari, English in English, Hinglish in Hinglish. Hindi is NOT a default: never answer an Indian-language question in Hindi unless the farmer wrote in Hindi. The location the farmer mentions NEVER decides the language: a question written in English about Nagpur is answered in English, not Marathi; about Ludhiana, in English, not Punjabi. If the farmer wrote Hindi or another Indian language in the LATIN alphabet (Hinglish - 'Kapas me sundi lag gayi hai'), reply in the Latin alphabet too. Do not convert their romanised text into Devanagari; they chose to write in Latin script and the reply must match. Match the question, not the place. If the farmer wrote Hindi or another Indian language in the LATIN alphabet (Hinglish - 'Kapas me sundi lag gayi hai'), reply in the Latin alphabet too. Do not convert their romanised text into Devanagari; they chose to write in Latin script and the reply must match. Never translate.

    ---

    **Conversation Context:**

    **Conversation so far:**
    {history_text}

    **Farmer's latest question:**
    {query}

    ---

    **Assistant's Expert Agronomic Advice:**
    """

    return llm.invoke([HumanMessage(content=prompt)]).content.strip()


def answer_about_system(query: str, history, channel: str, profile=None) -> str:
    """Answer a question about the assistant itself, from what it actually does.

    The facts are assembled from configuration, not recalled by the model, so a
    farmer asking where a soil figure came from is told the truth about this
    service rather than a textbook account of how soil is tested in general.
    """
    style = style_for(channel, query)
    history_text = "\n".join(
        f"{h.get('role', 'user').capitalize()}: {h.get('content', '')}"
        for h in (history or [])
    )

    prompt = f"""
    A farmer has asked about the assistant itself - what it can help with, how
    it knows something, or where its information comes from.

    Answer using ONLY the facts below, and answer the question they actually
    asked:
    - Asked what you can DO, name the things they can ask about, in plain
      farming terms. Do not list the names of data providers - a farmer wants
      to know you can help with pests and fertiliser, not that a service called
      OpenLandMap exists.
    - Asked where a particular figure came FROM, name that source honestly and
      do not oversell its accuracy.
    - Never say you cannot do something that appears under WHAT IT CAN HELP
      WITH. If it is listed there, you can do it.
    - If the facts genuinely do not cover what they asked, say so simply.

    FACTS:
    {provenance.describe(profile)}

    Conversation so far:
    {history_text}

    Farmer's question: {query}

    Constraints:
    - {style}
    - {script_instruction(query)}
    - Be warm and plain-spoken. This is a farmer asking what help is available,
      not an auditor. Offer, do not merely enumerate, and never end on a bare
      refusal where you have something useful to offer instead.
    """

    return llm.invoke([HumanMessage(content=prompt)]).content.strip()


def handle_correction(query: str, history, channel: str) -> str:
    """Reply to a farmer objecting to the previous answer.

    Apologising and restating the same advice is what the assistant did before,
    which is worse than useless: the farmer already said that was not what they
    asked. The useful move is to find the question they actually put and answer
    that one.
    """
    style = style_for(channel, query)
    history_text = "\n".join(
        f"{h.get('role', 'user').capitalize()}: {h.get('content', '')}"
        for h in (history or [])
    )

    prompt = f"""
    A farmer is telling you the previous reply was wrong or off-topic.

    Conversation so far:
    {history_text}

    What the farmer just said: {query}

    Work out what they ACTUALLY asked for, and answer that. If they were given
    advice they did not ask for, do not repeat it and do not defend it. If it is
    genuinely unclear what they want, ask one short question.

    Do not write a long apology, and never invent a reason for the earlier
    answer. At most a brief acknowledgement, then the useful reply.

    {temporal_context()}

    Constraints:
    - {style}
    - Reply in the SAME language and SAME script the farmer used.
    """

    return llm.invoke([HumanMessage(content=prompt)]).content.strip()


def greet(query: str, profile, channel: str) -> str:
    """Answer a greeting by saying what this service can actually do.

    A greeting previously fell through to the advice path and produced a
    lecture on wheat sowing, which answers a question nobody asked. What a
    farmer needs on first contact is to know what is worth asking.
    """
    style = style_for(channel, query)
    known = []
    if profile:
        if profile.get("location_text"):
            known.append(f"they farm near {profile['location_text']}")
        if profile.get("crops"):
            known.append(f"they grow {', '.join(profile['crops'])}")

    returning = bool(known)
    prompt = f"""
    A farmer has sent a greeting.

    {"This is someone you already know: " + " and ".join(known) + ". Greet them as you would someone you have spoken to before - briefly and by name of place if you know it. Do NOT recite your full list of capabilities to a farmer who has already used the service; they know. Ask what they need today." if returning else
     "You have not spoken to this farmer before. Introduce yourself in one sentence: you are AnnaData, an agricultural advisor they can text any time, free. Then say what is worth asking about - pests and diseases, fertiliser, irrigation, sowing times, weather, and support prices - and invite them to say where they farm and what they grow."}

    Be warm and human. No lists, no bullet points, no formal announcements.
    Do NOT give farming advice they did not ask for. Invite their question.

    {script_instruction(query)}

    Constraints:
    - {style}
    """

    return llm.invoke([HumanMessage(content=prompt)]).content.strip()


def acknowledge_statement(query: str, facts: dict, channel: str,
                          script_query: str = "") -> str:
    """Reply to a farmer who told us something rather than asking anything.

    Confirming what was understood is useful; volunteering a page of unrelated
    advice is not, and is what prompted 'I didnt ask about sowing?'.
    """
    style = style_for(channel, query)
    known = ", ".join(f"{k}: {v}" for k, v in facts.items() if v) or "nothing specific"

    prompt = f"""
    A farmer has told you something about their farm. They did NOT ask a
    question. Do not give advice they did not ask for.

    What they said: {query}
    What you understood: {known}

    Reply by confirming briefly what you noted, then invite their question by
    naming one or two things you could help with for that crop - for example
    pests, fertiliser, irrigation or when to sow. Do NOT actually give that
    advice; just offer it.

    Two short sentences.

    {script_instruction(script_query or query)}

    Constraints:
    - {style}
    """

    return llm.invoke([HumanMessage(content=prompt)]).content.strip()


def get_farming_advice(location, state, crop, gathered, farmer_query,
                       channel: str = "web", history=None) -> str:
    """Compose the final advisory from whatever data was gathered.

    Only tools that actually ran appear in the prompt. Padding the context with
    "unavailable" placeholders for tools this question never needed gave the
    model more to ignore and, in practice, more to get confused by.
    """
    style = style_for(channel, farmer_query)
    temporal = temporal_context()
    script_rule = script_instruction(farmer_query)

    # The advisory prompt used to see only the current question, so it could not
    # tell that it had already asked something, or that the farmer had answered
    # it. A farmer who said "it is harvested" was asked for the crop stage
    # again in the very next reply. Closing questions are only worth asking once.
    recent = "\n    ".join(
        f"{h.get('role', 'user').capitalize()}: {h.get('content', '')}"
        for h in (history or [])[-6:]
    )
    conversation = (
        "\n    CONVERSATION SO FAR (do not repeat a question already asked "
        "here, and treat anything the farmer has already told you as known):"
        f"\n    {recent}\n" if recent else ""
    )

    context_lines = [
        f"- Location: {location or 'unknown'}",
        f"- State: {state or 'unknown'}",
        f"- Crop: {crop or 'unknown'}",
    ]
    for label, key in (("APPROVED PESTICIDE USES", "doses"),
                       ("Soil", "soil"), ("Weather", "weather"),
                       ("Mandi Price", "mandi"),
                       ("MINIMUM SUPPORT PRICE", "msp"),
                       ("Knowledge Base", "kb")):
        value = gathered.get(key)
        if value:
            context_lines.append(f"- {label}: {value}")
    context = "\n    ".join(context_lines)

    prompt = f"""
    You are an expert agronomist and agricultural advisor.
    Answer the farmer's question using the provided data.
    Always respond in the farmer's query language:
    Farmer's Query: {farmer_query}.

    {temporal}

    SCRIPT (overrides everything else): {script_rule}

    Constraints:
    - {style}
    - Be practical and farmer-friendly.
    - Identify the exact language and script of the farmer's question and reply in that same one. Punjabi in Gurmukhi is answered in Gurmukhi, Bengali in Bengali, Telugu in Telugu, English in English. Hindi is NOT a default: never answer an Indian-language question in Hindi unless the farmer wrote in Hindi. The location the farmer mentions NEVER decides the language: a question written in English about Nagpur is answered in English, not Marathi; about Ludhiana, in English, not Punjabi. Match the question, not the place.
    - Cite a data point ONLY where it changes what the farmer should do. Soil pH matters for a fertiliser question; today's rainfall matters if it affects spraying or drainage. Appending an unrelated figure - the day's rainfall onto a pest answer - is noise, and the farmer did not ask for it. When nothing in the data changes the advice, do not mention the data at all.
    - If the farmer asks a direct factual question - the temperature, the humidity, the rainfall, the price - ANSWER IT with the exact figure from the Context. Never say you cannot provide live data when the figure is sitting in the Context above. If only part of what they asked for is present, give that part and say the rest is not available.
    - The Context is measured data for this farmer's own location. Trust it over any assumption the farmer states: if they say the weather is dry and the data shows heavy rain, tell them plainly what the data says.
    - NEVER name a price scheme you have no figure for. If there is no MINIMUM SUPPORT PRICE section above, then you do not have the support price for this crop: say plainly that you do not have it and name who does - the local APMC mandi, a cooperative sugar mill for sugarcane. Do NOT mention MSP, FRP, "Fair and Remunerative Price", "government support price" or "a reliable floor" as though it were an answer while quoting no number, and never invite the farmer to go and look it up themselves as a substitute for answering. A named scheme with no figure attached is worse than admitting you do not have it.
    - PRICES: the mandi rate and the Minimum Support Price answer different questions and are not interchangeable. If a MINIMUM SUPPORT PRICE section is present, give that figure - it is current for the whole marketing year and is a real answer, not a substitute for one. When the live mandi rate is unavailable, say so in a few words and then give the support price as the floor the farmer is guaranteed; do not end on "unavailable" while the support price is sitting in the Context. Never present the support price as today's market rate, and never quote a support price that is not in the Context - most vegetables have none.
    - Do not invent facts beyond the given data.
    - CHEMICALS AND DOSES: if an APPROVED PESTICIDE USES section is present, you may name a pesticide and a dose ONLY if it appears there, quoted exactly, and you should say it is a registered use. If that section says nothing is registered, or warns the listed uses are for a different pest, then name NO chemical and NO dose at all - say you have no approved treatment on record and tell them to ask their Krishi Vigyan Kendra or agriculture officer. Never fall back on a chemical you happen to know. But do NOT stop at the refusal: give at least one practical PEST-CONTROL measure that needs no registered product - destroying and removing affected plant material, field sanitation, pheromone or bait traps, bagging fruit, collecting and burying fallen fruit, crop rotation, resistant varieties, adjusting sowing or irrigation timing, hand-picking at low infestation. These carry no chemical risk and are often what the extension officer would advise first. A refusal that offers nothing is a wasted message to a farmer whose crop is being eaten. This applies ONLY to pest and disease control. It is NOT licence to elaborate on any other refusal: where a scheme, subsidy or price is not in the Context, state that plainly and add no figures, percentages or eligibility rules of your own.
    - WHEN THE FARMER SAYS THEY CANNOT DO WHAT YOU SUGGESTED - no animals for dung, no irrigation, no money for a product, no tractor - that is not the end of the conversation and it is not a reason to start over. Do not repeat the suggestion they just ruled out, and do not ask a fresh unrelated question. Give them the next best option that fits what they DO have: compost can be bought or made from crop residue alone, green manure can be grown, a neighbour's dairy sells dung. Name the alternative in the same breath as acknowledging the constraint.
    - End with ONE short, specific question only where the answer would genuinely change with it - the crop stage, how widespread the damage is, whether they have irrigation. Never ask for something already given above. If nothing useful is missing, end with the advice.
    - Format response clearly into sections.

    {conversation}
    Context:
    {context}
    - Farmer's Question: {farmer_query}
    """

    return llm.invoke([HumanMessage(content=prompt)]).content


def gather(tools, *, lat, lon, state, crop, query, pest=None) -> dict:
    """Run the selected tools concurrently.

    They are independent network calls, so running them in sequence meant the
    slowest upstream set the floor for every answer. One failing tool no longer
    delays or breaks the others.
    """
    if not tools:
        return {}

    calls = {
        # Registered uses come from a table, not a model. The result carries
        # whether the pest actually matched, because a use for a different pest
        # must never be offered as an answer for this one.
        "doses":   lambda: knowledge.format_uses(
            knowledge.approved_uses(crop, pest), pest
        ),
        "soil":    lambda: soil_tool(lat, lon),
        "weather": lambda: weather_openmeteo(lat, lon),
        "mandi":   lambda: get_state_data(state, crop),
        "msp":     lambda: msp.for_crop(crop),
        # Retrieval runs against the local pgvector store. The Bedrock path is
        # kept as a fallback for anyone who has that configured, but it is no
        # longer what the feature depends on.
        "kb":      lambda: knowledge.format_passages(knowledge.search(query))
                   if knowledge.documents_loaded() else query_kb(query),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=len(tools)) as pool:
        futures = {pool.submit(calls[t]): t for t in tools if t in calls}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                print(f"Tool {name} failed: {e}")
    return results


class AgentResult:
    """An answer plus what the agent learned about the farmer along the way.

    The facts are what let a profile improve over time: whatever the parser
    picked out of this message is worth remembering for the next one.
    """

    __slots__ = ("answer", "crop", "state", "location", "latitude", "longitude",
                 "tools_used", "missing_slots", "intent", "message_type")

    def __init__(self, answer, crop=None, state=None, location=None,
                 latitude=None, longitude=None, tools_used=None,
                 missing_slots=None, intent="general", message_type="question"):
        self.answer = answer
        self.crop = crop
        self.state = state
        self.location = location
        self.latitude = latitude
        self.longitude = longitude
        self.tools_used = tools_used or []
        # What the farmer still has not told us - lets the caller ask for one
        # precise thing instead of a generic "where are you?".
        self.missing_slots = missing_slots or []
        self.intent = intent
        self.message_type = message_type


def _known(value) -> str | None:
    if not value or str(value).strip().lower() == "unknown":
        return None
    return str(value).strip()


def run_agent(
    query: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    history: Optional[List[dict]] = None,
    channel: str = "web",
    profile: Optional[dict] = None,
) -> AgentResult:
    """Answer a farmer's question and report what was learned and used."""
    query_final = get_farming_query(query, history)
    print(f"Final query after refinement: {query_final}")

    structured_input = extract_farm_info(query_final, original=query)

    crop = structured_input.get("crop_type", "unknown")
    state = structured_input.get("state", "unknown")
    location = structured_input.get("location", "unknown")
    answer = structured_input.get("answer", "unknown")
    intent = planner.normalise_intent(structured_input.get("intent"))
    message_type = planner.normalise_message_type(structured_input.get("message_type"))

    # A farmer reporting damage is asking for help even though they phrased it
    # as a fact. "My cotton has been attacked by locusts" is grammatically a
    # statement and was being answered with "noted, what would you like to
    # ask?" - which is useless, and reads as indifference to a crop being
    # eaten. Naming a topic at all means they want something done about it.
    if message_type == "statement" and intent != "general":
        message_type = "question"

    # A bare "yes" or "haan" is classified from the raw message, which looks
    # like smalltalk, while the intent is read from the rewritten query, which
    # has the conversation folded into it. When those disagree the rewrite is
    # the one that knows what is going on: a farmer answering "yess" to an offer
    # was being greeted from scratch, losing the thread entirely. Smalltalk that
    # carries a topic is a contradiction - it is a continuation.
    if message_type == "smalltalk" and intent != "general":
        message_type = "question"

    # And a short reply to a question we asked is an answer, whatever topic the
    # classifier managed to read out of one word. "nahin" carries none.
    if message_type in ("smalltalk", "statement") and answers_our_question(query, history):
        message_type = "question"

    print(f"Extracted Crop: {crop}, State: {state}, Location: {location}, "
          f"intent: {intent}, type: {message_type}, answer: {answer}")

    facts = {
        "crop": _known(crop),
        "state": _known(state),
        "location": _known(location),
    }

    # What this message did not say, the profile may already know. Without this
    # the agent asks a farmer for a crop it recorded last week, and cannot look
    # up a dose because it thinks it has no crop.
    if profile:
        for key, stored in (("crop", (profile.get("crops") or [None])[-1]),
                            ("state", profile.get("state")),
                            ("location", profile.get("location_text"))):
            if not facts[key] and stored:
                facts[key] = stored

    # A question about the assistant itself is answered from what the service
    # actually does, not from the model's general knowledge of agronomy.
    if message_type == "meta":
        return AgentResult(
            answer_about_system(query, history, channel, profile),
            tools_used=["about"], intent=intent, message_type=message_type, **facts
        )

    # A greeting is answered by saying what this service can do, which the
    # parser's generic reply does not. This has to come before the direct
    # answer below, which would otherwise swallow it.
    if message_type == "smalltalk":
        return AgentResult(
            greet(query, profile, channel),
            tools_used=["greeting"], intent=intent, message_type=message_type, **facts
        )

    # Non-agricultural query: the parser already answered it.
    if answer != "unknown" and crop == "unknown" and state == "unknown" and location == "unknown":
        return AgentResult(answer, tools_used=["direct"], message_type=message_type)

    # A location named in this message beats a remembered or browser one, but
    # only if geocoding actually resolves it.
    lat, lon = latitude, longitude
    if _known(location):
        geo_lat, geo_lon = get_location(facts["location"])
        if geo_lat is not None and geo_lon is not None:
            lat, lon = geo_lat, geo_lon

    facts["latitude"], facts["longitude"] = lat, lon
    print(f"Coordinates: lat={lat}, lon={lon}")

    # Which tools this question can actually use, given what we know. A leaf
    # spot question no longer waits on mandi prices it will never mention.
    tools = planner.plan_tools(
        intent,
        has_coords=lat is not None and lon is not None,
        state=facts["state"],
        kb_available=knowledge.documents_loaded() or kb_available(),
        crop=facts["crop"],
    )
    missing = planner.missing_slots(
        intent, crop=facts["crop"], location=facts["location"], state=facts["state"]
    )
    print(f"Plan: intent={intent} type={message_type} "
          f"tools={sorted(tools) or 'none'} missing={missing or 'nothing'}")

    # The farmer is objecting to the previous reply. Answer what they actually
    # asked instead of apologising and repeating it.
    if message_type == "correction":
        return AgentResult(
            handle_correction(query, history, channel),
            tools_used=["correction"], missing_slots=missing,
            intent=intent, message_type=message_type, **facts
        )

    # The farmer told us something rather than asking. Confirm what was noted
    # and stop; unrequested advice is what prompted "I didnt ask about sowing?".
    if message_type == "statement":
        return AgentResult(
            acknowledge_statement(query, {
                "crop": facts["crop"], "location": facts["location"],
                "state": facts["state"],
            }, channel, script_query=query),
            tools_used=["acknowledge"], missing_slots=missing,
            intent=intent, message_type=message_type, **facts
        )

    if not tools:
        return AgentResult(
            extract_markdown_content(get_open_ended_answer(query_final, history, channel)),
            tools_used=["general"], missing_slots=missing, intent=intent, **facts
        )

    gathered = gather(tools, lat=lat, lon=lon, state=facts["state"], crop=facts["crop"],
                      query=query_final, pest=_known(structured_input.get("pest")))

    final_response = extract_markdown_content(
        get_farming_advice(facts["location"], facts["state"], facts["crop"],
                           gathered, query, channel, history=history)
    )
    # A price or a subsidy is a number a farmer acts on. If nothing retrieved
    # supports it, no sentence claiming one survives, whatever the model wrote.
    final_response, scrubbed = output_guards.scrub(final_response, gathered)
    if scrubbed:
        print("Removed an unsupported figure from the answer")
    print(f"Final response: {final_response}")
    return AgentResult(
        final_response, tools_used=sorted(gathered.keys()),
        missing_slots=missing, intent=intent, **facts
    )


def agent(
    query: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    history: Optional[List[dict]] = None,
    channel: str = "web",
) -> str:
    """Answer text only. Kept for callers that do not need the extracted facts."""
    return run_agent(query, latitude, longitude, history, channel).answer
