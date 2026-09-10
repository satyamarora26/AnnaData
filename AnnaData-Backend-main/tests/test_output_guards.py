import output_guards


def _extension(content, scope=None):
    return {
        "tier": "extension",
        "content": content,
        "scope": scope if scope is not None else {"states": ["Punjab"], "crops": ["wheat"]},
    }


def test_supported_fertilizer_quantity_survives():
    answer = "Apply 55 kg DAP per acre at sowing."
    gathered = {
        "_guard_context": {"state": "Punjab", "crop": "wheat"},
        "_kb_passages": [_extension("Apply 55 kg DAP per acre at sowing.")],
    }
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


def test_out_of_scope_extension_quantity_is_removed():
    answer = "Apply 55 kg DAP per acre. Keep the field evenly moist."
    gathered = {
        "_guard_context": {"state": "Punjab", "crop": "wheat"},
        "_kb_passages": [
            _extension("Apply 55 kg DAP per acre.", {"states": ["Haryana"], "crops": ["wheat"]})
        ],
    }
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "55 kg" not in cleaned
    assert "evenly moist" in cleaned


def test_malformed_extension_scope_cannot_authorize_quantity():
    answer = "Apply 55 kg DAP per acre. Keep the field evenly moist."
    gathered = {
        "_guard_context": {"state": "Punjab", "crop": "wheat"},
        "_kb_passages": [_extension("Apply 55 kg DAP per acre.", {"states": "Punjab"})],
    }
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "55 kg" not in cleaned
    assert "evenly moist" in cleaned


def test_pesticide_product_dose_and_wait_must_share_one_record():
    answer = "Spray Product A at 250 ml per hectare and wait 30 days before harvest. Remove affected leaves."
    gathered = {
        "doses": (
            "Registered pesticide uses: Product A, dose 250 ml per hectare, wait 21 days before harvest. "
            "Product B, dose 500 ml per hectare, wait 30 days before harvest."
        ),
        "_dose_records": [
            {"product": "Product A", "dose_formulation": "250 ml per hectare", "waiting_period": "21"},
            {"product": "Product B", "dose_formulation": "500 ml per hectare", "waiting_period": "30"},
        ],
    }
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "Product A" not in cleaned
    assert "Remove affected leaves" in cleaned


def test_apply_action_cannot_bypass_pesticide_product_guard():
    answer = "Apply Acephate at 500 ml per hectare. Remove affected leaves."
    gathered = {
        "doses": "Registered pesticide uses: Emamectin Benzoate 5% SG, dose 250 ml per hectare",
        "_dose_records": [{"product": "Emamectin Benzoate 5% SG", "dose_formulation": "250 ml per hectare"}],
    }
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "Acephate" not in cleaned
    assert "Remove affected leaves" in cleaned


def test_abbreviated_rupee_sentence_keeps_subject_and_figure_together():
    answer = "The MSP is Rs. 3,000 per quintal. Keep records of your sale."
    gathered = {"msp": "Minimum Support Price for Wheat: Rs 2,585 per quintal."}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "3,000" not in cleaned
    assert "Keep records" in cleaned


def test_newline_separates_unsafe_quantity_from_practical_advice():
    answer = "Apply 75 kg DAP per acre\nKeep the field evenly moist."
    gathered = {"_kb_passages": [_extension("Apply 55 kg DAP per acre.")]}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "75 kg" not in cleaned
    assert "evenly moist" in cleaned


def test_soft_wrapped_quantity_is_removed_before_sentence_splitting():
    answer = "Apply 75 kg\nDAP per acre\nKeep the field evenly moist."
    gathered = {
        "_guard_context": {"state": "Punjab", "crop": "wheat"},
        "_kb_passages": [_extension("Apply 55 kg DAP per acre.")],
    }
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "75 kg" not in cleaned
    assert "DAP per acre" not in cleaned
    assert "evenly moist" in cleaned


def test_reference_passage_cannot_authorize_legal_quantity():
    answer = "The legal limit is 5 kg per acre. Keep records of applications."
    gathered = {"_kb_passages": [{"tier": "reference", "content": "The legal limit is 5 kg per acre."}]}
    cleaned, changed = output_guards.scrub(answer, gathered)
    assert changed
    assert "5 kg" not in cleaned
    assert "Keep records" in cleaned


def test_scheme_figure_names_retrieved_official_authority():
    answer = "PM-KISAN provides Rs 6,000 per year."
    gathered = {
        "_kb_passages": [{
            "tier": "official",
            "content": "PM-KISAN provides Rs 6,000 per year to eligible farmers.",
            "authority": "Department of Agriculture and Farmers Welfare, Government of India",
        }],
    }

    cleaned, changed = output_guards.scrub(answer, gathered)

    assert changed
    assert "Rs 6,000" in cleaned
    assert "Department of Agriculture and Farmers Welfare, Government of India" in cleaned


def test_exact_fertilizer_question_without_evidence_adds_field_referral():
    answer = "Use zinc fertiliser carefully."
    gathered = {
        "_guard_context": {
            "intent": "fertiliser_nutrition",
            "query": "Exactly how much zinc fertiliser should I use per acre?",
        },
    }

    cleaned, changed = output_guards.scrub(answer, gathered)

    assert changed
    assert "soil test" in cleaned.lower()
    assert "Krishi Vigyan Kendra" in cleaned


def test_latin_registered_pesticide_answer_names_one_retained_record():
    answer = "Please inspect the affected plants before spraying."
    gathered = {
        "_dose_records": [{
            "product": "Acephate 75% SP",
            "crop": "Cotton",
            "pest": "Bollworm",
            "dose_formulation": "667 g/ha",
        }],
        "_guard_context": {"intent": "disease_pest", "script": "Latin"},
    }

    cleaned, changed = output_guards.scrub(answer, gathered)

    assert changed
    assert "Acephate 75% SP" in cleaned
    assert "667 g/ha" in cleaned
