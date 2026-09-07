from langchain.output_parsers import StructuredOutputParser, ResponseSchema
from langchain.prompts import PromptTemplate
from planner import normalise_intent, normalise_message_type
from utils import llm

UNKNOWN = {
    "location": "unknown",
    "state": "unknown",
    "crop_type": "unknown",
    "answer": "unknown",
    "intent": "general",
    "message_type": "question",
    "pest": "unknown",
}

# Intent rides along on this extraction call, which runs for every query
# anyway, so choosing tools costs no extra model call.
INTENTS = (
    "disease_pest, sowing_planting, fertiliser_nutrition, irrigation_water, "
    "weather_query, market_price, scheme_subsidy, storage_postharvest, general"
)

# What KIND of message this is, as opposed to what it is about. Without this
# every message was treated as a request for advice, so a farmer stating where
# they farm got a lecture on sowing and a farmer asking how we know their soil
# got a description of laboratory titration.
MESSAGE_TYPES = "question, statement, meta, correction, smalltalk"


def extract_farm_info(farmer_input: str, original: str | None = None) -> dict:
    """Extract location, state, crop, topic and message kind from a query.

    `farmer_input` is the standalone rewrite, which is what the slots should be
    read from - it carries context the farmer left implicit. `original` is what
    they actually typed, and the message kind must come from that: the rewrite
    turns a statement into a question, so classifying it would report every
    statement as a request for advice.

    Always returns the same four keys. The old error branch returned a
    differently shaped dict, so callers silently got None for `state`.
    """
    response_schemas = [
        ResponseSchema(
            name="location",
            description="Location of the farm (village, district, state, etc.)",
        ),
        ResponseSchema(
            name="state",
            description="State where the farm is located. Use the location "
                        "information to locate the Indian state",
        ),
        ResponseSchema(name="crop_type", description="Type of crop being grown"),
        ResponseSchema(
            name="answer",
            description="If the query is not related to agriculture, provide a "
                        "short answer to the query in the query language.",
        ),
        ResponseSchema(
            name="pest",
            description="The pest, insect, weed or disease the farmer mentions, "
                        "in English and as its common name - bollworm, stem borer, "
                        "blast, rust, aphid, whitefly. Translate what they wrote: "
                        "sundi and इल्ली are bollworm, tana chhedak is stem borer. "
                        "'unknown' if they name no pest or disease.",
        ),
        ResponseSchema(
            name="message_type",
            description=f"What KIND of message this is. Exactly one of: {MESSAGE_TYPES}. "
                        "question = asking for farming advice or information. "
                        "statement = telling us something about themselves or their farm, not asking anything. "
                        "meta = asking about the assistant itself - how it knows something, where its data comes from, what it can do, who it is. "
                        "correction = objecting to or correcting the previous reply. "
                        "smalltalk = greeting, thanks, or unrelated chatter.",
        ),
        ResponseSchema(
            name="intent",
            description=f"What the farmer wants. Exactly one of: {INTENTS}. "
                        "disease_pest for crop problems, pests, diseases or damage. "
                        "sowing_planting for when or how to sow or plant. "
                        "fertiliser_nutrition for fertiliser, manure or deficiency. "
                        "irrigation_water for watering and drainage. "
                        "weather_query for a direct question about weather. "
                        "market_price for prices, selling or what is profitable. "
                        "scheme_subsidy for government schemes, subsidies, loans, insurance. "
                        "storage_postharvest for storage, cold storage or after harvest. "
                        "general for anything else.",
        ),
    ]

    output_parser = StructuredOutputParser.from_response_schemas(response_schemas)
    format_instructions = output_parser.get_format_instructions()

    prompt = PromptTemplate(
        template="""
    You are a farming assistant that extracts structured agricultural data from
    farmer queries. Always translate the language of the query into English.

    Farmer's input, rewritten to stand alone (use this for location, state, crop, intent):
    {farmer_input}

    What the farmer ACTUALLY typed (use ONLY this to decide message_type):
    {original}

    {format_instructions}

    Make sure:
    - "location" is just the location.
    - "state" is the state name, not the full address.
    - "crop_type" is only the crop name.
    - "pest" is only the pest or disease name, in English.
    - "answer" is a short answer to the query if it is not related to agriculture.
    - "intent" is exactly one of the listed values, nothing else.
    - "message_type" is exactly one of the listed values, nothing else. A message that only states a location or crop, with no question, is a "statement".
    If information is missing, put "unknown".
    """,
        input_variables=["farmer_input", "original"],
        partial_variables={"format_instructions": format_instructions},
    )

    try:
        _input = prompt.format_prompt(farmer_input=farmer_input,
                                      original=original or farmer_input)
        output = llm.invoke(_input.to_messages()).content
        print(f"LLM output: {output}")

        if not output:
            return dict(UNKNOWN)

        parsed = output_parser.parse(output)
        # Normalise: guarantee all four keys, never None.
        result = {k: (parsed.get(k) or UNKNOWN[k]) for k in UNKNOWN}
        result["intent"] = normalise_intent(result["intent"])
        result["message_type"] = normalise_message_type(result["message_type"])
        return result

    except Exception as e:
        print(f"Query parsing failed: {e}")
        return dict(UNKNOWN)
