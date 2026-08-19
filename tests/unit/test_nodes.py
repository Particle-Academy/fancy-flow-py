"""The built-in executors, and the capability seams they refuse to guess at."""

from __future__ import annotations

import pytest

from fancy_flow import (
    ExecutionContext,
    ExecutorRegistry,
    FlowGraph,
    FlowNode,
    FlowRunner,
    NodeKindRegistry,
    RunOptions,
    builtin,
    capabilities,
    import_workflow,
)
from fancy_flow.exceptions import FlowError
from fancy_flow.nodes import ai, data, logic
from fancy_flow.nodes.support import structured
from fancy_flow.nodes.support.clients import DictStore


def ctx(config=None, inputs=None, depth: int = 0) -> ExecutionContext:
    return ExecutionContext(
        FlowNode("n", "k", config=config or {}), inputs or {}, lambda e: None, depth
    )


@pytest.fixture(autouse=True)
def _clean_capabilities():
    capabilities.reset()
    yield
    capabilities.reset()


# -- logic ---------------------------------------------------------------


def test_merge_keys_a_scalar_by_its_port_and_spreads_a_mapping() -> None:
    result = logic.merge(ctx({"mode": "merge"}, {"a": {"x": 1}, "b": "scalar"}))
    assert result == {"x": 1, "b": "scalar"}


def test_merge_skips_none_inputs() -> None:
    assert logic.merge(ctx({}, {"a": {"x": 1}, "b": None})) == {"x": 1}


def test_concat_spreads_lists_and_appends_scalars() -> None:
    assert logic.merge(ctx({"mode": "concat"}, {"a": [1, 2], "b": 3, "c": None})) == [1, 2, 3]


def test_for_each_publishes_the_collection_not_one_job_per_item() -> None:
    """Fan-out as data. A `for_each` over 10,000 rows is one node, one claim,
    one checkpoint -- not 10,000."""
    result = logic.for_each(ctx({"source": "{{ $json.users }}"}, {"in": {"users": ["a", "b"]}}))
    assert result == {"items": ["a", "b"], "count": 2}


def test_for_each_takes_the_values_of_a_mapping() -> None:
    result = logic.for_each(ctx({"source": "{{ $json.rows }}"}, {"in": {"rows": {"k": 1}}}))
    assert result == {"items": [1], "count": 1}


def test_switch_falls_through_to_default_for_an_unmapped_key() -> None:
    result = logic.switch_case(
        ctx({"value": "{{ $json.kind }}", "cases": {"a": "case_a"}}, {"in": {"kind": "zzz"}})
    )
    assert result["__port"] == "default"


def test_transform_without_an_expression_passes_through() -> None:
    assert logic.transform(ctx({}, {"in": {"x": 1}})) == {"x": 1}


# -- data ----------------------------------------------------------------


def test_the_data_store_namespaces_keys_by_table() -> None:
    store = DictStore()
    node = data.DataStore(store)

    node.execute(
        ctx(
            {"operation": "set", "table": "users", "key": "1", "value": "{{ $json }}"},
            {"in": {"name": "A"}},
        )
    )
    assert store.all() == {"users/1": {"name": "A"}}

    assert node.execute(ctx({"operation": "list", "table": "users"})) == {"1": {"name": "A"}}
    assert node.execute(ctx({"operation": "list", "table": "other"})) == {}


def test_a_query_filters_by_the_where_map() -> None:
    store = DictStore({"t/1": {"tag": "x"}, "t/2": {"tag": "y"}})
    node = data.DataStore(store)
    assert node.execute(ctx({"operation": "query", "table": "t", "where": {"tag": "y"}})) == [
        {"tag": "y"}
    ]


def test_memory_append_starts_a_list_and_grows_it() -> None:
    store = DictStore()
    node = data.MemoryStore(store)
    node.execute(ctx({"operation": "append", "key": "log", "value": "one"}))
    result = node.execute(ctx({"operation": "append", "key": "log", "value": "two"}))
    assert result == ["one", "two"]


# -- ai ------------------------------------------------------------------


def test_llm_router_aborts_rather_than_guessing_a_branch() -> None:
    """A silent default would look like the model made a choice."""
    node = ai.LlmRouter()
    result = FlowRunner().run(
        FlowGraph(nodes=(FlowNode("r", "llm_router", config={"routes": [{"port": "a"}]}),)),
        ExecutorRegistry().bind("llm_router", node),
    )
    assert result.ok is False
    assert "set_llm_client" in (result.error or "")


def test_llm_router_refuses_a_port_the_model_invented() -> None:
    """Emitting on a port with no edge silently ends the branch -- the worst
    failure mode in a workflow engine, because the run reports SUCCESS having
    done nothing."""

    class Inventive:
        def choose_route(self, request):  # type: ignore[no-untyped-def]
            return capabilities.LlmRouteChoice("port-that-does-not-exist")

    capabilities.set_llm_client(Inventive())
    result = ai.LlmRouter().execute(ctx({"routes": [{"port": "a"}, {"port": "b"}], "prompt": "hi"}))

    assert result["__port"] == "fallback"
    assert "unrecognised route" in result["value"]["reason"]


def test_llm_router_lands_on_a_real_route_when_the_fallback_port_is_off() -> None:
    class Inventive:
        def choose_route(self, request):  # type: ignore[no-untyped-def]
            return capabilities.LlmRouteChoice("nope")

    capabilities.set_llm_client(Inventive())
    result = ai.LlmRouter().execute(
        ctx({"routes": [{"port": "a"}, {"port": "b"}], "fallback": False, "prompt": "hi"})
    )
    assert result["__port"] == "a"


def test_llm_router_ports_drop_blanks_and_duplicates() -> None:
    ports = [
        p.id
        for p in ai.LlmRouter.ports(
            {
                "routes": [
                    {"port": "a"},
                    {"port": ""},
                    {"port": "a"},
                    {"port": "b"},
                ]
            }
        )
    ]
    assert ports == ["a", "b", "fallback"]


def test_llm_call_validates_data_the_adapter_claims_to_have_parsed() -> None:
    """ "The provider promised" is not the same as "the provider did"."""

    class Lying:
        def complete(self, prompt, options=None):  # type: ignore[no-untyped-def]
            return {"text": "{}", "data": {"title": 7}}

    node = ai.LlmCall(Lying())
    with pytest.raises(FlowError, match="did not match the requested schema"):
        node.execute(
            ctx(
                {
                    "prompt": "x",
                    "response_schema": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                    },
                }
            )
        )


def test_llm_call_parses_text_when_the_adapter_ignores_the_schema() -> None:
    """Without this, a downstream `{{ $json.data.title }}` gets nothing and the
    run reports success."""

    class Prosaic:
        def complete(self, prompt, options=None):  # type: ignore[no-untyped-def]
            return {"text": 'Here you go:\n```json\n{"title": "ok"}\n```'}

    result = ai.LlmCall(Prosaic()).execute(
        ctx({"prompt": "x", "response_schema": {"type": "object"}})
    )
    assert result["data"] == {"title": "ok"}


def test_a_broken_response_schema_is_reported_not_ignored() -> None:
    with pytest.raises(FlowError, match="not valid JSON"):
        ai.LlmCall(None).execute(ctx({"prompt": "x", "response_schema": "{not json"}))  # type: ignore[arg-type]


# -- structured output ---------------------------------------------------


def test_truncation_raises_instead_of_decoding_to_nothing() -> None:
    """A truncated array is indistinguishable from a short one once it fails to
    parse, and a workflow that silently processes zero records is the expensive
    kind of wrong."""
    with pytest.raises(FlowError):
        structured.extract('Here are the results: [{"a": 1}, {"b":')


def test_an_empty_response_raises() -> None:
    with pytest.raises(FlowError):
        structured.extract("   ")


def test_a_preamble_is_recovered_from() -> None:
    assert structured.extract('Sure! {"a": 1} hope that helps') == {"a": 1}


def test_a_brace_inside_a_string_does_not_confuse_the_scan() -> None:
    assert structured.extract('note: {"a": "{ not a brace }"} done') == {"a": "{ not a brace }"}


def test_an_integer_satisfies_a_number_schema() -> None:
    """JSON has one number type; rejecting `3` for `{"type":"number"}` is a
    trap every author hits."""
    assert structured.validate(3, {"type": "number"}) == []


def test_a_boolean_does_not_satisfy_an_integer_schema() -> None:
    """`True` is an `int` in Python, and the peers compare with `===`."""
    assert structured.validate(True, {"type": "integer"}) != []


def test_nested_required_and_items_are_checked() -> None:
    schema = {
        "type": "object",
        "required": ["rows"],
        "properties": {"rows": {"type": "array", "items": {"type": "object", "required": ["id"]}}},
    }
    assert structured.validate({"rows": [{"id": 1}, {}]}, schema) == [
        "$.rows[1].id is required but missing"
    ]


# -- structural ----------------------------------------------------------


def test_subflow_aborts_when_no_resolver_is_registered() -> None:
    result = FlowRunner().run(
        FlowGraph(nodes=(FlowNode("s", "subflow", config={"workflow": "child"}),)),
        builtin.executors(),
    )
    assert result.ok is False
    assert "set_workflow_resolver" in (result.error or "")


def test_subflow_names_the_workflow_that_referenced_itself() -> None:
    """A bare RecursionError tells an author nothing about the graph they wired
    into a loop."""
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    child = import_workflow(
        {
            "version": 1,
            "graph": {
                "nodes": [
                    {
                        "id": "s",
                        "kind": "subflow",
                        "position": {"x": 0, "y": 0},
                        "config": {"workflow": "loop", "maxDepth": 3},
                    }
                ],
                "edges": [],
            },
        },
        registry=registry,
    ).graph

    class SelfReferential:
        def resolve(self, ref, version=None):  # type: ignore[no-untyped-def]
            return child

    capabilities.set_workflow_resolver(SelfReferential())
    result = FlowRunner().run(child, builtin.executors())

    assert result.ok is False
    assert "depth limit reached" in (result.error or "")
    assert '"loop"' in (result.error or "")


def test_a_version_mismatch_is_not_reported_as_missing() -> None:
    """The two want different errors: "not found" sends an author looking for a
    workflow that is sitting right there."""
    from fancy_flow.capabilities import WorkflowResolutionFailure

    class Pinned:
        def resolve(self, ref, version=None):  # type: ignore[no-untyped-def]
            return WorkflowResolutionFailure(
                WorkflowResolutionFailure.VERSION_MISMATCH, available=4
            )

    capabilities.set_workflow_resolver(Pinned())
    result = FlowRunner().run(
        FlowGraph(nodes=(FlowNode("s", "subflow", config={"workflow": "child", "version": 2}),)),
        builtin.executors(),
    )

    assert "pinned to version 2" in (result.error or "")
    assert "host has 4" in (result.error or "")


def test_subgraph_runs_a_nested_workflow_and_returns_its_outputs() -> None:
    nested = {
        "version": 1,
        "graph": {
            "nodes": [
                {
                    "id": "itf",
                    "kind": "transform",
                    "position": {"x": 0, "y": 0},
                    "config": {"expression": "{{ $json.n }}"},
                },
            ],
            "edges": [],
        },
    }
    registry = builtin.register(NodeKindRegistry(), with_structural=True)
    graph = import_workflow(
        {
            "version": 1,
            "graph": {
                "nodes": [
                    {
                        "id": "sg",
                        "kind": "subgraph",
                        "position": {"x": 0, "y": 0},
                        "config": {"graph": nested},
                    }
                ],
                "edges": [],
            },
        },
        registry=registry,
    ).graph

    result = FlowRunner().run(
        graph, builtin.executors(), options=RunOptions(initial_inputs={"sg": {"n": 99}})
    )
    assert result.outputs["sg"] == {"itf": 99}


# -- capabilities --------------------------------------------------------


def test_status_reports_what_a_graph_would_be_missing() -> None:
    """So a host -- or an agent over MCP -- can answer "what have I not wired?"
    BEFORE a run fails halfway through."""
    assert capabilities.status() == {"llm": False, "workflow_resolver": False}
    capabilities.set_llm_client(object())  # type: ignore[arg-type]
    assert capabilities.status()["llm"] is True


def test_unregistering_only_removes_your_own_client() -> None:
    first = object()
    second = object()
    undo_first = capabilities.set_llm_client(first)  # type: ignore[arg-type]
    capabilities.set_llm_client(second)  # type: ignore[arg-type]

    undo_first()
    assert capabilities.llm_client() is second
