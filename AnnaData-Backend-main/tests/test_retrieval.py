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
