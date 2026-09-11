"""Tests for the AgentCore Gateway Lambda entrypoint.

The handler's whole job is the contract between the gateway and the MCP server:
read the tool name out of the client context, dispatch through `call_tool`, and
turn a failure into a raised exception rather than a successful-looking result.
All three are things nothing else in the suite covers and that only fail in
production, where the feedback is an opaque 500.
"""

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

import lambda_handler
from lambda_handler import ToolInvocationError, handler


class FakeClientContext:
    def __init__(self, custom):
        self.custom = custom


class FakeContext:
    def __init__(self, tool_name=None, custom=None):
        if custom is None and tool_name is not None:
            custom = {"bedrockAgentCoreToolName": tool_name}
        self.client_context = FakeClientContext(custom) if custom is not None else None


class FakeBlock:
    def __init__(self, text):
        self.text = text


class FakeResult:
    def __init__(self, structured_content=None, content=(), is_error=False):
        self.structured_content = structured_content
        self.content = list(content)
        self.is_error = is_error


class FakeMcp:
    """Stands in for the real MCPServer: records the call, returns what it is told."""

    def __init__(self):
        self.calls = []
        self.result = FakeResult(structured_content={"ok": True})
        self.raises = None

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.raises is not None:
            raise self.raises
        return self.result


@pytest.fixture
def mcp(monkeypatch):
    """Replace the MCP server with a recorder, so no tool ever really runs."""
    fake = FakeMcp()
    monkeypatch.setattr(lambda_handler, "mcp", fake)
    return fake


class TestToolName:
    def test_strips_the_target_prefix(self, mcp):
        handler({}, FakeContext("bfe-energy___search_energy_knowledge"))
        assert mcp.calls[0][0] == "search_energy_knowledge"

    def test_only_the_first_delimiter_separates(self, mcp):
        # A tool is free to contain the delimiter; split() would truncate it.
        handler({}, FakeContext("bfe-energy___odd___name"))
        assert mcp.calls[0][0] == "odd___name"

    def test_an_unprefixed_name_is_used_as_is(self, mcp):
        handler({}, FakeContext("search_energy_knowledge"))
        assert mcp.calls[0][0] == "search_energy_knowledge"

    def test_a_direct_invocation_is_refused(self, mcp):
        # No client context at all: someone invoked the function by hand and
        # would otherwise get a confusing failure from deep inside call_tool.
        with pytest.raises(ToolInvocationError, match="bedrockAgentCoreToolName"):
            handler({}, FakeContext())
        assert mcp.calls == []

    def test_a_context_without_the_key_is_refused(self, mcp):
        with pytest.raises(ToolInvocationError, match="bedrockAgentCoreToolName"):
            handler({}, FakeContext(custom={"bedrockAgentCoreGatewayId": "g"}))


class TestArguments:
    def test_the_event_is_passed_through_as_the_arguments(self, mcp):
        handler({"query": "Wasserkraft", "max_results": 1}, FakeContext("t___x"))
        assert mcp.calls[0][1] == {"query": "Wasserkraft", "max_results": 1}

    def test_a_non_dict_event_becomes_no_arguments(self, mcp):
        # Lambda will hand over whatever it was given. An empty argument map
        # produces a validation error naming the missing field, which is a far
        # better message than an AttributeError from inside the SDK.
        handler([], FakeContext("t___x"))
        assert mcp.calls[0][1] == {}


class TestResult:
    def test_structured_content_is_preferred(self, mcp):
        mcp.result = FakeResult(
            structured_content={"result_count": 1},
            content=[FakeBlock('{"result_count": 1}')],
        )
        assert handler({}, FakeContext("t___x")) == {"result_count": 1}

    def test_falls_back_to_joined_text_blocks(self, mcp):
        mcp.result = FakeResult(content=[FakeBlock("one"), FakeBlock("two")])
        assert handler({}, FakeContext("t___x")) == "one\ntwo"

    def test_non_text_blocks_are_skipped(self, mcp):
        mcp.result = FakeResult(content=[object(), FakeBlock("one")])
        assert handler({}, FakeContext("t___x")) == "one"

    # Every real tool here lands on this path: they all return dicts, none
    # declares an output schema, so the SDK renders the dict as JSON text and
    # leaves structured_content unset. Returning that text verbatim would hand
    # the gateway a string to serialise a second time.
    def test_json_object_text_is_unwrapped(self, mcp):
        mcp.result = FakeResult(content=[FakeBlock('{"result_count": 2}')])
        assert handler({}, FakeContext("t___x")) == {"result_count": 2}

    def test_json_array_text_is_unwrapped(self, mcp):
        mcp.result = FakeResult(content=[FakeBlock('[1, 2]')])
        assert handler({}, FakeContext("t___x")) == [1, 2]

    def test_prose_is_left_alone(self, mcp):
        mcp.result = FakeResult(content=[FakeBlock("not json at all")])
        assert handler({}, FakeContext("t___x")) == "not json at all"

    # The trap in unwrapping: bare json.loads accepts scalars, so a tool
    # returning the string "42" would come back as the integer 42 and a tool
    # returning "null" as None. Only containers are a rendered return value.
    @pytest.mark.parametrize("text", ["42", "null", "true", '"quoted"'])
    def test_json_scalars_stay_text(self, mcp, text):
        mcp.result = FakeResult(content=[FakeBlock(text)])
        assert handler({}, FakeContext("t___x")) == text


class TestFailures:
    def test_a_tool_error_is_raised_not_returned(self, mcp):
        # Returning an error-shaped dict would look like success to the
        # gateway, which would hand it to the model as a normal result.
        mcp.raises = ToolError("no such tool")
        with pytest.raises(ToolInvocationError, match="no such tool"):
            handler({}, FakeContext("t___x"))

    def test_a_crash_is_reported_without_its_internals(self, mcp):
        mcp.raises = UnexpectedToolError("boom")
        with pytest.raises(ToolInvocationError, match="tool x failed"):
            handler({}, FakeContext("t___x"))

    def test_an_is_error_result_is_raised_too(self, mcp):
        mcp.result = FakeResult(
            content=[FakeBlock("knowledge base unavailable")], is_error=True
        )
        with pytest.raises(ToolInvocationError, match="knowledge base unavailable"):
            handler({}, FakeContext("t___x"))
