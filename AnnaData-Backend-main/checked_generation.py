"""Release completed, checked sentences while the model is still generating."""
import re

import output_guards


MAX_ANSWER_CHARS = 64000
_BOUNDARY = re.compile(r"[.!?।](?:\*\*|__)?\s+(?=\S)")


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block["text"] for block in content
                       if isinstance(block, dict) and block.get("type") == "text"
                       and isinstance(block.get("text"), str))
    return ""


def _boundary(text):
    # Preserve offsets while protecting currency abbreviations such as Rs.
    protected = output_guards._ABBREVIATION_PERIOD.sub(
        lambda match: match.group()[:-1] + "\x00", text)
    for match in _BOUNDARY.finditer(protected):
        if not re.fullmatch(r"\s*\d+\.\s*", text[:match.end()]):
            return match.end()
    return None


def generate_checked(model, messages, gathered, on_text):
    pending, accepted = "", []
    received = 0

    def release(unit):
        cleaned, changed = output_guards.scrub(unit, gathered, finalize=False)
        if not cleaned.strip():
            return
        if changed and unit[-1:].isspace():
            cleaned += "\n" if "\n" in unit[-2:] else " "
        accepted.append(cleaned)
        on_text(cleaned)

    # Runnable fallbacks may switch providers before the first chunk, but a
    # failure after output starts must terminate rather than splice two answers.
    for chunk in model.stream(messages):
        text = _text(chunk.content)
        received += len(text)
        if received > MAX_ANSWER_CHARS:
            raise ValueError("Answer exceeds stream limit")
        pending += text
        # Whole-answer markdown fences need unwrapping before display. Do not
        # interpret partially received fence markers as ordinary answer text.
        if pending.lstrip().startswith("`"):
            continue
        while (end := _boundary(pending)) is not None:
            release(pending[:end])
            pending = pending[end:]

    if not received:
        raise ValueError("Empty model answer")
    fenced = re.fullmatch(r"\s*```(?:markdown)?\n([\s\S]*?)\n```\s*", pending)
    release(fenced.group(1).strip() if fenced else pending)
    checked = "".join(accepted).rstrip()
    final, _ = output_guards.scrub(checked, gathered)
    final = final or output_guards.ACTION_FALLBACK
    if final.startswith(checked) and final[len(checked):]:
        on_text(final[len(checked):])
    return final
