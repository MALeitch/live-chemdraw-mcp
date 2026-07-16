import pytest

from tools._common import parse_json_arg, parse_optional_json_arg


def test_parse_json_arg_list_ok():
    assert parse_json_arg('["a", "b"]', "x_json", list) == ["a", "b"]


def test_parse_json_arg_dict_ok():
    assert parse_json_arg('{"box_index": 1}', "x_json", dict) == {"box_index": 1}


def test_parse_json_arg_wrong_type_raises_clear_error():
    # Valid JSON, wrong shape (an int where a list was expected) -- must be
    # rejected here, at the tool boundary, not fail later deep in bridge.py.
    with pytest.raises(ValueError, match="rotate_ids_json must be a JSON list"):
        parse_json_arg("123", "rotate_ids_json", list)


def test_parse_json_arg_invalid_json_raises_clear_error():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_json_arg("{not json", "region_json", dict)


def test_parse_optional_json_arg_blank_is_none():
    assert parse_optional_json_arg("", "x_json", list) is None
    assert parse_optional_json_arg("   ", "x_json", list) is None


def test_parse_optional_json_arg_present_is_validated():
    assert parse_optional_json_arg('["a"]', "x_json", list) == ["a"]
    with pytest.raises(ValueError):
        parse_optional_json_arg('{"a": 1}', "x_json", list)
