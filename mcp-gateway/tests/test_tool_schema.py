"""Tests for tool_schema.to_schema_definition / tool_schema.tool_definitions.

The point of these is the lossy part of the conversion. AgentCore rejects a
SchemaDefinition carrying `enum`, `default`, `minimum`, `maximum` or `title`,
so those are folded into the description instead -- and a silent drop would be
invisible in production until a model invented a value for `source_fields`.
"""

from tool_schema import to_schema_definition, tool_definitions


class TestStructuralFields:
    def test_keeps_the_five_fields_agentcore_accepts(self):
        converted = to_schema_definition(
            {
                "type": "object",
                "description": "a thing",
                "properties": {"a": {"type": "string"}},
                "required": ["a"],
            }
        )
        assert converted == {
            "type": "object",
            "description": "a thing",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }

    def test_drops_everything_else(self):
        converted = to_schema_definition(
            {"type": "string", "title": "Query", "examples": ["x"], "format": "uri"}
        )
        assert converted == {"type": "string"}

    def test_defaults_to_object_when_type_is_absent(self):
        # pydantic emits no `type` for an untyped or union-typed value, and
        # `type` is required by AgentCore.
        assert to_schema_definition({"anyOf": [{"type": "string"}]})["type"] == "object"

    def test_omits_an_empty_required_list(self):
        # `"required": []` is not the same as no required list to every schema
        # validator, and sending one for a tool with no mandatory arguments is
        # noise at best.
        assert "required" not in to_schema_definition({"type": "object", "required": []})

    def test_recurses_into_properties_and_items(self):
        converted = to_schema_definition(
            {
                "type": "object",
                "properties": {
                    "years": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 2000},
                    }
                },
            }
        )
        items = converted["properties"]["years"]["items"]
        assert items["type"] == "integer"
        assert items["description"] == "Must be at least 2000."


class TestConstraintsBecomeProse:
    def test_enum_is_spelled_out(self):
        converted = to_schema_definition(
            {"type": "string", "description": "Which fields.", "enum": ["core", "all"]}
        )
        assert converted["description"] == (
            'Which fields. Allowed values: "core", "all".'
        )

    def test_bounds_collapse_into_one_sentence(self):
        converted = to_schema_definition({"type": "integer", "minimum": 1, "maximum": 25})
        assert converted["description"] == "Must be between 1 and 25 inclusive."

    def test_a_lower_bound_alone(self):
        converted = to_schema_definition({"type": "integer", "minimum": 1})
        assert converted["description"] == "Must be at least 1."

    def test_an_upper_bound_alone(self):
        converted = to_schema_definition({"type": "integer", "maximum": 25})
        assert converted["description"] == "Must be at most 25."

    def test_zero_bounds_are_not_mistaken_for_absent_ones(self):
        # `if minimum:` rather than `if minimum is not None:` would silently
        # drop a floor of 0, which is a plausible bound and a real one to lose.
        converted = to_schema_definition({"type": "integer", "minimum": 0})
        assert converted["description"] == "Must be at least 0."

    def test_default_is_stated(self):
        converted = to_schema_definition({"type": "integer", "default": 10})
        assert converted["description"] == "Defaults to 10 if omitted."

    def test_a_false_default_is_still_stated(self):
        converted = to_schema_definition({"type": "boolean", "default": False})
        assert converted["description"] == "Defaults to false if omitted."

    def test_sentences_are_appended_in_order_after_the_description(self):
        converted = to_schema_definition(
            {
                "type": "string",
                "description": "Which fields.  ",
                "enum": ["core", "all"],
                "default": "core",
            }
        )
        assert converted["description"] == (
            'Which fields. Allowed values: "core", "all". '
            'Defaults to "core" if omitted.'
        )

    def test_no_description_key_when_there_is_nothing_to_say(self):
        assert to_schema_definition({"type": "string"}) == {"type": "string"}


class TestToolDefinitions:
    def test_shape_matches_what_creategatewaytarget_requires(self):
        class FakeTool:
            name = "search_energy_knowledge"
            description = "Search things."
            input_schema = {
                "type": "object",
                "properties": {"query": {"type": "string", "title": "Query"}},
                "required": ["query"],
            }

        assert tool_definitions([FakeTool()]) == [
            {
                "name": "search_energy_knowledge",
                "description": "Search things.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ]
