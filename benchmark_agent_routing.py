import os
import csv
import json
import time
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# ============================================================
# IMPORT THE WORKING AGENT
# ============================================================
# GOAL
# ------------------------------------------------------------
# Reuse the exact same router, authentication, MCP discovery,
# MCP calling and repair logic as agent_app.py.
#
# WHY
# ------------------------------------------------------------
# This prevents the benchmark from accidentally testing a
# different implementation than the real agent.
# ============================================================

import agent_app


# ============================================================
# 1. LOAD BENCHMARK CONFIGURATION
# ============================================================
# INPUT
# ------------------------------------------------------------
# Optional .env variables:
#
# ROUTING_BENCHMARK_LIMIT=0
#   0 = run all benchmark questions
#   N = run only the first N questions
#
# CALL_MCP_DURING_ROUTING_BENCHMARK=true
#   true  = also execute the selected MCP tool
#   false = test only routing + argument schema
#
# OUTPUT
# ------------------------------------------------------------
# Configuration values used by this benchmark.
# ============================================================

load_dotenv(override=True)

BENCHMARK_LIMIT = int(
    os.getenv(
        "ROUTING_BENCHMARK_LIMIT",
        "0",
    )
)

CALL_MCP = os.getenv(
    "CALL_MCP_DURING_ROUTING_BENCHMARK",
    "true",
).strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
}

RESULTS_DIR = Path("benchmark_results")

DETAILS_PATH = RESULTS_DIR / "agent_routing_details.json"

SCORES_PATH = RESULTS_DIR / "agent_routing_scores.csv"

SUMMARY_PATH = RESULTS_DIR / "agent_routing_summary.csv"


# ============================================================
# 2. CURRENT MCP TOOL NAMES
# ============================================================
# These are the three tools provided by the challenge gateway.
#
# The benchmark still discovers the live tools dynamically.
# These constants are used only for the EXPECTED routing labels.
# ============================================================

SEARCH_TOOL = "bfe-energy___search_energy_knowledge"

TIMELINE_TOOL = "bfe-energy___get_metric_timeline"

CHART_TOOL = "bfe-energy___get_chart_data"


# ============================================================
# 3. ROUTING BENCHMARK QUESTIONS
# ============================================================
# GOAL
# ------------------------------------------------------------
# Test clear examples of the three routing intents:
#
# SEARCH
#   qualitative / policy / explanatory questions
#
# TIMELINE
#   annual development / trend / multi-year values
#
# CHART
#   values explicitly requested from a chart or table
#
# DESIGN
# ------------------------------------------------------------
# - 21 questions total
# - 7 per tool
# - English, German and French
# - includes a few routing stress cases
#
# IMPORTANT
# ------------------------------------------------------------
# The expected tool is defined by OUR agent design and the
# challenge-owner tool descriptions. It is not an answer-key
# supplied by SFOE.
# ============================================================

ROUTING_QUESTIONS: List[Dict[str, Any]] = [
    # --------------------------------------------------------
    # GENERAL SEMANTIC SEARCH: 7 questions
    # --------------------------------------------------------
    {
        "id": "SEARCH_01",
        "language": "en",
        "category": "general",
        "question": "What role does hydropower play in Switzerland?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "SEARCH_02",
        "language": "en",
        "category": "policy",
        "question": "What does SFOE say about hydrogen in Switzerland's energy system?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "SEARCH_03",
        "language": "de",
        "category": "general",
        "question": "Welche Bedeutung hat Photovoltaik für die Schweizer Energieversorgung?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "SEARCH_04",
        "language": "de",
        "category": "general",
        "question": "Welche Rolle spielt Wasserkraft in der Schweizer Stromversorgung?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "SEARCH_05",
        "language": "fr",
        "category": "general",
        "question": "Quel rôle joue l'hydroélectricité dans l'approvisionnement électrique suisse ?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "SEARCH_06",
        "language": "fr",
        "category": "policy",
        "question": "Quels sont les objectifs suisses en matière d'énergie renouvelable ?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "SEARCH_07",
        "language": "de",
        "category": "routing_stress_policy_years",
        "question": "Welche Ziele nennt das BFE für die Stromproduktion aus erneuerbaren Energien bis 2035 und 2050?",
        "expected_tool": SEARCH_TOOL,
    },
    # --------------------------------------------------------
    # METRIC TIMELINE: 7 questions
    # --------------------------------------------------------
    {
        "id": "TIMELINE_01",
        "language": "en",
        "category": "historical_trend",
        "question": "How has photovoltaic production developed from 2020 to 2024?",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "TIMELINE_02",
        "language": "en",
        "category": "historical_trend",
        "question": "How did renewable electricity production change from 2019 to 2023?",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "TIMELINE_03",
        "language": "de",
        "category": "historical_trend",
        "question": "Wie hat sich die Photovoltaikproduktion von 2020 bis 2024 entwickelt?",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "TIMELINE_04",
        "language": "de",
        "category": "annual_values",
        "question": "Zeige die jährliche Entwicklung der Solarstromproduktion von 2021 bis 2024.",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "TIMELINE_05",
        "language": "fr",
        "category": "historical_trend",
        "question": "Comment la production photovoltaïque a-t-elle évolué entre 2020 et 2024 ?",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "TIMELINE_06",
        "language": "fr",
        "category": "annual_values",
        "question": "Donne l'évolution annuelle de la production d'électricité solaire de 2020 à 2024.",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "TIMELINE_07",
        "language": "en",
        "category": "routing_stress_annual",
        "question": "Give me the yearly values for solar electricity production between 2020 and 2024.",
        "expected_tool": TIMELINE_TOOL,
    },
    # --------------------------------------------------------
    # CHART / TABLE EXTRACTION: 7 questions
    # --------------------------------------------------------
    {
        "id": "CHART_01",
        "language": "en",
        "category": "chart",
        "question": "What values are shown in the SFOE chart for renewable electricity production?",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "CHART_02",
        "language": "en",
        "category": "chart",
        "question": "Extract the numeric values from the SFOE chart about solar electricity production.",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "CHART_03",
        "language": "de",
        "category": "chart",
        "question": "Welche Werte zeigt das BFE-Diagramm zur erneuerbaren Stromproduktion?",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "CHART_04",
        "language": "de",
        "category": "table",
        "question": "Extrahiere die Zahlen aus der BFE-Tabelle zur Photovoltaikproduktion.",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "CHART_05",
        "language": "fr",
        "category": "chart",
        "question": "Quelles valeurs sont indiquées dans le graphique de l'OFEN sur la production d'électricité renouvelable ?",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "CHART_06",
        "language": "fr",
        "category": "table",
        "question": "Extrais les valeurs numériques du tableau de l'OFEN sur la production photovoltaïque.",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "CHART_07",
        "language": "en",
        "category": "routing_stress_chart_year",
        "question": "According to the SFOE chart, what value is shown for renewable electricity production in 2024?",
        "expected_tool": CHART_TOOL,
    },
]


# ============================================================
# 4. HELPER: SHORT TOOL LABEL
# ============================================================


def short_tool_name(
    tool_name: Optional[str],
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Make terminal output easier to read.
    """

    if tool_name == SEARCH_TOOL:
        return "search"

    if tool_name == TIMELINE_TOOL:
        return "timeline"

    if tool_name == CHART_TOOL:
        return "chart"

    return str(tool_name or "UNKNOWN")


# ============================================================
# 5. FIND ONE LIVE TOOL DEFINITION
# ============================================================


def find_tool_definition(
    tool_name: str,
    tools: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    GOAL
    ----------------------------------------------------------
    Find the live tools/list definition for one tool.

    INPUT
    ----------------------------------------------------------
    Tool name + dynamically discovered MCP tools.

    OUTPUT
    ----------------------------------------------------------
    Tool definition dictionary or None.
    """

    for tool in tools:
        if tool.get("name") == tool_name:
            return tool

    return None


# ============================================================
# 6. JSON-SCHEMA-LIKE TYPE CHECKING
# ============================================================


def value_matches_type(
    value: Any,
    expected_type: str,
) -> bool:
    """
    GOAL
    ----------------------------------------------------------
    Perform simple type checks for the common JSON Schema types
    used by MCP tool input schemas.

    INPUT
    ----------------------------------------------------------
    Python value + JSON schema type.

    OUTPUT
    ----------------------------------------------------------
    True / False.

    NOTE
    ----------------------------------------------------------
    This is intentionally lightweight and avoids adding a new
    jsonschema package dependency.
    """

    if expected_type == "string":
        return isinstance(
            value,
            str,
        )

    if expected_type == "integer":
        return isinstance(
            value,
            int,
        ) and not isinstance(
            value,
            bool,
        )

    if expected_type == "number":
        return isinstance(
            value,
            (
                int,
                float,
            ),
        ) and not isinstance(
            value,
            bool,
        )

    if expected_type == "boolean":
        return isinstance(
            value,
            bool,
        )

    if expected_type == "array":
        return isinstance(
            value,
            list,
        )

    if expected_type == "object":
        return isinstance(
            value,
            dict,
        )

    if expected_type == "null":
        return value is None

    # Unknown type -> do not fail solely because our lightweight
    # validator does not implement it.
    return True


# ============================================================
# 7. VALIDATE ROUTER ARGUMENTS AGAINST LIVE TOOL SCHEMA
# ============================================================


def validate_arguments_against_schema(
    tool_name: str,
    arguments: Dict[str, Any],
    tools: List[Dict[str, Any]],
) -> Tuple[
    bool,
    List[str],
]:
    """
    GOAL
    ----------------------------------------------------------
    Check whether router-generated arguments agree with the
    dynamically discovered MCP input schema.

    INPUT
    ----------------------------------------------------------
    Selected tool name
    Router-generated arguments
    Live tools/list response

    OUTPUT
    ----------------------------------------------------------
    (
        is_valid,
        list_of_validation_issues
    )

    CHECKS
    ----------------------------------------------------------
    - required fields
    - unknown fields when additionalProperties=false
    - simple JSON types
    - enum values
    """

    issues = []

    tool = find_tool_definition(
        tool_name,
        tools,
    )

    if tool is None:
        return (
            False,
            ["Selected tool is not present in live tools/list."],
        )

    schema = tool.get("inputSchema") or {}

    properties = schema.get("properties") or {}

    required = schema.get("required") or []

    if not isinstance(
        arguments,
        dict,
    ):
        return (
            False,
            ["Arguments are not a JSON object."],
        )

    # --------------------------------------------------------
    # Required fields
    # --------------------------------------------------------

    for field in required:
        if field not in arguments:
            issues.append(f"Missing required field: {field}")

    # --------------------------------------------------------
    # Unexpected fields
    # --------------------------------------------------------

    if schema.get("additionalProperties") is False:
        for field in arguments:
            if field not in properties:
                issues.append(f"Unexpected field: {field}")

    # --------------------------------------------------------
    # Type / enum checks
    # --------------------------------------------------------

    for (
        field,
        value,
    ) in arguments.items():
        field_schema = properties.get(field)

        if not isinstance(
            field_schema,
            dict,
        ):
            continue

        expected_type = field_schema.get("type")

        if isinstance(
            expected_type,
            str,
        ):
            if not value_matches_type(
                value,
                expected_type,
            ):
                issues.append(
                    (
                        f"Wrong type for '{field}': "
                        f"expected {expected_type}, "
                        f"got {type(value).__name__}"
                    )
                )

        elif isinstance(
            expected_type,
            list,
        ):
            if not any(
                value_matches_type(
                    value,
                    one_type,
                )
                for one_type in expected_type
                if isinstance(
                    one_type,
                    str,
                )
            ):
                issues.append(
                    (
                        f"Wrong type for '{field}': "
                        f"expected one of {expected_type}, "
                        f"got {type(value).__name__}"
                    )
                )

        enum_values = field_schema.get("enum")

        if (
            isinstance(
                enum_values,
                list,
            )
            and value not in enum_values
        ):
            issues.append(
                (f"Invalid enum value for '{field}': {value!r}; allowed={enum_values}")
            )

    return (
        len(issues) == 0,
        issues,
    )


# ============================================================
# 8. RUN ONE ROUTING TEST
# ============================================================


def evaluate_one_question(
    test_case: Dict[str, Any],
    tools: List[Dict[str, Any]],
    access_token: Optional[str],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Evaluate the routing behavior for one question.

    INPUT
    ----------------------------------------------------------
    One benchmark question
    Live MCP tools
    Optional auth token

    OUTPUT
    ----------------------------------------------------------
    Detailed dictionary containing:
    - selected tool
    - expected tool
    - routing correctness
    - arguments
    - schema validity
    - MCP success
    - repair information
    - latencies
    """

    question = test_case["question"]

    expected_tool = test_case["expected_tool"]

    record: Dict[str, Any] = {
        "id": test_case["id"],
        "language": test_case["language"],
        "category": test_case["category"],
        "question": question,
        "expected_tool": expected_tool,
        "selected_tool": None,
        "routing_correct": False,
        "arguments": None,
        "argument_schema_valid": False,
        "argument_validation_issues": [],
        "routing_reason": None,
        "router_latency_seconds": None,
        "initial_mcp_success": None,
        "repair_attempted": False,
        "repair_success": False,
        "final_mcp_success": None,
        "mcp_latency_seconds": None,
        "error": None,
    }

    # --------------------------------------------------------
    # STEP 1: Ask the same production model/router used by
    # agent_app.py to select a tool and generate arguments.
    # --------------------------------------------------------

    router_start = time.perf_counter()

    try:
        selection = agent_app.choose_tool(
            question=question,
            tools=tools,
        )

    except Exception as error:
        record["router_latency_seconds"] = round(
            time.perf_counter() - router_start,
            4,
        )

        record["error"] = "Router failure: " + str(error)

        return record

    router_latency = time.perf_counter() - router_start

    selected_tool = selection.get("tool_name")

    arguments = selection.get(
        "arguments",
        {},
    )

    record["selected_tool"] = selected_tool

    record["arguments"] = arguments

    record["routing_reason"] = selection.get("reason")

    record["router_latency_seconds"] = round(
        router_latency,
        4,
    )

    record["routing_correct"] = selected_tool == expected_tool

    # --------------------------------------------------------
    # STEP 2: Validate arguments against the ACTUAL schema
    # returned by the gateway's tools/list.
    # --------------------------------------------------------

    (
        args_valid,
        args_issues,
    ) = validate_arguments_against_schema(
        tool_name=selected_tool,
        arguments=arguments,
        tools=tools,
    )

    record["argument_schema_valid"] = args_valid

    record["argument_validation_issues"] = args_issues

    # --------------------------------------------------------
    # STEP 3: Optionally execute the selected MCP tool.
    #
    # This tests whether the generated arguments really work,
    # not merely whether they look correct to our validator.
    # --------------------------------------------------------

    if not CALL_MCP:
        return record

    mcp_start = time.perf_counter()

    try:
        agent_app.call_mcp_tool(
            tool_name=selected_tool,
            arguments=arguments,
            access_token=access_token,
        )

        record["initial_mcp_success"] = True

        record["final_mcp_success"] = True

    except Exception as first_error:
        record["initial_mcp_success"] = False

        # ----------------------------------------------------
        # STEP 4: Match the deployed agent behavior.
        #
        # agent_app.py gives the model one chance to repair tool
        # arguments after an MCP failure.
        # ----------------------------------------------------

        record["repair_attempted"] = True

        try:
            repaired = agent_app.repair_tool_arguments(
                question=question,
                selection=selection,
                error_message=str(first_error),
                tools=tools,
            )

            repaired_tool = repaired.get("tool_name")

            repaired_arguments = repaired.get(
                "arguments",
                {},
            )

            record["repaired_tool"] = repaired_tool

            record["repaired_arguments"] = repaired_arguments

            (
                repaired_valid,
                repaired_issues,
            ) = validate_arguments_against_schema(
                tool_name=repaired_tool,
                arguments=repaired_arguments,
                tools=tools,
            )

            record["repaired_argument_schema_valid"] = repaired_valid

            record["repaired_argument_validation_issues"] = repaired_issues

            agent_app.call_mcp_tool(
                tool_name=repaired_tool,
                arguments=repaired_arguments,
                access_token=access_token,
            )

            record["repair_success"] = True

            record["final_mcp_success"] = True

        except Exception as repair_error:
            record["repair_success"] = False

            record["final_mcp_success"] = False

            record["error"] = (
                "Initial MCP error: "
                + str(first_error)
                + " | Repair error: "
                + str(repair_error)
            )

    record["mcp_latency_seconds"] = round(
        time.perf_counter() - mcp_start,
        4,
    )

    return record


# ============================================================
# 9. CALCULATE AVERAGE SAFELY
# ============================================================


def safe_mean(
    values: List[Optional[float]],
) -> float:
    """
    GOAL
    ----------------------------------------------------------
    Calculate an average while ignoring None values.
    """

    cleaned = [float(value) for value in values if value is not None]

    if not cleaned:
        return 0.0

    return statistics.mean(cleaned)


# ============================================================
# 10. CREATE SUMMARY METRICS
# ============================================================


def summarize_results(
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    GOAL
    ----------------------------------------------------------
    Produce one overall row plus one row per expected tool.

    OUTPUT
    ----------------------------------------------------------
    Summary rows for terminal display and CSV.
    """

    groups = {
        "OVERALL": records,
        "SEARCH": [
            record for record in records if record["expected_tool"] == SEARCH_TOOL
        ],
        "TIMELINE": [
            record for record in records if record["expected_tool"] == TIMELINE_TOOL
        ],
        "CHART": [
            record for record in records if record["expected_tool"] == CHART_TOOL
        ],
    }

    summary_rows = []

    for (
        group_name,
        group_records,
    ) in groups.items():
        count = len(group_records)

        if count == 0:
            continue

        routing_correct_count = sum(
            bool(record.get("routing_correct")) for record in group_records
        )

        schema_valid_count = sum(
            bool(record.get("argument_schema_valid")) for record in group_records
        )

        if CALL_MCP:
            initial_mcp_count = sum(
                bool(record.get("initial_mcp_success")) for record in group_records
            )

            final_mcp_count = sum(
                bool(record.get("final_mcp_success")) for record in group_records
            )

        else:
            initial_mcp_count = 0
            final_mcp_count = 0

        # "Agent routing success" means:
        # correct expected tool AND final MCP call succeeds.
        if CALL_MCP:
            successful_agent_routes = sum(
                (
                    bool(record.get("routing_correct"))
                    and bool(record.get("final_mcp_success"))
                )
                for record in group_records
            )

            agent_route_success_rate = successful_agent_routes / count

        else:
            successful_agent_routes = routing_correct_count

            agent_route_success_rate = routing_correct_count / count

        summary_rows.append(
            {
                "group": group_name,
                "question_count": count,
                "routing_correct": routing_correct_count,
                "routing_accuracy": round(
                    routing_correct_count / count,
                    4,
                ),
                "schema_valid": schema_valid_count,
                "schema_valid_rate": round(
                    schema_valid_count / count,
                    4,
                ),
                "initial_mcp_success": initial_mcp_count if CALL_MCP else "",
                "initial_mcp_success_rate": round(
                    initial_mcp_count / count,
                    4,
                )
                if CALL_MCP
                else "",
                "final_mcp_success": final_mcp_count if CALL_MCP else "",
                "final_mcp_success_rate": round(
                    final_mcp_count / count,
                    4,
                )
                if CALL_MCP
                else "",
                "agent_route_success": successful_agent_routes,
                "agent_route_success_rate": round(
                    agent_route_success_rate,
                    4,
                ),
                "avg_router_latency_seconds": round(
                    safe_mean(
                        [
                            record.get("router_latency_seconds")
                            for record in group_records
                        ]
                    ),
                    4,
                ),
                "avg_mcp_latency_seconds": round(
                    safe_mean(
                        [record.get("mcp_latency_seconds") for record in group_records]
                    ),
                    4,
                )
                if CALL_MCP
                else "",
            }
        )

    return summary_rows


# ============================================================
# 11. BUILD CONFUSION MATRIX
# ============================================================


def build_confusion_matrix(
    records: List[Dict[str, Any]],
) -> Dict[
    str,
    Dict[str, int],
]:
    """
    GOAL
    ----------------------------------------------------------
    Show which wrong tool was selected when routing failed.

    ROW
    ----------------------------------------------------------
    Expected tool.

    COLUMN
    ----------------------------------------------------------
    Selected tool.
    """

    labels = [
        SEARCH_TOOL,
        TIMELINE_TOOL,
        CHART_TOOL,
        "OTHER",
    ]

    matrix = {
        expected: {selected: 0 for selected in labels}
        for expected in [
            SEARCH_TOOL,
            TIMELINE_TOOL,
            CHART_TOOL,
        ]
    }

    for record in records:
        expected = record.get("expected_tool")

        selected = record.get("selected_tool")

        if expected not in matrix:
            continue

        selected_label = (
            selected
            if selected
            in {
                SEARCH_TOOL,
                TIMELINE_TOOL,
                CHART_TOOL,
            }
            else "OTHER"
        )

        matrix[expected][selected_label] += 1

    return matrix


# ============================================================
# 12. SAVE DETAILED JSON
# ============================================================


def save_details(
    records: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    summary_rows: List[Dict[str, Any]],
    confusion_matrix: Dict[str, Dict[str, int]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Save complete benchmark evidence for later analysis.
    """

    payload = {
        "benchmark": "SFOE agent routing benchmark",
        "production_model": agent_app.BEDROCK_MODEL_ID,
        "mcp_calls_enabled": CALL_MCP,
        "question_count": len(records),
        "tool_names": [tool.get("name") for tool in tools],
        "summary": summary_rows,
        "confusion_matrix": confusion_matrix,
        "records": records,
    }

    DETAILS_PATH.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ============================================================
# 13. SAVE QUESTION-LEVEL CSV
# ============================================================


def save_scores_csv(
    records: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Save one row per benchmark question.
    """

    fieldnames = [
        "id",
        "language",
        "category",
        "question",
        "expected_tool",
        "selected_tool",
        "routing_correct",
        "arguments",
        "argument_schema_valid",
        "argument_validation_issues",
        "routing_reason",
        "router_latency_seconds",
        "initial_mcp_success",
        "repair_attempted",
        "repair_success",
        "final_mcp_success",
        "mcp_latency_seconds",
        "error",
    ]

    with SCORES_PATH.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for record in records:
            csv_record = {key: record.get(key) for key in fieldnames}

            csv_record["arguments"] = json.dumps(
                record.get("arguments"),
                ensure_ascii=False,
            )

            csv_record["argument_validation_issues"] = json.dumps(
                record.get(
                    "argument_validation_issues",
                    [],
                ),
                ensure_ascii=False,
            )

            writer.writerow(csv_record)


# ============================================================
# 14. SAVE SUMMARY CSV
# ============================================================


def save_summary_csv(
    summary_rows: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Save overall + per-tool benchmark metrics.
    """

    if not summary_rows:
        return

    fieldnames = list(summary_rows[0].keys())

    with SUMMARY_PATH.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(summary_rows)


# ============================================================
# 15. PRINT CONFUSION MATRIX
# ============================================================


def print_confusion_matrix(
    matrix: Dict[
        str,
        Dict[str, int],
    ],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Print a compact expected-vs-selected routing table.
    """

    print("\n" + "=" * 80)
    print("ROUTING CONFUSION MATRIX")
    print("=" * 80)

    print("\nRows = expected tool")
    print("Columns = selected tool\n")

    print(f"{'Expected':<12}{'Search':>10}{'Timeline':>10}{'Chart':>10}{'Other':>10}")

    print("-" * 52)

    for expected_tool in [
        SEARCH_TOOL,
        TIMELINE_TOOL,
        CHART_TOOL,
    ]:
        row = matrix[expected_tool]

        print(
            f"{short_tool_name(expected_tool):<12}"
            f"{row[SEARCH_TOOL]:>10}"
            f"{row[TIMELINE_TOOL]:>10}"
            f"{row[CHART_TOOL]:>10}"
            f"{row['OTHER']:>10}"
        )


# ============================================================
# 16. PRINT FINAL SUMMARY
# ============================================================


def print_summary(
    summary_rows: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Display the benchmark results clearly in the terminal.
    """

    print("\n" + "=" * 80)
    print("FINAL AGENT ROUTING BENCHMARK")
    print("=" * 80)

    for row in summary_rows:
        print(f"\n{row['group']}")

        print(f"   Questions              : {row['question_count']}")

        print(f"   Routing accuracy       : {row['routing_accuracy']:.1%}")

        print(f"   Argument schema valid  : {row['schema_valid_rate']:.1%}")

        if CALL_MCP:
            print(f"   Initial MCP success    : {row['initial_mcp_success_rate']:.1%}")

            print(f"   Final MCP success      : {row['final_mcp_success_rate']:.1%}")

        print(f"   Agent route success    : {row['agent_route_success_rate']:.1%}")

        print(f"   Avg router latency     : {row['avg_router_latency_seconds']:.2f} s")

        if CALL_MCP:
            print(f"   Avg MCP latency        : {row['avg_mcp_latency_seconds']:.2f} s")


# ============================================================
# 17. MAIN BENCHMARK
# ============================================================


def main() -> None:
    """
    GOAL
    ----------------------------------------------------------
    Run the complete routing benchmark.

    MAIN PROCESS
    ----------------------------------------------------------
    1. Validate agent configuration.
    2. Authenticate if Cognito is configured.
    3. Discover live MCP tools.
    4. Run each benchmark question through the real router.
    5. Validate generated arguments against live schemas.
    6. Optionally execute the real MCP tool.
    7. Use one repair attempt when the MCP call fails.
    8. Calculate metrics.
    9. Save JSON/CSV results.
    """

    agent_app.validate_configuration()

    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("\n" + "=" * 80)
    print("SFOE AGENT ROUTING BENCHMARK")
    print("=" * 80)

    print("\nProduction/router model:")
    print(agent_app.BEDROCK_MODEL_ID)

    print("\nMCP execution during benchmark:")
    print(CALL_MCP)

    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    if agent_app.cognito_is_configured():
        print("\nAuthenticating with Cognito...")

        access_token = agent_app.fetch_access_token()

        print("Authentication successful.")

    else:
        access_token = None

        print(
            "\nCognito is not configured. Using gateway without Authorization header."
        )

    # --------------------------------------------------------
    # Dynamic MCP discovery
    # --------------------------------------------------------

    print("\nDiscovering live MCP tools...")

    tools = agent_app.list_mcp_tools(access_token)

    live_tool_names = {tool.get("name") for tool in tools}

    print("\nDiscovered tools:")

    for tool_name in sorted(name for name in live_tool_names if name):
        print(f" - {tool_name}")

    # --------------------------------------------------------
    # Safety check:
    # benchmark expectations must exist on the live gateway.
    # --------------------------------------------------------

    required_benchmark_tools = {
        SEARCH_TOOL,
        TIMELINE_TOOL,
        CHART_TOOL,
    }

    missing_tools = required_benchmark_tools - live_tool_names

    if missing_tools:
        raise RuntimeError(
            "The live gateway is missing one or more tools "
            "required by this benchmark:\n" + "\n".join(sorted(missing_tools))
        )

    # --------------------------------------------------------
    # Apply optional question limit.
    # --------------------------------------------------------

    test_cases = ROUTING_QUESTIONS

    if BENCHMARK_LIMIT > 0:
        test_cases = test_cases[:BENCHMARK_LIMIT]

    print(f"\nQuestions: {len(test_cases)}")

    # --------------------------------------------------------
    # Run benchmark
    # --------------------------------------------------------

    records = []

    for index, test_case in enumerate(
        test_cases,
        start=1,
    ):
        print("\n" + "=" * 80)

        print(f"[{index}/{len(test_cases)}] {test_case['id']}")

        print("=" * 80)

        print("\n" + test_case["question"])

        print(f"\nExpected: {short_tool_name(test_case['expected_tool'])}")

        record = evaluate_one_question(
            test_case=test_case,
            tools=tools,
            access_token=access_token,
        )

        records.append(record)

        print(f"Selected: {short_tool_name(record.get('selected_tool'))}")

        print(f"Routing : {'PASS' if record['routing_correct'] else 'FAIL'}")

        print(f"Args    : {'PASS' if record['argument_schema_valid'] else 'FAIL'}")

        print(f"Router latency: {record.get('router_latency_seconds')} s")

        if CALL_MCP:
            print(f"MCP call: {'PASS' if record.get('final_mcp_success') else 'FAIL'}")

            if record.get("repair_attempted"):
                print(f"Repair  : {'PASS' if record.get('repair_success') else 'FAIL'}")

        if record.get("argument_validation_issues"):
            print("\nArgument issues:")

            for issue in record["argument_validation_issues"]:
                print(f" - {issue}")

        if record.get("error"):
            print("\nError:")
            print(record["error"])

    # --------------------------------------------------------
    # Calculate and save results
    # --------------------------------------------------------

    summary_rows = summarize_results(records)

    confusion_matrix = build_confusion_matrix(records)

    save_details(
        records=records,
        tools=tools,
        summary_rows=summary_rows,
        confusion_matrix=confusion_matrix,
    )

    save_scores_csv(records)

    save_summary_csv(summary_rows)

    # --------------------------------------------------------
    # Terminal report
    # --------------------------------------------------------

    print_confusion_matrix(confusion_matrix)

    print_summary(summary_rows)

    print("\n" + "=" * 80)
    print("RESULT FILES")
    print("=" * 80)

    print(f"\nDetails : {DETAILS_PATH}")

    print(f"Scores  : {SCORES_PATH}")

    print(f"Summary : {SUMMARY_PATH}")

    print("\nINTERPRETATION:")

    print("Routing accuracy is the main metric for this stage.")

    print(
        "Argument schema validity checks whether the router's "
        "arguments match the live MCP input schema."
    )

    if CALL_MCP:
        print("Final MCP success includes the one repair attempt used by agent_app.py.")

    print(
        "Final answer quality is intentionally NOT evaluated "
        "here; that belongs to the next end-to-end benchmark."
    )


# ============================================================
# 18. START BENCHMARK
# ============================================================
#
# RUN:
#
#     python benchmark_agent_routing.py
#
# QUICK TEST:
#
# In .env:
#
#     ROUTING_BENCHMARK_LIMIT=3
#
# FULL TEST:
#
#     ROUTING_BENCHMARK_LIMIT=0
#
# ============================================================

if __name__ == "__main__":
    main()
