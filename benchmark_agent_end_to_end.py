import os
import csv
import json
import time
import statistics
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

import agent_app


# ============================================================
# 1. LOAD CONFIGURATION
# ============================================================
# INPUT
# ------------------------------------------------------------
# Optional .env values:
#
# END_TO_END_BENCHMARK_LIMIT=0
#   0 = all questions
#   N = first N questions only
#
# JUDGE_MODEL_ID=qwen.qwen3-235b-a22b-2507-v1:0
#
# MAX_JUDGE_TOKENS=3000
#
# OUTPUT
# ------------------------------------------------------------
# Benchmark configuration.
# ============================================================

load_dotenv(override=True)

BENCHMARK_LIMIT = int(
    os.getenv(
        "END_TO_END_BENCHMARK_LIMIT",
        "0",
    )
)

JUDGE_MODEL_ID = os.getenv(
    "JUDGE_MODEL_ID",
    "qwen.qwen3-235b-a22b-2507-v1:0",
).strip()

MAX_JUDGE_TOKENS = int(
    os.getenv(
        "MAX_JUDGE_TOKENS",
        "3000",
    )
)

RESULTS_DIR = Path(
    "benchmark_results"
)

DETAILS_PATH = (
    RESULTS_DIR
    / "agent_end_to_end_details.json"
)

SCORES_PATH = (
    RESULTS_DIR
    / "agent_end_to_end_scores.csv"
)

SUMMARY_PATH = (
    RESULTS_DIR
    / "agent_end_to_end_summary.csv"
)


# ============================================================
# 2. TOOL NAMES
# ============================================================

SEARCH_TOOL = (
    "bfe-energy___search_energy_knowledge"
)

TIMELINE_TOOL = (
    "bfe-energy___get_metric_timeline"
)

CHART_TOOL = (
    "bfe-energy___get_chart_data"
)


# ============================================================
# 3. END-TO-END TEST QUESTIONS
# ============================================================
# GOAL
# ------------------------------------------------------------
# Exercise the full agent:
#
# question
#   -> route
#   -> MCP
#   -> evidence
#   -> grounded answer
#   -> citations
#   -> semantic judge
#
# DESIGN
# ------------------------------------------------------------
# 15 questions total:
# 5 general/search
# 5 timeline
# 5 chart/table
#
# Includes multilingual and stress-style cases.
#
# IMPORTANT
# ------------------------------------------------------------
# These expected tool labels are defined by OUR agent design,
# based on the challenge-owner MCP tool descriptions.
# ============================================================

TEST_CASES: List[
    Dict[str, Any]
] = [
    # --------------------------------------------------------
    # GENERAL / SEARCH
    # --------------------------------------------------------
    {
        "id": "E2E_SEARCH_01",
        "group": "SEARCH",
        "language": "en",
        "category": "general",
        "question":
            "What role does hydropower play in Switzerland?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "E2E_SEARCH_02",
        "group": "SEARCH",
        "language": "de",
        "category": "policy",
        "question":
            "Welche Rolle spielt Wasserstoff in der Schweizer Energiepolitik?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "E2E_SEARCH_03",
        "group": "SEARCH",
        "language": "fr",
        "category": "policy",
        "question":
            "Quels sont les objectifs suisses en matière d'énergie renouvelable ?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "E2E_SEARCH_04",
        "group": "SEARCH",
        "language": "de",
        "category": "source_precision",
        "question":
            "Was genau bedeutet die häufig genannte Zahl von rund 58 Prozent für die Schweizer Wasserkraft im Jahr 2020? Bezieht sie sich auf Stromproduktion oder Strombedarf?",
        "expected_tool": SEARCH_TOOL,
    },
    {
        "id": "E2E_SEARCH_05",
        "group": "SEARCH",
        "language": "en",
        "category": "insufficient_evidence",
        "question":
            "What was the exact electricity production of every individual hydropower plant in Switzerland in 2024?",
        "expected_tool": SEARCH_TOOL,
    },

    # --------------------------------------------------------
    # TIMELINE
    # --------------------------------------------------------
    {
        "id": "E2E_TIMELINE_01",
        "group": "TIMELINE",
        "language": "en",
        "category": "historical",
        "question":
            "How has photovoltaic production developed from 2020 to 2024?",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "E2E_TIMELINE_02",
        "group": "TIMELINE",
        "language": "de",
        "category": "historical",
        "question":
            "Wie hat sich die Photovoltaikproduktion von 2020 bis 2024 entwickelt?",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "E2E_TIMELINE_03",
        "group": "TIMELINE",
        "language": "fr",
        "category": "historical",
        "question":
            "Comment la production photovoltaïque a-t-elle évolué entre 2020 et 2024 ?",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "E2E_TIMELINE_04",
        "group": "TIMELINE",
        "language": "en",
        "category": "annual_values",
        "question":
            "Give me the yearly values for solar electricity production from 2020 through 2024.",
        "expected_tool": TIMELINE_TOOL,
    },
    {
        "id": "E2E_TIMELINE_05",
        "group": "TIMELINE",
        "language": "de",
        "category": "renewables_trend",
        "question":
            "Wie hat sich die erneuerbare Stromproduktion von 2019 bis 2023 verändert?",
        "expected_tool": TIMELINE_TOOL,
    },

    # --------------------------------------------------------
    # CHART / TABLE
    # --------------------------------------------------------
    {
        "id": "E2E_CHART_01",
        "group": "CHART",
        "language": "en",
        "category": "chart",
        "question":
            "What values are shown in the SFOE chart for renewable electricity production?",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "E2E_CHART_02",
        "group": "CHART",
        "language": "de",
        "category": "chart",
        "question":
            "Welche Werte zeigt das BFE-Diagramm zur erneuerbaren Stromproduktion?",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "E2E_CHART_03",
        "group": "CHART",
        "language": "fr",
        "category": "chart",
        "question":
            "Quelles valeurs sont indiquées dans le graphique de l'OFEN sur la production d'électricité renouvelable ?",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "E2E_CHART_04",
        "group": "CHART",
        "language": "en",
        "category": "chart_specific_year",
        "question":
            "According to the SFOE chart, what value is shown for renewable electricity production in 2024?",
        "expected_tool": CHART_TOOL,
    },
    {
        "id": "E2E_CHART_05",
        "group": "CHART",
        "language": "de",
        "category": "table",
        "question":
            "Extrahiere die Zahlen aus der BFE-Tabelle zur Photovoltaikproduktion.",
        "expected_tool": CHART_TOOL,
    },
]


# ============================================================
# 4. SEMANTIC QUALITY WEIGHTS
# ============================================================
# These weights add to 1.0 within semantic answer quality.
#
# The final END-TO-END score is:
#
#   80% semantic answer quality
#   10% routing correctness
#    5% MCP execution success
#    5% end-to-end latency
# ============================================================

SEMANTIC_WEIGHTS = {
    "groundedness": 0.25,
    "citation_correctness": 0.20,
    "completeness": 0.15,
    "numerical_accuracy": 0.10,
    "uncertainty_handling": 0.10,
    "abstention_quality": 0.10,
    "relevance": 0.05,
    "language_quality": 0.05,
}

SEMANTIC_FINAL_WEIGHT = 0.80
ROUTING_FINAL_WEIGHT = 0.10
MCP_FINAL_WEIGHT = 0.05
LATENCY_FINAL_WEIGHT = 0.05

SCORE_FIELDS = list(
    SEMANTIC_WEIGHTS.keys()
)


# ============================================================
# 5. LATENCY SCORE
# ============================================================

def latency_score(
    seconds: Optional[float],
) -> float:
    """
    GOAL
    ----------------------------------------------------------
    Convert total end-to-end latency to a small 0..1 score.

    IMPORTANT
    ----------------------------------------------------------
    Latency is only 5% of the final score.
    Quality remains much more important.
    """

    if seconds is None:
        return 0.0

    if seconds <= 5:
        return 1.0

    if seconds <= 10:
        return 0.8

    if seconds <= 20:
        return 0.6

    if seconds <= 30:
        return 0.4

    return 0.2


# ============================================================
# 6. EXTRACT JSON FROM JUDGE RESPONSE
# ============================================================

def extract_json_object(
    text: str,
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Parse judge JSON robustly even if a code fence is added.
    """

    cleaned = text.strip()

    try:
        return json.loads(
            cleaned
        )
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        r"\s*```$",
        "",
        cleaned,
    ).strip()

    try:
        return json.loads(
            cleaned
        )
    except json.JSONDecodeError:
        pass

    start = cleaned.find(
        "{"
    )

    if start == -1:
        raise ValueError(
            "Judge response did not contain a JSON object."
        )

    depth = 0
    in_string = False
    escape = False

    for index in range(
        start,
        len(cleaned),
    ):
        char = cleaned[index]

        if escape:
            escape = False
            continue

        if (
            char == "\\"
            and in_string
        ):
            escape = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1

        elif char == "}":
            depth -= 1

            if depth == 0:
                return json.loads(
                    cleaned[
                        start:index + 1
                    ]
                )

    raise ValueError(
        "Could not extract complete judge JSON."
    )


# ============================================================
# 7. BUILD JUDGE PROMPT
# ============================================================

def build_judge_prompt(
    test_case: Dict[str, Any],
    selected_tool: str,
    evidence_context: str,
    answer: str,
) -> str:
    """
    GOAL
    ----------------------------------------------------------
    Ask an independent LLM to score the FINAL agent answer
    against the exact evidence available to the production agent.

    INPUT
    ----------------------------------------------------------
    Question
    Expected tool
    Selected tool
    MCP evidence/context
    Final answer

    OUTPUT
    ----------------------------------------------------------
    Strict JSON-only judge prompt.
    """

    return f"""
You are an independent evaluator of a RAG-based SFOE energy
knowledge agent.

Evaluate ONLY the candidate answer against the supplied MCP
evidence.

Do not use outside knowledge.

============================================================
QUESTION
============================================================

{test_case["question"]}

Expected language:
{test_case["language"]}

Question category:
{test_case["category"]}

Expected MCP tool:
{test_case["expected_tool"]}

Selected MCP tool:
{selected_tool}

============================================================
MCP EVIDENCE AVAILABLE TO THE AGENT
============================================================

{evidence_context}

============================================================
CANDIDATE ANSWER
============================================================

{answer}

============================================================
EVALUATION RULES
============================================================

Score every dimension from 1 to 5.

5 means excellent.
1 means serious failure.

Be strict. Do not award 5 unless the behavior is genuinely
excellent.

1. groundedness
   - Are factual claims directly supported by the supplied MCP
     evidence?
   - Penalize unsupported strengthening, inference or invention.

2. citation_correctness
   - Do citations support the exact claims beside them?
   - Penalize citation dumping at the end of unrelated claims.
   - Penalize invalid or unsupported citation numbers.

3. completeness
   - Does the answer sufficiently answer the user's request?
   - For timeline questions, if yearly values are available,
     expect the requested years to be covered.
   - For chart/table questions, expect the requested values or
     a faithful summary of all relevant available values.

4. numerical_accuracy
   - Are values, years, units and percentages consistent with
     the evidence?
   - Distinguish production vs demand.
   - Distinguish capacity vs generation.
   - Distinguish national totals vs individual values.

5. uncertainty_handling
   - If evidence contains estimates, ranges, projections,
     truncation or extraction limitations, are they represented
     faithfully?
   - Penalize presenting estimated values as exact.
   - Penalize turning a range into an invented midpoint.

6. abstention_quality
   - If evidence is insufficient, does the answer say so rather
     than guess?
   - If evidence is sufficient, do not penalize the answer for
     answering normally.

7. relevance
   - Is the answer direct and focused on the exact question?

8. language_quality
   - Is the answer clear and in the requested language?

============================================================
CLAIM CHECK
============================================================

Identify important factual claims and classify each as one of:

- directly_supported
- partially_supported
- unsupported
- contradicted

============================================================
RETURN FORMAT
============================================================

Return ONLY valid JSON.

Use exactly this structure:

{{
  "claim_checks": [
    {{
      "claim": "short factual claim",
      "classification": "directly_supported",
      "reason": "short explanation"
    }}
  ],
  "scores": {{
    "groundedness": 1,
    "citation_correctness": 1,
    "completeness": 1,
    "numerical_accuracy": 1,
    "uncertainty_handling": 1,
    "abstention_quality": 1,
    "relevance": 1,
    "language_quality": 1
  }},
  "unsupported_claims": [
    "claim text"
  ],
  "citation_issues": [
    "issue"
  ],
  "uncertainty_issues": [
    "issue"
  ],
  "strengths": [
    "strength"
  ],
  "summary": "short overall assessment"
}}
"""


# ============================================================
# 8. RUN SEMANTIC JUDGE
# ============================================================

def judge_answer(
    test_case: Dict[str, Any],
    selected_tool: str,
    evidence_context: str,
    answer: str,
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Run the independent Bedrock judge.
    """

    prompt = build_judge_prompt(
        test_case=
            test_case,
        selected_tool=
            selected_tool,
        evidence_context=
            evidence_context,
        answer=
            answer,
    )

    response = (
        agent_app.bedrock_runtime.converse(
            modelId=
                JUDGE_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt,
                        }
                    ],
                }
            ],
            inferenceConfig={
                "maxTokens":
                    MAX_JUDGE_TOKENS,
                "temperature":
                    0,
            },
        )
    )

    text = (
        agent_app.extract_generated_text(
            response
        )
    )

    result = extract_json_object(
        text
    )

    scores = result.get(
        "scores",
        {}
    )

    for field in SCORE_FIELDS:
        value = scores.get(
            field
        )

        if not isinstance(
            value,
            (
                int,
                float,
            ),
        ):
            raise ValueError(
                f"Judge score '{field}' is missing or invalid."
            )

        if not (
            1
            <= float(value)
            <= 5
        ):
            raise ValueError(
                f"Judge score '{field}' must be between 1 and 5."
            )

    return result


# ============================================================
# 9. CALCULATE SEMANTIC QUALITY
# ============================================================

def calculate_semantic_quality(
    judge_result: Dict[str, Any],
) -> float:
    """
    GOAL
    ----------------------------------------------------------
    Convert eight 1..5 judge scores into a normalized 0..1
    semantic quality score.
    """

    scores = judge_result[
        "scores"
    ]

    weighted = 0.0

    for (
        field,
        weight,
    ) in SEMANTIC_WEIGHTS.items():

        normalized = (
            float(
                scores[field]
            )
            / 5.0
        )

        weighted += (
            weight
            * normalized
        )

    return weighted


# ============================================================
# 10. RUN ONE FULL END-TO-END QUESTION
# ============================================================

def evaluate_one_case(
    test_case: Dict[str, Any],
    tools: List[Dict[str, Any]],
    access_token: Optional[str],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Run the REAL agent pipeline for one question.

    PROCESS
    ----------------------------------------------------------
    question
      -> choose_tool()
      -> call_mcp_tool()
      -> prepare_agent_context()
      -> generate_answer()
      -> clean_answer_and_sources()
      -> semantic judge
    """

    started = (
        time.perf_counter()
    )

    record: Dict[str, Any] = {
        "id":
            test_case["id"],
        "group":
            test_case["group"],
        "language":
            test_case["language"],
        "category":
            test_case["category"],
        "question":
            test_case["question"],
        "expected_tool":
            test_case["expected_tool"],
        "selected_tool":
            None,
        "routing_correct":
            False,
        "routing_reason":
            None,
        "arguments":
            None,
        "mcp_success":
            False,
        "repair_attempted":
            False,
        "repair_success":
            False,
        # Raw answer uses the ORIGINAL citation numbering from
        # the evidence context. The semantic judge evaluates this
        # version so citation references remain aligned.
        "raw_answer":
            None,

        # User-facing answer after deterministic citation/source
        # cleanup and renumbering.
        "answer":
            None,

        "sources":
            [],
        "judge":
            None,
        "semantic_quality":
            0.0,
        "latency_score":
            0.0,
        "end_to_end_score":
            0.0,
        # User-facing deployed-agent latency ONLY.
        # This excludes the independent evaluation judge.
        "total_latency_seconds":
            None,

        # Judge latency is evaluator overhead and is tracked
        # separately for transparency.
        "judge_latency_seconds":
            None,

        "error":
            None,
    }

    question = test_case[
        "question"
    ]

    try:
        # ----------------------------------------------------
        # STEP 1: ROUTING
        # ----------------------------------------------------

        selection = (
            agent_app.choose_tool(
                question=
                    question,
                tools=
                    tools,
            )
        )

        selected_tool = selection[
            "tool_name"
        ]

        arguments = selection[
            "arguments"
        ]

        record[
            "selected_tool"
        ] = selected_tool

        record[
            "routing_reason"
        ] = selection.get(
            "reason"
        )

        record[
            "arguments"
        ] = arguments

        record[
            "routing_correct"
        ] = (
            selected_tool
            == test_case[
                "expected_tool"
            ]
        )

        # ----------------------------------------------------
        # STEP 2: MCP CALL
        # ----------------------------------------------------

        try:
            tool_payload = (
                agent_app.call_mcp_tool(
                    tool_name=
                        selected_tool,
                    arguments=
                        arguments,
                    access_token=
                        access_token,
                )
            )

            record[
                "mcp_success"
            ] = True

        except Exception as first_error:
            # ------------------------------------------------
            # Match agent_app.py:
            # one controlled argument repair attempt.
            # ------------------------------------------------

            record[
                "repair_attempted"
            ] = True

            repaired = (
                agent_app.repair_tool_arguments(
                    question=
                        question,
                    selection=
                        selection,
                    error_message=
                        str(first_error),
                    tools=
                        tools,
                )
            )

            selected_tool = repaired[
                "tool_name"
            ]

            arguments = repaired[
                "arguments"
            ]

            record[
                "selected_tool_after_repair"
            ] = selected_tool

            record[
                "arguments_after_repair"
            ] = arguments

            tool_payload = (
                agent_app.call_mcp_tool(
                    tool_name=
                        selected_tool,
                    arguments=
                        arguments,
                    access_token=
                        access_token,
                )
            )

            record[
                "repair_success"
            ] = True

            record[
                "mcp_success"
            ] = True

        # ----------------------------------------------------
        # STEP 3: PREPARE EVIDENCE
        # ----------------------------------------------------

        (
            evidence_context,
            sources,
        ) = (
            agent_app.prepare_agent_context(
                tool_name=
                    selected_tool,
                tool_payload=
                    tool_payload,
            )
        )

        # ----------------------------------------------------
        # STEP 4: FINAL ANSWER
        # ----------------------------------------------------

        # ----------------------------------------------------
        # Generate the RAW answer.
        #
        # IMPORTANT:
        # Its citation numbers correspond directly to the
        # ORIGINAL evidence_context numbering.
        # ----------------------------------------------------

        raw_answer = (
            agent_app.generate_answer(
                question=
                    question,
                tool_name=
                    selected_tool,
                context=
                    evidence_context,
            )
        )

        record[
            "raw_answer"
        ] = raw_answer

        # ----------------------------------------------------
        # Create the USER-FACING answer.
        #
        # The cleanup may deduplicate / renumber sources.
        # This version is stored as the deployed output.
        # ----------------------------------------------------

        (
            answer,
            sources,
        ) = (
            agent_app.clean_answer_and_sources(
                answer=
                    raw_answer,
                sources=
                    sources,
            )
        )

        record[
            "answer"
        ] = answer

        record[
            "sources"
        ] = sources

        # ----------------------------------------------------
        # CAPTURE DEPLOYED AGENT LATENCY HERE.
        #
        # Everything above is part of the real user-facing agent:
        # routing + MCP + evidence + answer + cleanup.
        #
        # The independent LLM judge below is evaluation overhead
        # and must NOT be counted as deployed-agent latency.
        # ----------------------------------------------------

        agent_finished_at = (
            time.perf_counter()
        )

        record[
            "total_latency_seconds"
        ] = round(
            agent_finished_at
            - started,
            4,
        )

        # ----------------------------------------------------
        # STEP 5: SEMANTIC JUDGE
        # ----------------------------------------------------
        # Judge the RAW answer against the ORIGINAL evidence
        # context so citation numbers are aligned correctly.
        # ----------------------------------------------------

        judge_started_at = (
            time.perf_counter()
        )

        judge_result = (
            judge_answer(
                test_case=
                    test_case,
                selected_tool=
                    selected_tool,
                evidence_context=
                    evidence_context,
                answer=
                    raw_answer,
            )
        )

        record[
            "judge_latency_seconds"
        ] = round(
            time.perf_counter()
            - judge_started_at,
            4,
        )

        semantic_quality = (
            calculate_semantic_quality(
                judge_result
            )
        )

        record[
            "judge"
        ] = judge_result

        record[
            "semantic_quality"
        ] = round(
            semantic_quality,
            4,
        )

    except Exception as error:
        record[
            "error"
        ] = str(
            error
        )

    # --------------------------------------------------------
    # STEP 6: LATENCY + FINAL SCORE
    # --------------------------------------------------------
    # total_latency_seconds should represent the deployed agent,
    # NOT the independent evaluation judge.
    #
    # If an exception occurred before we captured normal agent
    # completion, use elapsed time up to the failure.
    # --------------------------------------------------------

    if (
        record[
            "total_latency_seconds"
        ]
        is None
    ):
        record[
            "total_latency_seconds"
        ] = round(
            time.perf_counter()
            - started,
            4,
        )

    latency_component = (
        latency_score(
            record[
                "total_latency_seconds"
            ]
        )
    )

    record[
        "latency_score"
    ] = latency_component

    routing_component = (
        1.0
        if record[
            "routing_correct"
        ]
        else 0.0
    )

    mcp_component = (
        1.0
        if record[
            "mcp_success"
        ]
        else 0.0
    )

    final_score = (
        SEMANTIC_FINAL_WEIGHT
        * record[
            "semantic_quality"
        ]
        + ROUTING_FINAL_WEIGHT
        * routing_component
        + MCP_FINAL_WEIGHT
        * mcp_component
        + LATENCY_FINAL_WEIGHT
        * latency_component
    )

    record[
        "end_to_end_score"
    ] = round(
        final_score,
        4,
    )

    return record


# ============================================================
# 11. SAFE MEAN
# ============================================================

def safe_mean(
    values: List[Optional[float]],
) -> float:
    """
    GOAL
    ----------------------------------------------------------
    Calculate mean while ignoring None.
    """

    cleaned = [
        float(value)
        for value in values
        if value is not None
    ]

    if not cleaned:
        return 0.0

    return statistics.mean(
        cleaned
    )


# ============================================================
# 12. SCORE FIELD MEAN
# ============================================================

def average_judge_field(
    records: List[Dict[str, Any]],
    field: str,
) -> float:
    """
    GOAL
    ----------------------------------------------------------
    Average one 1..5 judge dimension.
    """

    values = []

    for record in records:
        judge = record.get(
            "judge"
        )

        if not isinstance(
            judge,
            dict,
        ):
            continue

        score = (
            judge.get(
                "scores",
                {},
            )
            .get(
                field
            )
        )

        if isinstance(
            score,
            (
                int,
                float,
            ),
        ):
            values.append(
                float(score)
            )

    return safe_mean(
        values
    )


# ============================================================
# 13. SUMMARIZE RESULTS
# ============================================================

def summarize_group(
    group_name: str,
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    GOAL
    ----------------------------------------------------------
    Summarize one subset of end-to-end results.
    """

    count = len(
        records
    )

    routing_accuracy = (
        sum(
            bool(
                record[
                    "routing_correct"
                ]
            )
            for record
            in records
        )
        / count
        if count
        else 0.0
    )

    mcp_success_rate = (
        sum(
            bool(
                record[
                    "mcp_success"
                ]
            )
            for record
            in records
        )
        / count
        if count
        else 0.0
    )

    success_rate = (
        sum(
            record.get(
                "error"
            )
            is None
            for record
            in records
        )
        / count
        if count
        else 0.0
    )

    unsupported_count = 0
    citation_issue_count = 0
    uncertainty_issue_count = 0

    for record in records:
        judge = record.get(
            "judge"
        )

        if not isinstance(
            judge,
            dict,
        ):
            continue

        unsupported_count += len(
            judge.get(
                "unsupported_claims",
                [],
            )
        )

        citation_issue_count += len(
            judge.get(
                "citation_issues",
                [],
            )
        )

        uncertainty_issue_count += len(
            judge.get(
                "uncertainty_issues",
                [],
            )
        )

    row = {
        "group":
            group_name,
        "question_count":
            count,
        "end_to_end_score":
            round(
                safe_mean(
                    [
                        record[
                            "end_to_end_score"
                        ]
                        for record
                        in records
                    ]
                ),
                4,
            ),
        "semantic_quality":
            round(
                safe_mean(
                    [
                        record[
                            "semantic_quality"
                        ]
                        for record
                        in records
                    ]
                ),
                4,
            ),
        "routing_accuracy":
            round(
                routing_accuracy,
                4,
            ),
        "mcp_success_rate":
            round(
                mcp_success_rate,
                4,
            ),
        "full_pipeline_success_rate":
            round(
                success_rate,
                4,
            ),
        "avg_total_latency_seconds":
            round(
                safe_mean(
                    [
                        record[
                            "total_latency_seconds"
                        ]
                        for record
                        in records
                    ]
                ),
                4,
            ),

        "avg_judge_latency_seconds":
            round(
                safe_mean(
                    [
                        record.get(
                            "judge_latency_seconds"
                        )
                        for record
                        in records
                    ]
                ),
                4,
            ),

        "groundedness":
            round(
                average_judge_field(
                    records,
                    "groundedness",
                ),
                3,
            ),
        "citation_correctness":
            round(
                average_judge_field(
                    records,
                    "citation_correctness",
                ),
                3,
            ),
        "completeness":
            round(
                average_judge_field(
                    records,
                    "completeness",
                ),
                3,
            ),
        "numerical_accuracy":
            round(
                average_judge_field(
                    records,
                    "numerical_accuracy",
                ),
                3,
            ),
        "uncertainty_handling":
            round(
                average_judge_field(
                    records,
                    "uncertainty_handling",
                ),
                3,
            ),
        "abstention_quality":
            round(
                average_judge_field(
                    records,
                    "abstention_quality",
                ),
                3,
            ),
        "relevance":
            round(
                average_judge_field(
                    records,
                    "relevance",
                ),
                3,
            ),
        "language_quality":
            round(
                average_judge_field(
                    records,
                    "language_quality",
                ),
                3,
            ),
        "unsupported_claims":
            unsupported_count,
        "citation_issues":
            citation_issue_count,
        "uncertainty_issues":
            uncertainty_issue_count,
    }

    return row


def summarize_results(
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    GOAL
    ----------------------------------------------------------
    Produce OVERALL + SEARCH + TIMELINE + CHART summaries.
    """

    return [
        summarize_group(
            "OVERALL",
            records,
        ),
        summarize_group(
            "SEARCH",
            [
                record
                for record
                in records
                if record[
                    "group"
                ]
                == "SEARCH"
            ],
        ),
        summarize_group(
            "TIMELINE",
            [
                record
                for record
                in records
                if record[
                    "group"
                ]
                == "TIMELINE"
            ],
        ),
        summarize_group(
            "CHART",
            [
                record
                for record
                in records
                if record[
                    "group"
                ]
                == "CHART"
            ],
        ),
    ]


# ============================================================
# 14. SAVE JSON
# ============================================================

def save_details(
    records: List[Dict[str, Any]],
    summary_rows: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Save complete end-to-end evidence.
    """

    payload = {
        "benchmark":
            "SFOE agent end-to-end benchmark",
        "production_model":
            agent_app.BEDROCK_MODEL_ID,
        "judge_model":
            JUDGE_MODEL_ID,
        "semantic_weights":
            SEMANTIC_WEIGHTS,
        "final_weights": {
            "semantic_quality":
                SEMANTIC_FINAL_WEIGHT,
            "routing_correctness":
                ROUTING_FINAL_WEIGHT,
            "mcp_success":
                MCP_FINAL_WEIGHT,
            "latency":
                LATENCY_FINAL_WEIGHT,
        },
        "question_count":
            len(records),
        "summary":
            summary_rows,
        "records":
            records,
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
# 15. SAVE QUESTION-LEVEL CSV
# ============================================================

def save_scores_csv(
    records: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Save one compact row per end-to-end test question.
    """

    fieldnames = [
        "id",
        "group",
        "language",
        "category",
        "question",
        "expected_tool",
        "selected_tool",
        "routing_correct",
        "mcp_success",
        "repair_attempted",
        "repair_success",
        "semantic_quality",
        "end_to_end_score",
        "total_latency_seconds",
        "judge_latency_seconds",
        "answer",
        "groundedness",
        "citation_correctness",
        "completeness",
        "numerical_accuracy",
        "uncertainty_handling",
        "abstention_quality",
        "relevance",
        "language_quality",
        "unsupported_claim_count",
        "citation_issue_count",
        "uncertainty_issue_count",
        "error",
    ]

    with SCORES_PATH.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=
                fieldnames,
        )

        writer.writeheader()

        for record in records:

            judge = (
                record.get(
                    "judge"
                )
                or {}
            )

            scores = judge.get(
                "scores",
                {}
            )

            row = {
                "id":
                    record["id"],
                "group":
                    record["group"],
                "language":
                    record["language"],
                "category":
                    record["category"],
                "question":
                    record["question"],
                "expected_tool":
                    record[
                        "expected_tool"
                    ],
                "selected_tool":
                    record.get(
                        "selected_tool"
                    ),
                "routing_correct":
                    record[
                        "routing_correct"
                    ],
                "mcp_success":
                    record[
                        "mcp_success"
                    ],
                "repair_attempted":
                    record[
                        "repair_attempted"
                    ],
                "repair_success":
                    record[
                        "repair_success"
                    ],
                "semantic_quality":
                    record[
                        "semantic_quality"
                    ],
                "end_to_end_score":
                    record[
                        "end_to_end_score"
                    ],
                "total_latency_seconds":
                    record[
                        "total_latency_seconds"
                    ],
                "judge_latency_seconds":
                    record.get(
                        "judge_latency_seconds"
                    ),
                "answer":
                    record.get(
                        "answer"
                    ),
                "groundedness":
                    scores.get(
                        "groundedness"
                    ),
                "citation_correctness":
                    scores.get(
                        "citation_correctness"
                    ),
                "completeness":
                    scores.get(
                        "completeness"
                    ),
                "numerical_accuracy":
                    scores.get(
                        "numerical_accuracy"
                    ),
                "uncertainty_handling":
                    scores.get(
                        "uncertainty_handling"
                    ),
                "abstention_quality":
                    scores.get(
                        "abstention_quality"
                    ),
                "relevance":
                    scores.get(
                        "relevance"
                    ),
                "language_quality":
                    scores.get(
                        "language_quality"
                    ),
                "unsupported_claim_count":
                    len(
                        judge.get(
                            "unsupported_claims",
                            [],
                        )
                    ),
                "citation_issue_count":
                    len(
                        judge.get(
                            "citation_issues",
                            [],
                        )
                    ),
                "uncertainty_issue_count":
                    len(
                        judge.get(
                            "uncertainty_issues",
                            [],
                        )
                    ),
                "error":
                    record.get(
                        "error"
                    ),
            }

            writer.writerow(
                row
            )


# ============================================================
# 16. SAVE SUMMARY CSV
# ============================================================

def save_summary_csv(
    summary_rows: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Save overall + per-tool summary.
    """

    if not summary_rows:
        return

    with SUMMARY_PATH.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=
                list(
                    summary_rows[
                        0
                    ].keys()
                ),
        )

        writer.writeheader()

        writer.writerows(
            summary_rows
        )


# ============================================================
# 17. PRINT ONE RESULT
# ============================================================

def print_case_result(
    record: Dict[str, Any],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Print compact per-question benchmark results.
    """

    print(
        "\nSelected tool : "
        f"{record.get('selected_tool')}"
    )

    print(
        "Routing       : "
        f"{'PASS' if record['routing_correct'] else 'FAIL'}"
    )

    print(
        "MCP           : "
        f"{'PASS' if record['mcp_success'] else 'FAIL'}"
    )

    if record.get(
        "judge"
    ):
        scores = record[
            "judge"
        ][
            "scores"
        ]

        print(
            "Semantic      : "
            f"{record['semantic_quality']:.3f}"
        )

        print(
            "Groundedness  : "
            f"{scores['groundedness']}/5"
        )

        print(
            "Citations     : "
            f"{scores['citation_correctness']}/5"
        )

        print(
            "Completeness  : "
            f"{scores['completeness']}/5"
        )

        print(
            "Numerical     : "
            f"{scores['numerical_accuracy']}/5"
        )

        print(
            "Uncertainty   : "
            f"{scores['uncertainty_handling']}/5"
        )

        print(
            "Abstention    : "
            f"{scores['abstention_quality']}/5"
        )

    print(
        "Final score   : "
        f"{record['end_to_end_score']:.3f}"
    )

    print(
        "Agent latency : "
        f"{record['total_latency_seconds']:.2f} s"
    )

    if record.get(
        "judge_latency_seconds"
    ) is not None:
        print(
            "Judge latency : "
            f"{record['judge_latency_seconds']:.2f} s"
        )

    if record.get(
        "error"
    ):
        print(
            "\nERROR:"
        )
        print(
            record[
                "error"
            ]
        )


# ============================================================
# 18. PRINT FINAL SUMMARY
# ============================================================

def print_summary(
    summary_rows: List[Dict[str, Any]],
) -> None:
    """
    GOAL
    ----------------------------------------------------------
    Print overall and per-tool end-to-end scores.
    """

    print(
        "\n"
        + "=" * 80
    )

    print(
        "FINAL END-TO-END AGENT EVALUATION"
    )

    print(
        "=" * 80
    )

    for row in summary_rows:

        print(
            f"\n{row['group']}"
        )

        print(
            "   Questions               : "
            f"{row['question_count']}"
        )

        print(
            "   End-to-end score        : "
            f"{row['end_to_end_score']:.3f}"
        )

        print(
            "   Semantic quality        : "
            f"{row['semantic_quality']:.3f}"
        )

        print(
            "   Routing accuracy        : "
            f"{row['routing_accuracy']:.1%}"
        )

        print(
            "   MCP success             : "
            f"{row['mcp_success_rate']:.1%}"
        )

        print(
            "   Pipeline success        : "
            f"{row['full_pipeline_success_rate']:.1%}"
        )

        print(
            "   Groundedness            : "
            f"{row['groundedness']:.2f}/5"
        )

        print(
            "   Citation correctness    : "
            f"{row['citation_correctness']:.2f}/5"
        )

        print(
            "   Completeness            : "
            f"{row['completeness']:.2f}/5"
        )

        print(
            "   Numerical accuracy      : "
            f"{row['numerical_accuracy']:.2f}/5"
        )

        print(
            "   Uncertainty handling    : "
            f"{row['uncertainty_handling']:.2f}/5"
        )

        print(
            "   Abstention quality      : "
            f"{row['abstention_quality']:.2f}/5"
        )

        print(
            "   Avg agent latency       : "
            f"{row['avg_total_latency_seconds']:.2f} s"
        )

        print(
            "   Avg judge latency       : "
            f"{row['avg_judge_latency_seconds']:.2f} s"
        )

        print(
            "   Unsupported claims      : "
            f"{row['unsupported_claims']}"
        )

        print(
            "   Citation issues         : "
            f"{row['citation_issues']}"
        )

        print(
            "   Uncertainty issues      : "
            f"{row['uncertainty_issues']}"
        )


# ============================================================
# 19. MAIN
# ============================================================

def main() -> None:
    """
    GOAL
    ----------------------------------------------------------
    Run the full end-to-end benchmark.

    MAIN PROCESS
    ----------------------------------------------------------
    1. Authenticate.
    2. Discover live MCP tools.
    3. Run the real agent pipeline.
    4. Judge every final answer independently.
    5. Calculate end-to-end score.
    6. Save JSON + CSV results.
    """

    agent_app.validate_configuration()

    RESULTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        "\n"
        + "=" * 80
    )

    print(
        "SFOE END-TO-END AGENT BENCHMARK"
    )

    print(
        "=" * 80
    )

    print(
        "\nProduction model:"
    )

    print(
        agent_app.BEDROCK_MODEL_ID
    )

    print(
        "\nJudge model:"
    )

    print(
        JUDGE_MODEL_ID
    )

    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    if agent_app.cognito_is_configured():

        print(
            "\nAuthenticating with Cognito..."
        )

        access_token = (
            agent_app.fetch_access_token()
        )

        print(
            "Authentication successful."
        )

    else:

        access_token = None

        print(
            "\nNo Cognito configuration. "
            "Using gateway without Authorization header."
        )

    # --------------------------------------------------------
    # Discover tools
    # --------------------------------------------------------

    print(
        "\nDiscovering MCP tools..."
    )

    tools = (
        agent_app.list_mcp_tools(
            access_token
        )
    )

    live_names = {
        tool.get(
            "name"
        )
        for tool in tools
    }

    for name in sorted(
        name
        for name in live_names
        if name
    ):
        print(
            f" - {name}"
        )

    required = {
        SEARCH_TOOL,
        TIMELINE_TOOL,
        CHART_TOOL,
    }

    missing = (
        required
        - live_names
    )

    if missing:
        raise RuntimeError(
            "Required tools missing from live gateway:\n"
            + "\n".join(
                sorted(
                    missing
                )
            )
        )

    # --------------------------------------------------------
    # Apply optional benchmark limit.
    # --------------------------------------------------------

    cases = TEST_CASES

    if BENCHMARK_LIMIT > 0:
        cases = cases[
            :BENCHMARK_LIMIT
        ]

    print(
        f"\nQuestions: {len(cases)}"
    )

    print(
        f"Judge calls: {len(cases)}"
    )

    # --------------------------------------------------------
    # Execute benchmark
    # --------------------------------------------------------

    records = []

    for index, test_case in enumerate(
        cases,
        start=1,
    ):

        print(
            "\n"
            + "=" * 80
        )

        print(
            f"[{index}/{len(cases)}] "
            f"{test_case['id']} "
            f"[{test_case['group']}]"
        )

        print(
            "=" * 80
        )

        print(
            "\n"
            + test_case[
                "question"
            ]
        )

        record = evaluate_one_case(
            test_case=
                test_case,
            tools=
                tools,
            access_token=
                access_token,
        )

        records.append(
            record
        )

        print_case_result(
            record
        )

    # --------------------------------------------------------
    # Summary + files
    # --------------------------------------------------------

    summary_rows = (
        summarize_results(
            records
        )
    )

    save_details(
        records=
            records,
        summary_rows=
            summary_rows,
    )

    save_scores_csv(
        records
    )

    save_summary_csv(
        summary_rows
    )

    print_summary(
        summary_rows
    )

    print(
        "\n"
        + "=" * 80
    )

    print(
        "RESULT FILES"
    )

    print(
        "=" * 80
    )

    print(
        f"\nDetails : {DETAILS_PATH}"
    )

    print(
        f"Scores  : {SCORES_PATH}"
    )

    print(
        f"Summary : {SUMMARY_PATH}"
    )

    print(
        "\nSCORING:"
    )

    print(
        "80% semantic answer quality"
    )

    print(
        "10% routing correctness"
    )

    print(
        " 5% MCP execution success"
    )

    print(
        " 5% end-to-end latency"
    )

    print(
        "\nThis benchmark evaluates the complete deployed agent "
        "pipeline, not just the router and not just the LLM."
    )

    print(
        "Agent latency excludes the independent judge call."
    )

    print(
        "Citation correctness is judged on the raw answer against "
        "the original evidence numbering; source renumbering is a "
        "separate deterministic UI-cleanup step."
    )


# ============================================================
# 20. START
# ============================================================
#
# RUN FULL BENCHMARK:
#
#     python benchmark_agent_end_to_end.py
#
# QUICK TEST:
#
# In .env:
#
#     END_TO_END_BENCHMARK_LIMIT=3
#
# FULL RUN:
#
#     END_TO_END_BENCHMARK_LIMIT=0
#
# ============================================================

if __name__ == "__main__":
    main()
