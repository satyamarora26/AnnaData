from types import SimpleNamespace

import Agent
import knowledge
import planner
import provenance


def _passage(source, tier, similarity, scope=None):
    return {
        "content": f"guidance from {source}",
        "source": source,
        "title": source,
        "url": f"https://example.gov.in/{source}",
        "authority": source,
        "tier": tier,
        "scope": scope or {},
        "topics": ["fertiliser_nutrition"],
        "similarity": similarity,
    }


def test_matching_extension_guidance_beats_unscoped_material():
    rows = [
        _passage("central", "official", 0.82),
        _passage("pau", "extension", 0.78, {"states": ["Punjab"], "crops": ["wheat"]}),
    ]
    ranked = knowledge.rank_passages(rows, "Punjab", "wheat", min_similarity=0.70)
    assert [row["source"] for row in ranked] == ["pau", "central"]


def test_out_of_scope_extension_guidance_is_removed():
    rows = [_passage("pau", "extension", 0.92, {"states": ["Punjab"]})]
    assert knowledge.rank_passages(rows, "West Bengal", "rice", min_similarity=0.70) == []


def test_reference_loses_to_official_at_equal_scope():
    rows = [_passage("blog", "reference", 0.90), _passage("icar", "official", 0.75)]
    ranked = knowledge.rank_passages(rows, None, None, min_similarity=0.70)
    assert [row["source"] for row in ranked] == ["icar", "blog"]


def test_only_five_passages_reach_the_prompt():
    rows = [_passage(f"official-{index}", "official", 0.90 - index / 100)
            for index in range(8)]
    assert len(knowledge.rank_passages(rows, None, None, limit=5,
                                       min_similarity=0.70)) == 5


def test_formatted_passage_names_authority_tier_and_url():
    text = knowledge.format_passages([_passage("icar", "official", 0.82)],
                                     min_similarity=0.70)
    assert "OFFICIAL" in text
    assert "icar" in text
    assert "https://example.gov.in/icar" in text


def test_fertilizer_questions_retrieve_knowledge_when_available():
    tools = planner.plan_tools("fertiliser_nutrition", has_coords=True,
                               state="Punjab", crop="wheat", kb_available=True)
    assert tools == {"soil", "weather", "kb"}


def test_provenance_counts_only_active_documents_by_tier(monkeypatch):
    queries = []

    class Cursor:
        def __init__(self, query):
            self.query = query

        def fetchall(self):
            if "GROUP BY tier" in self.query:
                return [("official", 2), ("extension", 1), ("reference", 3)]
            return [("PM-KISAN", "pm-kisan")]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query):
            queries.append(query)
            return Cursor(query)

    monkeypatch.setattr(provenance.db, "is_available", lambda: True)
    monkeypatch.setattr(provenance.db, "connection", Connection)
    monkeypatch.setattr(provenance.knowledge, "counts", lambda: {"pesticide_uses": 0, "documents": 6})
    monkeypatch.setattr(provenance.startup, "is_available", lambda: False)
    monkeypatch.setattr(provenance.msp, "counts", lambda: {"msp_commodities": 0})
    monkeypatch.setattr(provenance.Mandi_Price_Tool, "is_available", lambda: False)

    sources = provenance.data_sources()

    assert next(source for source in sources if source.startswith("Official documents")) == (
        "Official documents (2 active): issued by the responsible government authority."
    )
    assert any(source.startswith("Extension guidance (1 active)") for source in sources)
    assert any(source.startswith("Reference material (3 active)") for source in sources)
    assert all("active = TRUE" in query for query in queries)


def test_rank_limit_is_clamped_to_zero_through_five():
    rows = [_passage(f"source-{index}", "official", 0.9 - index / 100)
            for index in range(8)]

    assert len(knowledge.rank_passages(rows, None, None, limit=99, min_similarity=0.7)) == 5
    assert knowledge.rank_passages(rows, None, None, limit=0, min_similarity=0.7) == []
    assert knowledge.rank_passages(rows, None, None, limit=-1, min_similarity=0.7) == []


def test_rank_ties_use_stable_metadata_order():
    rows = [
        _passage("z-source", "official", 0.8),
        _passage("a-source", "official", 0.8),
    ]

    ranked = knowledge.rank_passages(rows, None, None, min_similarity=0.7)

    assert [row["source"] for row in ranked] == ["a-source", "z-source"]


def test_rank_rejects_malformed_or_non_object_scope():
    malformed = _passage("malformed", "official", 0.9)
    malformed["scope"] = "{not json}"
    non_object = _passage("array", "official", 0.9)
    non_object["scope"] = ["Punjab"]

    assert knowledge.rank_passages([malformed, non_object], "Punjab", "wheat", min_similarity=0.7) == []


def test_rank_requires_complete_https_citation_metadata():
    missing_authority = _passage("source", "official", 0.9)
    missing_authority["authority"] = ""
    missing_title = _passage("source-title", "official", 0.9)
    missing_title["title"] = "\n"
    insecure_url = _passage("source-two", "official", 0.9)
    insecure_url["url"] = "http://example.gov.in/source-two"

    assert knowledge.rank_passages([missing_authority, missing_title, insecure_url], None, None,
                                   min_similarity=0.7) == []
    assert "guidance from source" not in knowledge.format_passages(
        [missing_authority], min_similarity=0.7
    )


def test_formatted_citation_uses_distinct_sanitized_fields():
    row = _passage("document-key", "official", 0.9)
    row.update({
        "authority": "Indian Council | Research\nBoard",
        "title": "Wheat | Nutrient\rGuide",
        "url": "https://example.gov.in/wheat|guide",
    })

    text = knowledge.format_passages([row], min_similarity=0.7)

    assert "[OFFICIAL | Indian Council / Research Board | Wheat / Nutrient Guide | https://example.gov.in/wheat%7Cguide]" in text
    assert "\nBoard" not in text
    assert "\rGuide" not in text


def test_formatted_guidance_orders_scope_before_tier():
    text = knowledge.format_passages([_passage("icar", "official", 0.9)], min_similarity=0.7)

    assert "Among equally applicable passages, prefer OFFICIAL material." in text
    assert "matching scoped EXTENSION passage can be more useful than unscoped central material" in text
    assert "REFERENCE material never authorizes a scheme amount, deadline, MSP, or fertiliser quantity." in text


def test_search_with_none_topic_binds_typed_null_and_defensive_candidate_limit(monkeypatch):
    calls = []

    class Cursor:
        def fetchall(self):
            return [_search_row()]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params):
            calls.append((query, params))
            return Cursor()

    monkeypatch.setattr(knowledge.db, "is_available", lambda: True)
    monkeypatch.setattr(knowledge.db, "connection", Connection)
    monkeypatch.setattr(knowledge, "embed", lambda _: [0.1, 0.2])

    results = knowledge.search("wheat nutrition", topic=None, candidate_limit="not-a-number")

    assert [row["source"] for row in results] == ["search-source"]
    query, params = calls[0]
    assert "%s::text IS NULL" in query
    assert params[1:3] == (None, None)
    assert params[-1] == 20


def test_gather_calls_approved_uses_once_and_keeps_matching_raw_records(monkeypatch):
    calls = []
    raw_uses = [{"product": "Approved product", "crop": "rice", "pest": "blast"}]

    def approved(crop, pest):
        calls.append((crop, pest))
        return {"uses": raw_uses, "pest_matched": True}

    monkeypatch.setattr(Agent.knowledge, "approved_uses", approved)
    monkeypatch.setattr(Agent.knowledge, "format_uses", lambda result, pest: "approved text")

    result = Agent.gather({"doses"}, lat=None, lon=None, state="Punjab", crop="rice",
                          query="blast", intent="disease_pest", pest="blast")

    assert calls == [("rice", "blast")]
    assert result == {"doses": "approved text", "_dose_records": raw_uses}


def test_gather_drops_raw_dose_records_when_pest_does_not_match(monkeypatch):
    monkeypatch.setattr(
        Agent.knowledge, "approved_uses",
        lambda crop, pest: {"uses": [{"product": "other"}], "pest_matched": False},
    )
    monkeypatch.setattr(Agent.knowledge, "format_uses", lambda result, pest: "warning text")

    result = Agent.gather({"doses"}, lat=None, lon=None, state="Punjab", crop="rice",
                          query="blast", intent="disease_pest", pest="blast")

    assert result == {"doses": "warning text"}


def test_gather_scopes_knowledge_search_and_keeps_raw_passages(monkeypatch):
    search_calls = []
    passages = [_passage("knowledge-source", "official", 0.9)]

    def search(query, **kwargs):
        search_calls.append((query, kwargs))
        return passages

    monkeypatch.setattr(Agent.knowledge, "search", search)
    monkeypatch.setattr(Agent.knowledge, "format_passages", lambda rows: "knowledge text")

    result = Agent.gather({"kb"}, lat=None, lon=None, state="Punjab", crop="wheat",
                          query="fertiliser dose", intent="fertiliser_nutrition")

    assert result == {"kb": "knowledge text", "_kb_passages": passages}
    assert search_calls == [("fertiliser dose", {
        "state": "Punjab", "crop": "wheat", "topic": "fertiliser_nutrition",
        "candidate_limit": 20, "limit": 5,
    })]


def test_private_evidence_is_excluded_from_advice_prompt(monkeypatch):
    prompts = []

    class FakeLLM:
        def invoke(self, messages):
            prompts.append(messages[0].content)
            return SimpleNamespace(content="answer")

    monkeypatch.setattr(Agent, "llm", FakeLLM())

    Agent.get_farming_advice(
        "Ludhiana", "Punjab", "wheat",
        {"kb": "public evidence", "_kb_passages": "private evidence", "_dose_records": "private doses"},
        "What fertiliser should I use?",
    )

    assert "public evidence" in prompts[0]
    assert "private evidence" not in prompts[0]
    assert "private doses" not in prompts[0]


def test_run_agent_uses_selected_tools_not_private_evidence_keys(monkeypatch):
    gather_calls = []
    monkeypatch.setattr(Agent, "get_farming_query", lambda query, history: query)
    monkeypatch.setattr(Agent, "extract_farm_info", lambda *args, **kwargs: {
        "crop_type": "wheat", "state": "Punjab", "location": "unknown", "answer": "unknown",
        "intent": "fertiliser_nutrition", "message_type": "question", "pest": None,
    })
    monkeypatch.setattr(Agent.planner, "plan_tools", lambda *args, **kwargs: {"kb"})
    monkeypatch.setattr(Agent.planner, "missing_slots", lambda *args, **kwargs: [])
    monkeypatch.setattr(Agent.knowledge, "documents_loaded", lambda: True)

    def gather(tools, **kwargs):
        gather_calls.append((tools, kwargs))
        return {"kb": "public evidence", "_kb_passages": [{"content": "private"}]}

    monkeypatch.setattr(Agent, "gather", gather)
    monkeypatch.setattr(Agent, "get_farming_advice", lambda *args, **kwargs: "advice")
    monkeypatch.setattr(Agent, "extract_markdown_content", lambda value: value)
    monkeypatch.setattr(Agent.output_guards, "scrub", lambda answer, gathered: (answer, False))

    result = Agent.run_agent("fertiliser question")

    assert result.tools_used == ["kb"]
    assert gather_calls == [({"kb"}, {
        "lat": None, "lon": None, "state": "Punjab", "crop": "wheat",
        "query": "fertiliser question", "intent": "fertiliser_nutrition", "pest": None,
    })]


def test_active_scheme_names_drive_capabilities(monkeypatch):
    queries = []

    class Cursor:
        def fetchall(self):
            return [("PM-KISAN guidelines", "pm-kisan")]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query):
            queries.append(query)
            return Cursor()

    monkeypatch.setattr(provenance.db, "is_available", lambda: True)
    monkeypatch.setattr(provenance.db, "connection", Connection)
    monkeypatch.setattr(provenance.knowledge, "counts", lambda: {"pesticide_uses": 0, "documents": 1})
    monkeypatch.setattr(provenance.startup, "is_available", lambda: False)
    monkeypatch.setattr(provenance.msp, "counts", lambda: {"msp_commodities": 0})
    monkeypatch.setattr(provenance.Mandi_Price_Tool, "is_available", lambda: False)
    monkeypatch.setattr(provenance.Web_Crawler, "is_available", lambda: False)

    assert provenance._scheme_names() == ["PM-KISAN"]
    assert any("PM-KISAN" in item for item in provenance.capabilities())
    assert all("active = TRUE" in query for query in queries)


def _search_row():
    return (
        "retrieved evidence", "search-source", "Search title", "https://example.gov.in/search",
        "Search authority", "official", "{}", ["fertiliser_nutrition"], 0.9,
    )
