import pytest

from incident_rca_agent.schema import validate

QUOTED = {
    "type": "object",
    "properties": {"value": {"type": ["string", "null"]}, "quote": {"type": ["string", "null"]}},
    "required": ["value", "quote"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "iban": QUOTED,
        "currency": {"type": ["string", "null"], "enum": ["EUR", "GBP", None]},
        "claims": {
            "type": "array",
            "items": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
        "steps": {"type": "integer"},
        "confident": {"type": "boolean"},
        "score": {"type": "number"},
    },
    "required": ["iban"],
    "additionalProperties": False,
}


def test_valid_data_is_returned_unchanged():
    data = {
        "iban": {"value": "DE89370400440532013000", "quote": "DE89 3704 0044 0532 0130 00"},
        "currency": "EUR",
        "claims": [{"text": "a"}],
        "steps": 3,
        "confident": True,
        "score": 0.5,
    }
    assert validate(data, SCHEMA) is data


def test_missing_required_key_names_the_path():
    with pytest.raises(ValueError, match=r"iban\.quote: missing"):
        validate({"iban": {"value": "x"}}, SCHEMA)


def test_wrong_type_names_the_path_and_the_expectation():
    with pytest.raises(ValueError, match=r"iban\.value: expected string or null, got 12"):
        validate({"iban": {"value": 12, "quote": None}}, SCHEMA)


def test_value_outside_the_enum_is_rejected():
    with pytest.raises(ValueError, match=r"currency: expected one of \['EUR', 'GBP', None\], got 'USD'"):
        validate({"iban": {"value": None, "quote": None}, "currency": "USD"}, SCHEMA)


def test_unexpected_key_is_rejected():
    with pytest.raises(ValueError, match="unexpected key 'bic'"):
        validate({"iban": {"value": None, "quote": None}, "bic": "x"}, SCHEMA)


def test_array_items_report_their_index():
    with pytest.raises(ValueError, match=r"claims\[1\]\.text: missing"):
        validate({"iban": {"value": None, "quote": None}, "claims": [{"text": "a"}, {}]}, SCHEMA)


def test_true_is_not_an_integer():
    with pytest.raises(ValueError, match="steps: expected integer, got True"):
        validate({"iban": {"value": None, "quote": None}, "steps": True}, SCHEMA)


def test_integer_counts_as_a_number():
    assert validate({"iban": {"value": None, "quote": None}, "score": 1}, SCHEMA)


def test_top_level_must_be_an_object():
    with pytest.raises(ValueError, match="expected object"):
        validate(["not", "an", "object"], SCHEMA)
