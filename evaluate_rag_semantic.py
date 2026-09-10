import os
import re
import csv
import json
import statistics

import boto3

from dotenv import load_dotenv
from botocore.exceptions import BotoCoreError, ClientError


# ============================================================
# GOAL OF THIS SCRIPT
# ============================================================
# Perform a SECOND-STAGE semantic evaluation of the answers
# already produced by benchmark_rag_models.py.
#
# This script does NOT retrieve documents again and does NOT
# ask the candidate models to generate new answers.
#
# Instead, it reads:
#
#     benchmark_results/rag_benchmark_details.json
#
# and evaluates the saved answers against the EXACT SFOE
# evidence that each model originally received.
#
#
# COMPLETE DATA FLOW
# ------------------------------------------------------------
#
# rag_benchmark_details.json
#       ↓
# Select top N finalist models from previous benchmark
#       ↓
# For each question + finalist answer:
#       ↓
# Same saved SFOE evidence
#       ↓
# Independent Bedrock judge model
#       ↓
# Semantic evaluation:
#     - groundedness
#     - citation correctness
#     - completeness
#     - relevance
#     - language quality
#     - unsupported claims
#     - projection / truncation misuse
#       ↓
# Final semantic ranking
#
#
# WHY THIS IS NEEDED
# ------------------------------------------------------------
# The first benchmark used simple proxy metrics such as word
# overlap and citation syntax.
#
# Those metrics cannot reliably detect subtle problems such as:
#
#     "System stability"
#
# when the source only supports:
#
#     "critical supply situations"
#
# This script asks an independent LLM judge to compare each
# factual claim against the retrieved SFOE evidence.
# ============================================================


# ============================================================
# 1. LOAD CONFIGURATION
# ============================================================
# INPUT
# ------------------------------------------------------------
# Optional values in .env:
#
# AWS_REGION=eu-central-1
#
# JUDGE_MODEL_ID=qwen.qwen3-235b-a22b-2507-v1:0
#
# NUM_FINALISTS=2
#
# BENCHMARK_DETAILS_PATH=
# benchmark_results/rag_benchmark_details.json
#
#
# AWS credentials must be available to boto3.
#
# OUTPUT
# ------------------------------------------------------------
# Runtime configuration for semantic evaluation.
# ============================================================

load_dotenv(override=True)

AWS_REGION = os.getenv(
    "AWS_REGION",
    "eu-central-1",
)

JUDGE_MODEL_ID = os.getenv(
    "JUDGE_MODEL_ID",
    "qwen.qwen3-235b-a22b-2507-v1:0",
)

NUM_FINALISTS = int(
    os.getenv(
        "NUM_FINALISTS",
        "2",
    )
)

BENCHMARK_DETAILS_PATH = os.getenv(
    "BENCHMARK_DETAILS_PATH",
    os.path.join(
        "benchmark_results",
        "rag_benchmark_details.json",
    ),
)

OUTPUT_DIRECTORY = os.getenv(
    "SEMANTIC_OUTPUT_DIRECTORY",
    "benchmark_results",
)

MAX_JUDGE_TOKENS = int(
    os.getenv(
        "MAX_JUDGE_TOKENS",
        "1400",
    )
)


# ============================================================
# 2. SEMANTIC SCORING WEIGHTS
# ============================================================
# GOAL
# ------------------------------------------------------------
# Give the largest weight to factual grounding and citation
# correctness because this is a trusted-government RAG system.
#
# OUTPUT
# ------------------------------------------------------------
# Final semantic score from 0 to 1.
#
# WEIGHTS
# ------------------------------------------------------------
# Groundedness          40%
# Citation correctness  25%
# Completeness          20%
# Relevance             10%
# Language quality       5%
# ============================================================

SEMANTIC_WEIGHTS = {
    "groundedness": 0.40,
    "citation_correctness": 0.25,
    "completeness": 0.20,
    "relevance": 0.10,
    "language_quality": 0.05,
}


# ============================================================
# 3. CREATE BEDROCK CLIENT
# ============================================================
# GOAL
# ------------------------------------------------------------
# Connect to Amazon Bedrock Runtime.
#
# INPUT
# ------------------------------------------------------------
# AWS credentials + AWS_REGION.
#
# OUTPUT
# ------------------------------------------------------------
# Bedrock Runtime client used by the semantic judge.
# ============================================================

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION,
)


# ============================================================
# 4. LOAD PREVIOUS RAG BENCHMARK
# ============================================================

def load_benchmark():
    """
    GOAL
    ----------------------------------------------------------
    Load the complete saved RAG benchmark.

    INPUT
    ----------------------------------------------------------
    BENCHMARK_DETAILS_PATH

    OUTPUT
    ----------------------------------------------------------
    Parsed benchmark JSON containing:
        questions
        retrieved evidence
        candidate answers
        automatic benchmark summary
    """

    if not os.path.exists(
        BENCHMARK_DETAILS_PATH
    ):
        raise FileNotFoundError(
            "Benchmark details file not found:\n"
            f"{BENCHMARK_DETAILS_PATH}\n\n"
            "Run benchmark_rag_models.py first."
        )

    with open(
        BENCHMARK_DETAILS_PATH,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


# ============================================================
# 5. SELECT FINALISTS
# ============================================================

def select_finalists(benchmark_data):
    """
    GOAL
    ----------------------------------------------------------
    Automatically select the top N models from the previous
    automatic benchmark.

    INPUT
    ----------------------------------------------------------
    benchmark_data["summary"]

    OUTPUT
    ----------------------------------------------------------
    List of finalist model IDs.

    EXAMPLE
    ----------------------------------------------------------
    With your current benchmark and NUM_FINALISTS=2:

        qwen.qwen3-32b-v1:0
        zai.glm-4.7-flash
    """

    summary = benchmark_data.get(
        "summary",
        []
    )

    if not summary:
        raise RuntimeError(
            "No model summary was found in "
            "rag_benchmark_details.json."
        )

    finalists = [
        item["model_id"]
        for item in summary[:NUM_FINALISTS]
    ]

    return finalists


# ============================================================
# 6. FIND MODEL METADATA
# ============================================================

def build_model_lookup(benchmark_data):
    """
    GOAL
    ----------------------------------------------------------
    Build a simple model_id → metadata lookup.

    INPUT
    ----------------------------------------------------------
    benchmark_data["models"]

    OUTPUT
    ----------------------------------------------------------
    Dictionary containing provider/name/model_id.
    """

    return {
        model["model_id"]: model
        for model in benchmark_data.get(
            "models",
            []
        )
    }


# ============================================================
# 7. BUILD EVIDENCE TEXT FOR THE JUDGE
# ============================================================

def build_evidence_text(sources):
    """
    GOAL
    ----------------------------------------------------------
    Reconstruct the exact numbered evidence seen by the
    candidate model.

    INPUT
    ----------------------------------------------------------
    Saved SFOE source/evidence list.

    OUTPUT
    ----------------------------------------------------------
    Numbered evidence string for the semantic judge.

    IMPORTANT
    ----------------------------------------------------------
    Projection and truncation metadata are preserved because
    the judge must detect misuse of those passages.
    """

    parts = []

    for index, source in enumerate(
        sources,
        start=1,
    ):
        number = source.get(
            "source_number",
            index,
        )

        document = (
            source.get("document")
            or source.get("title")
            or "Unknown document"
        )

        published_at = source.get(
            "published_at"
        )

        url = (
            source.get("url")
            or source.get("download_url")
        )

        score = source.get(
            "score",
            source.get(
                "retrieval_score"
            )
        )

        years_covered = source.get(
            "years_covered"
        )

        is_projection = source.get(
            "is_projection",
            False,
        )

        is_truncated = source.get(
            "is_truncated",
            False,
        )

        text = source.get(
            "text",
            "",
        )

        parts.append(
            f"""
[SOURCE {number}]
Document: {document}
Published: {published_at}
URL: {url}
Retrieval score: {score}
Years covered: {years_covered}
Projection: {is_projection}
Truncated: {is_truncated}

Passage:
{text}
"""
        )

    return "\n".join(parts)


# ============================================================
# 8. BUILD STRICT SEMANTIC-JUDGE PROMPT
# ============================================================

def build_judge_prompt(
    question,
    expected_language,
    evidence_text,
    candidate_answer,
):
    """
    GOAL
    ----------------------------------------------------------
    Ask an independent LLM judge to evaluate one candidate
    answer against the SFOE evidence.

    INPUT
    ----------------------------------------------------------
    question
    expected_language
    evidence_text
    candidate_answer

    OUTPUT
    ----------------------------------------------------------
    Strict evaluation prompt requesting JSON only.
    """

    return f"""
You are a strict evaluator for a retrieval-augmented generation
system based on official Swiss Federal Office of Energy
(SFOE/BFE) publications.

You are NOT answering the question yourself.

You are evaluating whether the CANDIDATE ANSWER is supported by
the RETRIEVED SFOE EVIDENCE.

Use ONLY the supplied evidence. Do not use external knowledge.

QUESTION LANGUAGE:
{expected_language}

QUESTION:
{question}


RETRIEVED SFOE EVIDENCE:
{evidence_text}


CANDIDATE ANSWER:
{candidate_answer}


EVALUATION RULES:

1. GROUNDEDNESS
   Check every factual claim.
   Score 5 only if essentially all factual claims are directly
   supported by the supplied evidence.
   Penalize stronger wording than the evidence supports,
   causal claims not present in the evidence, invented facts,
   unsupported generalizations and misleading interpretation.

2. CITATION CORRECTNESS
   Check citations such as [1], [2].
   A citation is correct only when the cited numbered source
   actually supports the claim attached to it.
   Do not give full credit merely because citation numbers are
   syntactically valid.

3. COMPLETENESS
   Judge whether the answer covers the most important evidence
   needed to answer the question without unnecessary detail.
   Do not penalize the answer for information that is not
   present in the retrieved evidence.

4. RELEVANCE
   Judge whether the answer directly addresses the question
   and avoids irrelevant material.

5. LANGUAGE QUALITY
   Judge whether the answer is clear, professional and written
   in the requested language.

6. PROJECTIONS
   If a source has Projection: True, forward-looking
   information must not be presented as measured historical
   fact.

7. TRUNCATED SOURCES
   If Truncated: True, claims relying on potentially incomplete
   information should be treated cautiously.

8. UNSUPPORTED CLAIMS
   List each important unsupported or overstated claim.
   If none exist, return an empty list.

9. CITATION ISSUES
   List citations that do not support the associated claim.
   If none exist, return an empty list.

10. Do not reward verbosity.

SCORING SCALE FOR EACH DIMENSION:

5 = excellent
4 = good, minor issue
3 = acceptable, meaningful issue
2 = weak
1 = very weak
0 = unusable


RETURN ONLY VALID JSON.
Do NOT use Markdown fences.
Do NOT add text before or after the JSON.

Use exactly this structure:

{{
  "groundedness": 0,
  "citation_correctness": 0,
  "completeness": 0,
  "relevance": 0,
  "language_quality": 0,
  "unsupported_claims": [
    {{
      "claim": "short description",
      "reason": "why the evidence does not support it"
    }}
  ],
  "citation_issues": [
    {{
      "citation": "[1]",
      "issue": "short description"
    }}
  ],
  "projection_or_truncation_issues": [
    "short description"
  ],
  "strengths": [
    "short description"
  ],
  "summary": "brief overall assessment"
}}
"""


# ============================================================
# 9. EXTRACT TEXT FROM BEDROCK RESPONSE
# ============================================================

def extract_generated_text(response):
    """
    GOAL
    ----------------------------------------------------------
    Extract all text blocks from a Bedrock Converse response.

    INPUT
    ----------------------------------------------------------
    Bedrock Converse response.

    OUTPUT
    ----------------------------------------------------------
    One combined text string.
    """

    content = (
        response["output"]
        ["message"]
        ["content"]
    )

    text_parts = []

    for item in content:
        if (
            isinstance(item, dict)
            and "text" in item
        ):
            text_parts.append(
                item["text"]
            )

    if not text_parts:
        raise ValueError(
            "Judge model returned no text."
        )

    return "\n".join(text_parts)


# ============================================================
# 10. ROBUSTLY EXTRACT JSON
# ============================================================

def extract_json_object(text):
    """
    GOAL
    ----------------------------------------------------------
    Parse strict JSON from the judge response.

    INPUT
    ----------------------------------------------------------
    Raw judge text.

    OUTPUT
    ----------------------------------------------------------
    Python dictionary.

    PROCESS
    ----------------------------------------------------------
    1. Try direct json.loads().
    2. Remove accidental Markdown code fences.
    3. If extra text exists, extract the first complete JSON
       object using balanced braces.
    """

    cleaned = text.strip()

    # --------------------------------------------------------
    # Direct parsing
    # --------------------------------------------------------

    try:
        return json.loads(
            cleaned
        )
    except json.JSONDecodeError:
        pass

    # --------------------------------------------------------
    # Remove accidental Markdown code fences
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Balanced-brace extraction
    # --------------------------------------------------------

    start = cleaned.find(
        "{"
    )

    if start == -1:
        raise ValueError(
            "No JSON object found in judge response."
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
                candidate = cleaned[
                    start:index + 1
                ]

                return json.loads(
                    candidate
                )

    raise ValueError(
        "Could not extract a complete JSON object "
        "from judge response."
    )


# ============================================================
# 11. VALIDATE JUDGE SCORES
# ============================================================

SCORE_FIELDS = [
    "groundedness",
    "citation_correctness",
    "completeness",
    "relevance",
    "language_quality",
]


def validate_judgement(judgement):
    """
    GOAL
    ----------------------------------------------------------
    Ensure the judge returned all required score fields and
    valid 0-5 values.

    INPUT
    ----------------------------------------------------------
    Judge JSON dictionary.

    OUTPUT
    ----------------------------------------------------------
    Cleaned/validated dictionary.
    """

    if not isinstance(
        judgement,
        dict,
    ):
        raise ValueError(
            "Judge response is not a JSON object."
        )

    for field in SCORE_FIELDS:

        if field not in judgement:
            raise ValueError(
                f"Judge response is missing '{field}'."
            )

        try:
            value = float(
                judgement[field]
            )
        except (
            TypeError,
            ValueError,
        ) as error:
            raise ValueError(
                f"Judge score '{field}' is not numeric."
            ) from error

        if not (
            0
            <= value
            <= 5
        ):
            raise ValueError(
                f"Judge score '{field}' must be between 0 and 5."
            )

        judgement[field] = value

    # Make optional list fields safe.
    for field in [
        "unsupported_claims",
        "citation_issues",
        "projection_or_truncation_issues",
        "strengths",
    ]:
        value = judgement.get(
            field,
            []
        )

        if not isinstance(
            value,
            list,
        ):
            value = [
                str(value)
            ]

        judgement[field] = value

    judgement["summary"] = str(
        judgement.get(
            "summary",
            "",
        )
    )

    return judgement


# ============================================================
# 12. CALL SEMANTIC JUDGE
# ============================================================

def judge_answer(
    question,
    language,
    sources,
    candidate_answer,
):
    """
    GOAL
    ----------------------------------------------------------
    Evaluate one candidate answer using the independent judge.

    INPUT
    ----------------------------------------------------------
    question
    language
    saved SFOE evidence
    candidate answer

    OUTPUT
    ----------------------------------------------------------
    Structured semantic evaluation dictionary.
    """

    evidence_text = build_evidence_text(
        sources
    )

    prompt = build_judge_prompt(
        question=question,
        expected_language=language,
        evidence_text=evidence_text,
        candidate_answer=candidate_answer,
    )

    response = (
        bedrock_runtime.converse(
            modelId=JUDGE_MODEL_ID,

            messages=[
                {
                    "role":
                        "user",

                    "content": [
                        {
                            "text":
                                prompt
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

    raw_text = extract_generated_text(
        response
    )

    judgement = extract_json_object(
        raw_text
    )

    return validate_judgement(
        judgement
    )


# ============================================================
# 13. CALCULATE WEIGHTED SEMANTIC SCORE
# ============================================================

def calculate_semantic_score(
    judgement
):
    """
    GOAL
    ----------------------------------------------------------
    Convert five 0-5 judge scores into one weighted 0-1 score.

    INPUT
    ----------------------------------------------------------
    Validated semantic judgement.

    OUTPUT
    ----------------------------------------------------------
    Weighted semantic score from 0 to 1.
    """

    weighted_score = 0.0

    for field, weight in (
        SEMANTIC_WEIGHTS.items()
    ):
        normalized = (
            judgement[field]
            / 5.0
        )

        weighted_score += (
            normalized
            * weight
        )

    return weighted_score


# ============================================================
# 14. EVALUATE ALL FINALIST ANSWERS
# ============================================================

def evaluate_finalists(
    benchmark_data,
    finalist_ids,
    model_lookup,
):
    """
    GOAL
    ----------------------------------------------------------
    Semantically evaluate every finalist answer for every
    benchmark question.

    INPUT
    ----------------------------------------------------------
    benchmark_data
    finalist model IDs
    model metadata lookup

    OUTPUT
    ----------------------------------------------------------
    Detailed semantic evaluation records.

    IMPORTANT
    ----------------------------------------------------------
    No candidate model is called here.
    We judge the answers that were already saved by the first
    benchmark.
    """

    detailed_results = []

    question_results = benchmark_data.get(
        "results",
        []
    )

    total_calls = (
        len(question_results)
        * len(finalist_ids)
    )

    print("\n")
    print("=" * 80)
    print(
        "SFOE SEMANTIC RAG EVALUATION"
    )
    print("=" * 80)

    print(
        f"\nJudge model : "
        f"{JUDGE_MODEL_ID}"
    )

    print(
        f"Finalists   : "
        f"{len(finalist_ids)}"
    )

    print(
        f"Questions   : "
        f"{len(question_results)}"
    )

    print(
        f"Judge calls : "
        f"{total_calls}"
    )

    if (
        JUDGE_MODEL_ID
        in finalist_ids
    ):
        print(
            "\nWARNING: The judge model is also one of the "
            "finalists. For a cleaner evaluation, choose a "
            "different JUDGE_MODEL_ID."
        )

    call_number = 0

    for question_result in (
        question_results
    ):

        question_id = (
            question_result[
                "question_id"
            ]
        )

        language = (
            question_result[
                "language"
            ]
        )

        question = (
            question_result[
                "question"
            ]
        )

        sources = (
            question_result.get(
                "sources",
                [],
            )
        )

        model_results = {
            item["model_id"]: item
            for item in question_result.get(
                "model_results",
                []
            )
        }

        print("\n")
        print("=" * 80)
        print(
            f"QUESTION {question_id}"
        )
        print("=" * 80)

        print(
            f"\n{question}"
        )

        for model_id in finalist_ids:

            model_meta = model_lookup.get(
                model_id,
                {
                    "provider":
                        "Unknown",

                    "name":
                        model_id,
                }
            )

            candidate = (
                model_results.get(
                    model_id
                )
            )

            if (
                not candidate
                or candidate.get("status")
                != "SUCCESS"
            ):
                print(
                    f"\nSkipping "
                    f"{model_meta['name']} "
                    f"(no successful saved answer)."
                )

                continue

            call_number += 1

            print(
                f"\n[{call_number}/{total_calls}] "
                f"Judging "
                f"{model_meta['provider']} | "
                f"{model_meta['name']}"
            )

            try:

                judgement = judge_answer(
                    question=question,
                    language=language,
                    sources=sources,
                    candidate_answer=
                        candidate["answer"],
                )

                semantic_score = (
                    calculate_semantic_score(
                        judgement
                    )
                )

                record = {
                    "question_id":
                        question_id,

                    "language":
                        language,

                    "question":
                        question,

                    "provider":
                        model_meta[
                            "provider"
                        ],

                    "model_name":
                        model_meta[
                            "name"
                        ],

                    "model_id":
                        model_id,

                    "candidate_answer":
                        candidate[
                            "answer"
                        ],

                    "previous_auto_score":
                        candidate.get(
                            "overall_score"
                        ),

                    "candidate_latency":
                        candidate.get(
                            "latency"
                        ),

                    "semantic_score":
                        semantic_score,

                    **judgement,
                }

                detailed_results.append(
                    record
                )

                print(
                    f"   Semantic score      : "
                    f"{semantic_score:.3f}"
                )

                print(
                    f"   Groundedness        : "
                    f"{judgement['groundedness']:.1f}/5"
                )

                print(
                    f"   Citation correctness: "
                    f"{judgement['citation_correctness']:.1f}/5"
                )

                print(
                    f"   Completeness        : "
                    f"{judgement['completeness']:.1f}/5"
                )

                print(
                    f"   Unsupported claims  : "
                    f"{len(judgement['unsupported_claims'])}"
                )

                print(
                    f"   Citation issues     : "
                    f"{len(judgement['citation_issues'])}"
                )

            except Exception as error:

                print(
                    "   FAILED"
                )

                print(
                    f"   {error}"
                )

                detailed_results.append(
                    {
                        "question_id":
                            question_id,

                        "language":
                            language,

                        "question":
                            question,

                        "provider":
                            model_meta[
                                "provider"
                            ],

                        "model_name":
                            model_meta[
                                "name"
                            ],

                        "model_id":
                            model_id,

                        "candidate_answer":
                            candidate[
                                "answer"
                            ],

                        "previous_auto_score":
                            candidate.get(
                                "overall_score"
                            ),

                        "candidate_latency":
                            candidate.get(
                                "latency"
                            ),

                        "semantic_score":
                            None,

                        "error":
                            str(error),
                    }
                )

    return detailed_results


# ============================================================
# 15. BUILD FINAL SEMANTIC SUMMARY
# ============================================================

def build_semantic_summary(
    detailed_results,
    benchmark_data,
):
    """
    GOAL
    ----------------------------------------------------------
    Aggregate semantic results by model.

    INPUT
    ----------------------------------------------------------
    Detailed semantic evaluations.
    Previous benchmark data.

    OUTPUT
    ----------------------------------------------------------
    Finalist-level semantic ranking.

    TIEBREAK
    ----------------------------------------------------------
    If semantic scores are equal, lower previous average
    latency ranks first.
    """

    previous_summary_lookup = {
        item["model_id"]: item
        for item in benchmark_data.get(
            "summary",
            []
        )
    }

    grouped = {}

    for result in detailed_results:

        if (
            result.get(
                "semantic_score"
            )
            is None
        ):
            continue

        model_id = result[
            "model_id"
        ]

        grouped.setdefault(
            model_id,
            [],
        ).append(
            result
        )

    summaries = []

    for model_id, results in (
        grouped.items()
    ):

        previous = (
            previous_summary_lookup.get(
                model_id,
                {}
            )
        )

        unsupported_count = sum(
            len(
                item.get(
                    "unsupported_claims",
                    [],
                )
            )
            for item in results
        )

        citation_issue_count = sum(
            len(
                item.get(
                    "citation_issues",
                    [],
                )
            )
            for item in results
        )

        projection_issue_count = sum(
            len(
                item.get(
                    "projection_or_truncation_issues",
                    [],
                )
            )
            for item in results
        )

        summary = {
            "provider":
                results[0][
                    "provider"
                ],

            "model_name":
                results[0][
                    "model_name"
                ],

            "model_id":
                model_id,

            "semantic_score":
                statistics.mean(
                    item[
                        "semantic_score"
                    ]
                    for item in results
                ),

            "groundedness":
                statistics.mean(
                    item[
                        "groundedness"
                    ]
                    for item in results
                ),

            "citation_correctness":
                statistics.mean(
                    item[
                        "citation_correctness"
                    ]
                    for item in results
                ),

            "completeness":
                statistics.mean(
                    item[
                        "completeness"
                    ]
                    for item in results
                ),

            "relevance":
                statistics.mean(
                    item[
                        "relevance"
                    ]
                    for item in results
                ),

            "language_quality":
                statistics.mean(
                    item[
                        "language_quality"
                    ]
                    for item in results
                ),

            "unsupported_claim_count":
                unsupported_count,

            "citation_issue_count":
                citation_issue_count,

            "projection_or_truncation_issue_count":
                projection_issue_count,

            "questions_evaluated":
                len(results),

            "previous_auto_score":
                previous.get(
                    "average_score"
                ),

            "previous_avg_latency":
                previous.get(
                    "average_latency"
                ),
        }

        summaries.append(
            summary
        )

    summaries.sort(
        key=lambda item: (
            item["semantic_score"],
            -item["unsupported_claim_count"],
            -item["citation_issue_count"],
            -(
                item["previous_avg_latency"]
                if item[
                    "previous_avg_latency"
                ]
                is not None
                else 9999
            ),
        ),
        reverse=True,
    )

    return summaries


# ============================================================
# 16. SAVE DETAILED JSON
# ============================================================

def save_details_json(
    finalist_ids,
    detailed_results,
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Preserve all semantic evaluations and judge explanations.

    OUTPUT
    ----------------------------------------------------------
    semantic_evaluation_details.json
    """

    os.makedirs(
        OUTPUT_DIRECTORY,
        exist_ok=True,
    )

    path = os.path.join(
        OUTPUT_DIRECTORY,
        "semantic_evaluation_details.json",
    )

    payload = {
        "judge_model_id":
            JUDGE_MODEL_ID,

        "semantic_weights":
            SEMANTIC_WEIGHTS,

        "finalist_model_ids":
            finalist_ids,

        "source_benchmark_file":
            BENCHMARK_DETAILS_PATH,

        "results":
            detailed_results,

        "summary":
            summaries,
    }

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            indent=2,
            ensure_ascii=False,
        )

    return path


# ============================================================
# 17. SAVE QUESTION-LEVEL CSV
# ============================================================

def save_scores_csv(
    detailed_results,
):
    """
    GOAL
    ----------------------------------------------------------
    Save one row per finalist/question semantic evaluation.

    OUTPUT
    ----------------------------------------------------------
    semantic_evaluation_scores.csv
    """

    os.makedirs(
        OUTPUT_DIRECTORY,
        exist_ok=True,
    )

    path = os.path.join(
        OUTPUT_DIRECTORY,
        "semantic_evaluation_scores.csv",
    )

    fields = [
        "question_id",
        "language",
        "question",
        "provider",
        "model_name",
        "model_id",
        "semantic_score",
        "groundedness",
        "citation_correctness",
        "completeness",
        "relevance",
        "language_quality",
        "unsupported_claim_count",
        "citation_issue_count",
        "projection_or_truncation_issue_count",
        "previous_auto_score",
        "candidate_latency",
        "summary",
        "candidate_answer",
        "error",
    ]

    with open(
        path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )

        writer.writeheader()

        for item in detailed_results:

            writer.writerow(
                {
                    "question_id":
                        item.get(
                            "question_id"
                        ),

                    "language":
                        item.get(
                            "language"
                        ),

                    "question":
                        item.get(
                            "question"
                        ),

                    "provider":
                        item.get(
                            "provider"
                        ),

                    "model_name":
                        item.get(
                            "model_name"
                        ),

                    "model_id":
                        item.get(
                            "model_id"
                        ),

                    "semantic_score":
                        item.get(
                            "semantic_score"
                        ),

                    "groundedness":
                        item.get(
                            "groundedness"
                        ),

                    "citation_correctness":
                        item.get(
                            "citation_correctness"
                        ),

                    "completeness":
                        item.get(
                            "completeness"
                        ),

                    "relevance":
                        item.get(
                            "relevance"
                        ),

                    "language_quality":
                        item.get(
                            "language_quality"
                        ),

                    "unsupported_claim_count":
                        len(
                            item.get(
                                "unsupported_claims",
                                [],
                            )
                        ),

                    "citation_issue_count":
                        len(
                            item.get(
                                "citation_issues",
                                [],
                            )
                        ),

                    "projection_or_truncation_issue_count":
                        len(
                            item.get(
                                "projection_or_truncation_issues",
                                [],
                            )
                        ),

                    "previous_auto_score":
                        item.get(
                            "previous_auto_score"
                        ),

                    "candidate_latency":
                        item.get(
                            "candidate_latency"
                        ),

                    "summary":
                        item.get(
                            "summary"
                        ),

                    "candidate_answer":
                        item.get(
                            "candidate_answer"
                        ),

                    "error":
                        item.get(
                            "error"
                        ),
                }
            )

    return path


# ============================================================
# 18. SAVE FINAL SUMMARY CSV
# ============================================================

def save_summary_csv(
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Save the final semantic model ranking.

    OUTPUT
    ----------------------------------------------------------
    semantic_evaluation_summary.csv
    """

    os.makedirs(
        OUTPUT_DIRECTORY,
        exist_ok=True,
    )

    path = os.path.join(
        OUTPUT_DIRECTORY,
        "semantic_evaluation_summary.csv",
    )

    if not summaries:
        return path

    with open(
        path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=list(
                summaries[0].keys()
            ),
        )

        writer.writeheader()

        writer.writerows(
            summaries
        )

    return path


# ============================================================
# 19. PRINT FINAL SEMANTIC RANKING
# ============================================================

def print_ranking(
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Present the final semantic comparison clearly.

    INPUT
    ----------------------------------------------------------
    Aggregated finalist summaries.

    OUTPUT
    ----------------------------------------------------------
    Semantic ranking + recommended model.
    """

    print("\n")
    print("=" * 80)
    print(
        "FINAL SEMANTIC RAG RANKING"
    )
    print("=" * 80)

    if not summaries:

        print(
            "\nNo successful semantic "
            "evaluations were produced."
        )

        return

    for rank, model in enumerate(
        summaries,
        start=1,
    ):

        print(
            f"\n{rank}. "
            f"{model['provider']} - "
            f"{model['model_name']}"
        )

        print(
            f"   Model ID                 : "
            f"{model['model_id']}"
        )

        print(
            f"   Semantic score           : "
            f"{model['semantic_score']:.3f}"
        )

        print(
            f"   Groundedness             : "
            f"{model['groundedness']:.2f}/5"
        )

        print(
            f"   Citation correctness     : "
            f"{model['citation_correctness']:.2f}/5"
        )

        print(
            f"   Completeness             : "
            f"{model['completeness']:.2f}/5"
        )

        print(
            f"   Relevance                : "
            f"{model['relevance']:.2f}/5"
        )

        print(
            f"   Language quality         : "
            f"{model['language_quality']:.2f}/5"
        )

        print(
            f"   Unsupported claims       : "
            f"{model['unsupported_claim_count']}"
        )

        print(
            f"   Citation issues          : "
            f"{model['citation_issue_count']}"
        )

        print(
            f"   Projection/trunc. issues : "
            f"{model['projection_or_truncation_issue_count']}"
        )

        previous_auto = (
            model.get(
                "previous_auto_score"
            )
        )

        if (
            previous_auto
            is not None
        ):
            print(
                f"   Previous auto score      : "
                f"{previous_auto:.3f}"
            )

        previous_latency = (
            model.get(
                "previous_avg_latency"
            )
        )

        if (
            previous_latency
            is not None
        ):
            print(
                f"   Previous avg. latency    : "
                f"{previous_latency:.2f} s"
            )

    winner = summaries[0]

    print("\n")
    print("=" * 80)
    print(
        "SEMANTICALLY RECOMMENDED MODEL"
    )
    print("=" * 80)

    print(
        f"\n{winner['provider']} - "
        f"{winner['model_name']}"
    )

    print(
        "\nModel ID:"
    )

    print(
        winner["model_id"]
    )

    print(
        "\nIf you accept this result, set:"
    )

    print(
        f"BEDROCK_MODEL_ID="
        f"{winner['model_id']}"
    )

    print(
        "\nIMPORTANT:"
    )

    print(
        "This is stronger than the first lexical/proxy "
        "benchmark, but it is still an LLM-as-a-judge "
        "evaluation. For a production-grade evaluation, "
        "human-reviewed reference answers and/or a second "
        "independent judge would further improve reliability."
    )


# ============================================================
# 20. MAIN
# ============================================================

def main():
    """
    GOAL
    ----------------------------------------------------------
    Run the complete semantic evaluation.

    INPUT
    ----------------------------------------------------------
    rag_benchmark_details.json

    PROCESS
    ----------------------------------------------------------
    1. Load previous benchmark.
    2. Select top N finalists automatically.
    3. Use saved evidence and saved candidate answers.
    4. Judge each answer semantically.
    5. Calculate weighted scores.
    6. Aggregate by model.
    7. Save JSON and CSV results.
    8. Print final semantic ranking.

    OUTPUT
    ----------------------------------------------------------
    benchmark_results/
        semantic_evaluation_details.json
        semantic_evaluation_scores.csv
        semantic_evaluation_summary.csv
    """

    try:

        benchmark_data = (
            load_benchmark()
        )

        finalist_ids = (
            select_finalists(
                benchmark_data
            )
        )

        model_lookup = (
            build_model_lookup(
                benchmark_data
            )
        )

        print(
            "\nSelected finalists:"
        )

        for model_id in finalist_ids:

            model = model_lookup.get(
                model_id,
                {}
            )

            print(
                f" - "
                f"{model.get('provider', '')} "
                f"{model.get('name', model_id)}"
            )

        detailed_results = (
            evaluate_finalists(
                benchmark_data=
                    benchmark_data,

                finalist_ids=
                    finalist_ids,

                model_lookup=
                    model_lookup,
            )
        )

        summaries = (
            build_semantic_summary(
                detailed_results=
                    detailed_results,

                benchmark_data=
                    benchmark_data,
            )
        )

        details_path = (
            save_details_json(
                finalist_ids=
                    finalist_ids,

                detailed_results=
                    detailed_results,

                summaries=
                    summaries,
            )
        )

        scores_path = (
            save_scores_csv(
                detailed_results
            )
        )

        summary_path = (
            save_summary_csv(
                summaries
            )
        )

        print_ranking(
            summaries
        )

        print("\n")
        print("=" * 80)
        print(
            "RESULT FILES"
        )
        print("=" * 80)

        print(
            f"\nDetails : "
            f"{details_path}"
        )

        print(
            f"Scores  : "
            f"{scores_path}"
        )

        print(
            f"Summary : "
            f"{summary_path}"
        )

    except (
        ClientError,
        BotoCoreError,
    ) as error:

        print(
            "\nAWS Bedrock error:"
        )

        print(
            error
        )

    except Exception as error:

        print(
            "\nUnexpected error:"
        )

        print(
            error
        )


# ============================================================
# 21. START SCRIPT
# ============================================================
# INPUT
# ------------------------------------------------------------
# Run:
#
#     python evaluate_rag_semantic.py
#
# OUTPUT
# ------------------------------------------------------------
# Semantic evaluation of the top finalist RAG models.
# ============================================================

if __name__ == "__main__":
    main()
