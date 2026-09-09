import output_guards


def _extension(content):
    return {
        "tier": "extension",
        "content": content,
        "scope": {"states": ["Punjab"], "crops": ["wheat"]},
    }


def test_supported_fertilizer_quantity_survives():
    answer = "Apply 55 kg DAP per acre at sowing."
    gathered = {"_kb_passages": [_extension("Apply 55 kg DAP per acre at sowing.")]}
    assert output_guards.scrub(answer, gathered) == (answer, False)


def test_different_fertilizer_quantity_is_removed():
    answer = "Apply 75 kg DAP per acre at sowing. Keep the field evenly moist."
    gathered = {"_kb_passages": [_extension("Apply 55 kg DAP per acre at sowing.")]}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "75 kg" not in cleaned
    assert "evenly moist" in cleaned


def test_soil_weather_dates_and_intervals_are_not_input_claims():
    text = "Soil pH is 8.0, rain was 25 mm, and recheck after 20 days."
    assert output_guards.extract_input_claims(text) == set()


def test_pesticide_quantity_must_match_structured_context():
    answer = "Spray 500 ml per hectare. Remove affected leaves as well."
    gathered = {"doses": "Registered pesticide uses: dose 250 ml per hectare"}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "500 ml" not in cleaned
    assert "Remove affected leaves" in cleaned


def test_pesticide_waiting_period_must_match_structured_context():
    answer = "Wait 30 days before harvest. Remove affected leaves now."
    gathered = {"doses": "Registered pesticide uses: wait 21 days before harvest"}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "30 days" not in cleaned
    assert "Remove affected leaves" in cleaned


def test_different_msp_figure_is_removed_even_when_msp_exists():
    answer = "The MSP for wheat is Rs 3,000 per quintal."
    gathered = {"msp": "Minimum Support Price for Wheat in 2026-27: Rs 2,585 per quintal."}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "3,000" not in cleaned


def test_reference_passage_cannot_authorize_a_subsidy_figure():
    answer = "The scheme pays a 50% subsidy. Ask the district office for eligibility."
    gathered = {"_kb_passages": [{"tier": "reference", "content": "A 50% subsidy is discussed."}]}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "50%" not in cleaned
    assert "district office" in cleaned


def test_unregistered_pesticide_product_is_removed():
    answer = "Spray Acephate 75% SP at 500 ml per hectare. Remove affected leaves."
    gathered = {
        "doses": "Registered pesticide uses: Emamectin Benzoate 5% SG, dose 250 ml per hectare",
        "_dose_records": [{"product": "Emamectin Benzoate 5% SG"}],
    }
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "Acephate" not in cleaned
    assert "Remove affected leaves" in cleaned
