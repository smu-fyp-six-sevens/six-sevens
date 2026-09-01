from __future__ import annotations

import random
import re
from abc import ABC, abstractmethod
from pathlib import Path

from .field_text import replace_value, require_value
from .pdf import (
    BUILTIN_FONT_HELVETICA,
    BUILTIN_FONT_TIMES,
    apply_text_emphasis,
    available_custom_fonts,
    replace_field_text,
    read_document_metadata,
    set_document_metadata,
    working_file_path,
)
from .schemas import DefectType, FieldValue, InjectedDefect, SampleDocument

_BUILTIN_ALT_FONTS = (
    BUILTIN_FONT_TIMES,  # Times Roman
    "cour",  # Courier
)

_FONT_LABELS = {
    "tiro": "Times Roman",
    "cour": "Courier",
    "arial": "Arial",
    "helv": "Helvetica",
}

_FAKE_AUTHORS = (
    "Unknown Editor",
    "Admin User",
    "PDF Editor Pro",
    "j.smith",
    "Document Scanner",
    "Adobe Acrobat",
)
_FAKE_CREATORS = (
    "Suspicious Toolkit",
    "ImageMagick",
    "Canva",
    "Preview",
    "Microsoft Print to PDF",
)
_FAKE_TITLES = (
    "Untitled",
    "Scan001",
    "document_final_v2",
    "Copy of original",
)
_LETTER_SWAPS = {
    "a": "e",
    "e": "a",
    "i": "l",
    "l": "i",
    "o": "0",
    "0": "o",
    "s": "5",
    "5": "s",
    "b": "d",
    "d": "b",
    "m": "n",
    "n": "m",
    "u": "v",
    "v": "u",
    "c": "e",
    "g": "q",
    "q": "g",
}


class DefectInjector(ABC):
    defect_type: DefectType

    @abstractmethod
    def inject(self, document: SampleDocument, field_name: str) -> InjectedDefect:
        raise NotImplementedError


def _snapshot(field: FieldValue) -> dict:
    return field.model_dump()


def _working_path(document: SampleDocument) -> Path:
    return working_file_path(document.filename)


def _rng() -> random.Random:
    return random.Random()


def _require_injectable_value(document: SampleDocument, field_name: str) -> str:
    field = document.fields[field_name]
    return require_value(field_name, field.text)


class FontInjector(DefectInjector):
    defect_type = DefectType.FONT

    def inject(self, document: SampleDocument, field_name: str) -> InjectedDefect:
        _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        current = (field.font or BUILTIN_FONT_HELVETICA).lower()
        choices = [
            name
            for name in (*_BUILTIN_ALT_FONTS, *available_custom_fonts())
            if name != current
        ]
        if not choices:
            choices = list(_BUILTIN_ALT_FONTS) or ["arial"]
        new_font = _rng().choice(choices)
        replace_field_text(_working_path(document), field, field.text, font=new_font)
        field.font = new_font
        label = _FONT_LABELS.get(new_font, new_font)
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Changed field font from {before['font']} to {label}",
            before=before,
            after=_snapshot(field),
        )


class FontSizeInjector(DefectInjector):
    defect_type = DefectType.FONT_SIZE

    def inject(self, document: SampleDocument, field_name: str) -> InjectedDefect:
        _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        # Mix of subtle and obvious size drifts (pt).
        delta = _rng().choice([-3.0, -2.0, -1.0, 1.0, 1.5, 2.0, 3.0, 4.0])
        new_size = max(6.0, round(field.font_size + delta, 1))
        if new_size == field.font_size:
            new_size = field.font_size + 2.0
        replace_field_text(_working_path(document), field, field.text, font_size=new_size)
        field.font_size = new_size
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=(
                f"Changed field font size from {before['font_size']} to {new_size}"
            ),
            before=before,
            after=_snapshot(field),
        )


def _tamper_number(original: str, rng: random.Random) -> str:
    """Return a different numeric string with similar shape."""
    strategies = ["bump", "random_digit", "transpose", "zero_pad"]
    # Prefer strategies that can actually apply.
    if "." in original:
        strategies = ["bump", "random_digit"]
    elif len(re.sub(r"\D", "", original)) < 2:
        strategies = ["bump", "random_digit"]

    strategy = rng.choice(strategies)

    if strategy == "bump":
        if "." in original:
            whole, fraction = original.split(".", 1)
            delta = rng.choice([-2, -1, 1, 2])
            value = max(0, int(fraction) + delta)
            return f"{whole}.{value:0{len(fraction)}d}"
        delta = rng.choice([-5, -2, -1, 1, 2, 5, 10])
        return str(max(0, int(original) + delta))

    if strategy == "transpose":
        digits = list(original)
        idxs = [i for i, ch in enumerate(digits) if ch.isdigit()]
        if len(idxs) >= 2:
            i, j = rng.sample(idxs, 2)
            # Prefer adjacent when possible.
            adjacent = [(a, b) for a, b in zip(idxs, idxs[1:]) if b == a + 1]
            if adjacent:
                i, j = rng.choice(adjacent)
            digits[i], digits[j] = digits[j], digits[i]
            candidate = "".join(digits)
            if candidate != original:
                return candidate

    if strategy == "zero_pad" and original.isdigit():
        if original.endswith("0") and len(original) > 1 and rng.random() < 0.5:
            candidate = original[:-1]
        else:
            candidate = original + "0"
        if candidate != original and candidate.isdigit():
            return candidate

    # random_digit (fallback)
    chars = list(original)
    digit_idxs = [i for i, ch in enumerate(chars) if ch.isdigit()]
    if not digit_idxs:
        return original
    idx = rng.choice(digit_idxs)
    choices = [str(d) for d in range(10) if str(d) != chars[idx]]
    chars[idx] = rng.choice(choices)
    return "".join(chars)


class NumericalInjector(DefectInjector):
    defect_type = DefectType.NUMERICAL

    def inject(self, document: SampleDocument, field_name: str) -> InjectedDefect:
        value = _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        match = re.search(r"\d+(?:\.\d+)?", value)
        if not match:
            raise ValueError(f"Field '{field_name}' does not contain a numeric value")

        original = match.group()
        rng = _rng()
        tampered = _tamper_number(original, rng)
        if tampered == original:
            tampered = _tamper_number(original, random.Random(rng.randint(1, 10_000)))
        if tampered == original:
            # Last resort: force +1 on integer part.
            if "." in original:
                whole, fraction = original.split(".", 1)
                tampered = f"{whole}.{int(fraction) + 1:0{len(fraction)}d}"
            else:
                tampered = str(int(original) + 1)

        new_value = value[: match.start()] + tampered + value[match.end() :]
        new_text = replace_value(field.text, new_value)
        replace_field_text(_working_path(document), field, new_text)
        field.text = new_text
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Tampered numeric value from {original} to {tampered}",
            before=before,
            after=_snapshot(field),
        )


class MetadataInjector(DefectInjector):
    defect_type = DefectType.METADATA

    def inject(self, document: SampleDocument, field_name: str) -> InjectedDefect:
        field = document.fields[field_name]
        before = _snapshot(field)
        path = _working_path(document)
        metadata = dict(read_document_metadata(path))
        rng = _rng()

        # Apply 1–2 metadata anomalies so each inject isn't identical.
        ops = rng.sample(
            ["author", "creator", "title", "subject"],
            k=rng.choice([1, 2]),
        )
        notes: list[str] = []
        if "author" in ops:
            metadata["author"] = rng.choice(
                [a for a in _FAKE_AUTHORS if a != metadata.get("author")] or list(_FAKE_AUTHORS)
            )
            notes.append(f"author={metadata['author']!r}")
        if "creator" in ops:
            metadata["creator"] = rng.choice(_FAKE_CREATORS)
            notes.append(f"creator={metadata['creator']!r}")
        if "title" in ops:
            metadata["title"] = rng.choice(_FAKE_TITLES)
            notes.append(f"title={metadata['title']!r}")
        if "subject" in ops:
            metadata["subject"] = rng.choice(
                ["Edited copy", "Resaved", "Exported scan", ""]
            )
            notes.append(f"subject={metadata['subject']!r}")

        set_document_metadata(path, metadata)
        field.metadata = metadata
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description="Tampered document metadata: " + ", ".join(notes),
            before=before,
            after=_snapshot(field),
        )


def _tamper_word(word: str, rng: random.Random) -> str:
    letters = list(word)
    if not letters:
        return word

    strategies = ["duplicate", "delete", "swap", "substitute", "case_flip"]
    if len(letters) < 2:
        strategies = ["duplicate", "substitute", "case_flip"]

    strategy = rng.choice(strategies)

    if strategy == "duplicate" and len(letters) >= 2:
        idx = rng.randrange(1, len(letters))
        letters.insert(idx, letters[idx])
        return "".join(letters)

    if strategy == "delete" and len(letters) >= 3:
        idx = rng.randrange(1, len(letters) - 1)
        del letters[idx]
        return "".join(letters)

    if strategy == "swap" and len(letters) >= 2:
        idx = rng.randrange(0, len(letters) - 1)
        letters[idx], letters[idx + 1] = letters[idx + 1], letters[idx]
        candidate = "".join(letters)
        if candidate != word:
            return candidate

    if strategy == "substitute":
        candidates = [i for i, ch in enumerate(letters) if ch.lower() in _LETTER_SWAPS]
        if candidates:
            idx = rng.choice(candidates)
            ch = letters[idx]
            repl = _LETTER_SWAPS[ch.lower()]
            letters[idx] = repl.upper() if ch.isupper() else repl
            return "".join(letters)

    if strategy == "case_flip":
        idx = rng.randrange(len(letters))
        ch = letters[idx]
        if ch.isalpha():
            letters[idx] = ch.lower() if ch.isupper() else ch.upper()
            return "".join(letters)

    # Fallback: duplicate first letter.
    if len(word) > 1:
        return word[0] + word[0] + word[1:]
    return word + word


class SpellingInjector(DefectInjector):
    defect_type = DefectType.SPELLING

    def inject(self, document: SampleDocument, field_name: str) -> InjectedDefect:
        value = _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        words = value.split()
        if not words:
            raise ValueError(f"Field '{field_name}' has no text to tamper")

        rng = _rng()
        # Prefer longer alpha words when available — never the leading label.
        alpha_idxs = [
            i
            for i, w in enumerate(words)
            if re.search(r"[A-Za-z]", w) and len(re.sub(r"[^A-Za-z]", "", w)) >= 2
        ]
        idx = rng.choice(alpha_idxs) if alpha_idxs else 0
        original = words[idx]
        words[idx] = _tamper_word(original, rng)
        if words[idx] == original:
            words[idx] = _tamper_word(original, random.Random(rng.randint(1, 10_000)))
        if words[idx] == original and len(original) > 1:
            words[idx] = original[0] + original[1] + original[1] + original[2:]

        new_text = replace_value(field.text, " ".join(words))
        replace_field_text(_working_path(document), field, new_text)
        field.text = new_text
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Introduced spelling anomaly: {original!r} → {words[idx]!r}",
            before=before,
            after=_snapshot(field),
        )


class MisalignmentInjector(DefectInjector):
    defect_type = DefectType.MISALIGNMENT

    def inject(self, document: SampleDocument, field_name: str) -> InjectedDefect:
        _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        rng = _rng()
        axis = rng.choice(["x", "y", "both"])
        magnitude = rng.choice([8.0, 12.0, 15.0, 18.0, 22.0, 28.0])
        sign = rng.choice([-1.0, 1.0])

        new_x = field.x
        new_y = field.y
        notes: list[str] = []
        if axis in {"x", "both"}:
            new_x = field.x + sign * magnitude
            notes.append(f"x {before['x']}→{new_x}")
        if axis in {"y", "both"}:
            y_sign = rng.choice([-1.0, 1.0]) if axis == "both" else sign
            y_mag = magnitude if axis != "both" else rng.choice([6.0, 10.0, 14.0])
            new_y = field.y + y_sign * y_mag
            notes.append(f"y {before['y']}→{new_y}")

        replace_field_text(
            _working_path(document),
            field,
            field.text,
            x=new_x,
            y=new_y,
        )
        field.x = new_x
        field.y = new_y
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description="Shifted field position: " + ", ".join(notes),
            before=before,
            after=_snapshot(field),
        )


class EmphasisInjector(DefectInjector):
    defect_type = DefectType.EMPHASIS

    def inject(self, document: SampleDocument, field_name: str) -> InjectedDefect:
        _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        style = _rng().choice(["bold", "italic", "underline"])
        apply_text_emphasis(_working_path(document), field, style)
        field.style = style
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Applied text emphasis: {style}",
            before=before,
            after=_snapshot(field),
        )


INJECTORS: dict[DefectType, DefectInjector] = {
    DefectType.FONT: FontInjector(),
    DefectType.FONT_SIZE: FontSizeInjector(),
    DefectType.NUMERICAL: NumericalInjector(),
    DefectType.METADATA: MetadataInjector(),
    DefectType.SPELLING: SpellingInjector(),
    DefectType.MISALIGNMENT: MisalignmentInjector(),
    DefectType.EMPHASIS: EmphasisInjector(),
}


def get_injector(defect_type: DefectType) -> DefectInjector:
    return INJECTORS[defect_type]
