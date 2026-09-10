from types import SimpleNamespace

import pytest

import output_guards


def generate(chunks, context, emit):
    from checked_generation import generate_checked
    model = SimpleNamespace(stream=lambda messages: (
        SimpleNamespace(content=part) for part in chunks
    ))
    return generate_checked(model, [], context, emit)


def test_filter_only_does_not_inject_a_fallback_for_each_rejected_sentence():
    cleaned, changed = output_guards.scrub(
        'Spray 500 ml per hectare.', {'_guard_context': {'intent': 'disease_pest'}},
        finalize=False,
    )
    assert cleaned == ''
    assert changed


def test_checked_sentence_arrives_before_generation_finishes():
    emitted = []
    def chunks():
        yield 'Keep the field moist. '
        assert emitted == [], 'wait until the sentence boundary is confirmed'
        yield 'Remove affected leaves'
        assert ''.join(emitted) == 'Keep the field moist. '
        yield ' promptly.'
    result = generate(chunks(), {}, emitted.append)
    assert result == 'Keep the field moist. Remove affected leaves promptly.'
    assert ''.join(emitted) == result


@pytest.mark.parametrize('chunks', [
    ['Spray 500 ', 'ml\nper hectare. ', 'Remove affected leaves.'],
    ['Spray 2.', '5 ml per hectare. ', 'Remove affected leaves.'],
    ['The scheme pays Rs.', ' 8,000 per year. ', 'Ask the agriculture office.'],
    ['The MSP is Rs. 9,999 per quintal. ', 'Ask the local market.'],
])
def test_unsupported_claims_never_reach_streamed_text(chunks):
    emitted = []
    result = generate(chunks, {}, emitted.append)
    streamed = ''.join(emitted)
    for forbidden in ('500', '2.5', '8,000', '9,999'):
        assert forbidden not in streamed
        assert forbidden not in result
    assert streamed.strip()


def test_complete_markdown_fences_are_unwrapped_before_display():
    emitted = []
    result = generate(['```mark', 'down\nKeep soil moist. ', 'Remove weeds.\n```'], {}, emitted.append)
    assert result == 'Keep soil moist. Remove weeds.'
    assert ''.join(emitted) == result


def test_all_rejected_text_gets_one_final_fallback():
    emitted = []
    result = generate(['Spray 500 ml per hectare.'], {}, emitted.append)
    assert result == output_guards.ACTION_FALLBACK
    assert emitted == [result]


def test_provider_failure_is_not_converted_into_a_complete_answer():
    emitted = []
    def chunks():
        yield 'Keep soil moist. Remove'
        raise RuntimeError('provider unavailable')
    with pytest.raises(RuntimeError):
        generate(chunks(), {}, emitted.append)
    assert ''.join(emitted) == 'Keep soil moist. '


def test_thought_blocks_are_not_exposed():
    emitted = []
    result = generate([[{'type': 'thinking', 'thinking': 'private reasoning'},
                        {'type': 'text', 'text': 'Keep soil moist.'}]], {}, emitted.append)
    assert result == 'Keep soil moist.'
    assert 'private reasoning' not in ''.join(emitted)


def test_supported_scheme_amount_streams_and_authority_is_appended_once():
    emitted = []
    context = {'_kb_passages': [{
        'tier': 'official', 'content': 'PM-KISAN pays Rs. 6,000 per year.',
        'authority': 'Department of Agriculture',
    }]}
    result = generate(['PM-KISAN pays Rs. ', '6,000 per year. ', 'Check your eligibility.'],
                      context, emitted.append)
    assert '6,000' in ''.join(emitted)
    assert ''.join(emitted) == result
    assert result.count('Source: Department of Agriculture.') == 1


def test_claim_filtering_is_stable_at_every_chunk_boundary():
    text = 'Spray 2.5 ml per hectare. The scheme pays Rs. 8,000. Remove affected leaves.'
    for split in range(1, len(text)):
        emitted = []
        result = generate([text[:split], text[split:]], {}, emitted.append)
        assert '2.5' not in ''.join(emitted)
        assert '8,000' not in ''.join(emitted)
        assert result == 'Remove affected leaves.'


def test_incomplete_answer_buffer_is_bounded():
    from checked_generation import MAX_ANSWER_CHARS
    emitted = []
    with pytest.raises(ValueError, match='limit'):
        generate(['x' * (MAX_ANSWER_CHARS + 1)], {}, emitted.append)
    assert not emitted
