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
# INPUT
# ------------------------------------------------------------
# benchmark_results/rag_benchmark_details.json
#
# This script does NOT retrieve SFOE documents again and does
# NOT ask the candidate models to generate new answers.
#
# It evaluates the SAVED candidate answers against the EXACT
# SAVED SFOE evidence used in the first-stage benchmark.
#
# MAIN PROCESS
# ------------------------------------------------------------
# 1. Load the first-stage benchmark.
# 2. Select the top N finalist models.
# 3. Reuse the saved evidence and candidate answers.
# 4. Ask an independent Bedrock judge to evaluate each answer.
# 5. Score:
#       - groundedness
#       - citation correctness
#       - completeness
#       - numerical accuracy
#       - abstention quality
#       - relevance
#       - language quality
# 6. Combine semantic quality (95%) with latency (5%).
# 7. Report CORE / EXTENDED / STRESS quality separately.
# 8. Save detailed JSON and CSV files.
#
# OUTPUT
# ------------------------------------------------------------
# benchmark_results/
#     semantic_evaluation_details.json
#     semantic_evaluation_scores.csv
#     semantic_evaluation_summary.csv
# ============================================================


# ============================================================
# 1. LOAD CONFIGURATION
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
        "3",
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

# Claim-by-claim evaluation produces more JSON than the old
# prompt, so a larger output limit helps avoid truncated JSON.
MAX_JUDGE_TOKENS = int(
    os.getenv(
        "MAX_JUDGE_TOKENS",
        "3000",
    )
)


# ============================================================
# 2. SCORING CONFIGURATION
# ============================================================
# GOAL
# ------------------------------------------------------------
# Keep answer quality dominant:
#
#   95% semantic quality
#    5% response latency
#
# The semantic weights intentionally sum to 0.95.
# ============================================================

SEMANTIC_WEIGHTS = {
    "groundedness": 0.30,
    "citation_correctness": 0.20,
    "completeness": 0.15,
    "numerical_accuracy": 0.10,
    "abstention_quality": 0.10,
    "relevance": 0.05,
    "language_quality": 0.05,
}

SEMANTIC_WEIGHT_TOTAL = sum(SEMANTIC_WEIGHTS.values())

LATENCY_WEIGHT = 0.05

SCORE_FIELDS = list(SEMANTIC_WEIGHTS.keys())


# ============================================================
# 3. CREATE BEDROCK CLIENT
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
    Load the complete saved first-stage RAG benchmark.

    INPUT
    ----------------------------------------------------------
    BENCHMARK_DETAILS_PATH

    OUTPUT
    ----------------------------------------------------------
    Parsed benchmark JSON containing questions, models,
    retrieved evidence, candidate answers and first-stage
    automatic scores.
    """

    if not os.path.exists(BENCHMARK_DETAILS_PATH):
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


def select_finalists(
    benchmark_data,
):
    """
    GOAL
    ----------------------------------------------------------
    Select the top N models from the first-stage benchmark.

    INPUT
    ----------------------------------------------------------
    benchmark_data["summary"]
    NUM_FINALISTS

    OUTPUT
    ----------------------------------------------------------
    List of finalist model IDs.
    """

    summary = benchmark_data.get(
        "summary",
        [],
    )

    if not summary:
        raise RuntimeError("No model summary was found in rag_benchmark_details.json.")

    return [item["model_id"] for item in summary[:NUM_FINALISTS]]


# ============================================================
# 6. BUILD MODEL / QUESTION LOOKUPS
# ============================================================


def build_model_lookup(
    benchmark_data,
):
    """
    GOAL
    ----------------------------------------------------------
    Build model_id -> model metadata.
    """

    return {
        model["model_id"]: model
        for model in benchmark_data.get(
            "models",
            [],
        )
    }


def build_question_lookup(
    benchmark_data,
):
    """
    GOAL
    ----------------------------------------------------------
    Build question_id -> question metadata.

    WHY
    ----------------------------------------------------------
    The saved first-stage result rows may not contain the
    question group/category. We recover that metadata from the
    original benchmark question definitions.
    """

    return {
        question["id"]: question
        for question in benchmark_data.get(
            "questions",
            [],
        )
        if "id" in question
    }


def infer_question_group(
    question_id,
    question_meta,
):
    """
    GOAL
    ----------------------------------------------------------
    Return a stable group name:
        core
        extended
        stress

    INPUT
    ----------------------------------------------------------
    question_id + question metadata.

    OUTPUT
    ----------------------------------------------------------
    Lower-case group name.
    """

    explicit_group = (
        str(
            question_meta.get(
                "group",
                "",
            )
        )
        .strip()
        .lower()
    )

    if explicit_group:
        return explicit_group

    upper_id = str(question_id).upper()

    if upper_id.startswith("CORE_"):
        return "core"

    if upper_id.startswith("EXT_"):
        return "extended"

    if upper_id.startswith("STRESS_"):
        return "stress"

    return "other"


# ============================================================
# 7. BUILD EVIDENCE TEXT FOR THE JUDGE
# ============================================================


def build_evidence_text(
    sources,
):
    """
    GOAL
    ----------------------------------------------------------
    Reconstruct the numbered SFOE evidence saved by the
    first-stage benchmark.

    INPUT
    ----------------------------------------------------------
    Saved source/evidence list.

    OUTPUT
    ----------------------------------------------------------
    Numbered evidence string for the semantic judge.

    IMPORTANT
    ----------------------------------------------------------
    Projection and truncation metadata are preserved.
    """

    parts = []

    for index, source in enumerate(
        sources,
        start=1,
    ):
        number = source.get(
            "source_number",
            source.get(
                "citation_number",
                index,
            ),
        )

        document = source.get("document") or source.get("title") or "Unknown document"

        published_at = source.get("published_at")

        url = source.get("url") or source.get("download_url")

        score = source.get(
            "score",
            source.get("retrieval_score"),
        )

        years_covered = source.get("years_covered")

        bases = source.get("bases")

        is_projection = source.get(
            "is_projection",
            False,
        )

        is_truncated = source.get(
            "is_truncated",
            False,
        )

        truncation_reasons = source.get("truncation_reasons")

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
Bases: {bases}
Projection: {is_projection}
Truncated: {is_truncated}
Truncation reasons: {truncation_reasons}

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
    Build one strict, identical judge template for every
    candidate answer.

    INPUT
    ----------------------------------------------------------
    question:
        Original benchmark question.

    expected_language:
        Expected answer language.

    evidence_text:
        Exact SFOE evidence given to the candidate.

    candidate_answer:
        Candidate model's saved answer.

    OUTPUT
    ----------------------------------------------------------
    Prompt asking the judge to evaluate:
        - claim-level grounding
        - citation entailment
        - completeness
        - numerical accuracy
        - abstention quality
        - relevance
        - language
        - projection/truncation misuse
    """

    return f"""
You are a STRICT and SKEPTICAL evaluator of a
retrieval-augmented generation system based on official
Swiss Federal Office of Energy (SFOE/BFE) publications.

You are NOT answering the user's question.

Your task is ONLY to evaluate whether the CANDIDATE ANSWER is
correctly supported by the RETRIEVED SFOE EVIDENCE.

Use ONLY the evidence provided below.

Do NOT use external knowledge.
Do NOT assume that a statement is correct merely because it
sounds plausible.

============================================================
QUESTION LANGUAGE
============================================================

{expected_language}


============================================================
QUESTION
============================================================

{question}


============================================================
RETRIEVED SFOE EVIDENCE
============================================================

{evidence_text}


============================================================
CANDIDATE ANSWER
============================================================

{candidate_answer}


============================================================
MANDATORY EVALUATION PROCESS
============================================================

Before assigning scores, internally perform the following
checks.


STEP 1 — IDENTIFY FACTUAL CLAIMS

Break the candidate answer into its important factual claims.

Examples include:

- numerical values
- percentages
- dates
- policy targets
- historical developments
- current conditions
- future developments
- causal relationships
- comparisons
- statements about the role of a technology
- statements about security, adequacy, reliability or stability


STEP 2 — VERIFY EACH CLAIM

For every important factual claim, classify it as:

DIRECTLY_SUPPORTED
    The retrieved evidence clearly states or entails the claim.

PARTIALLY_SUPPORTED
    The evidence supports the general idea, but the candidate
    uses stronger, broader, more certain or more specific
    wording than the source.

UNSUPPORTED
    The supplied evidence does not support the claim.

CONTRADICTED
    The supplied evidence conflicts with the claim.


IMPORTANT:

Semantic similarity alone is NOT enough.

Example:

Evidence:
    "Hydropower helps manage critical supply situations."

Candidate:
    "Hydropower ensures grid stability."

These are related concepts, but they are not equivalent.

Unless the evidence explicitly supports grid stability, the
candidate statement must NOT be classified as fully supported.


STEP 3 — VERIFY CITATIONS

For each citation such as:

    [1]
    [2]
    [1][3]

check whether THAT PARTICULAR source supports the factual claim
associated with the citation.

Do NOT give full citation credit merely because:

- citations are present
- citation numbers are valid
- the source discusses the same general topic

The cited source must actually support the associated claim.


STEP 4 — VERIFY NUMBERS

Carefully check every numerical statement, including:

- percentages
- years
- production values
- demand values
- installed capacities
- targets
- ranges
- quantities
- units

Check both the VALUE and its MEANING.

Be especially careful not to confuse:

- electricity production vs electricity demand
- installed capacity vs annual production
- historical observation vs future target
- measured value vs estimated value
- national total vs individual plant value
- current value vs scenario/projection


STEP 5 — CHECK PROJECTIONS

If a source has:

    Projection: True

future-looking information must NOT be presented as an
observed historical fact.

Appropriate wording may include:

    expected
    projected
    planned
    target
    scenario
    future


STEP 6 — CHECK TRUNCATED SOURCES

If a source has:

    Truncated: True

the passage may be incomplete.

The candidate should not make a strong conclusion that depends
on information that may be missing from the passage.


STEP 7 — CHECK INSUFFICIENT EVIDENCE / ABSTENTION

Determine whether the supplied evidence is sufficient to answer
the question.

If the evidence is insufficient, a trustworthy candidate should
explicitly state this limitation.

Example:

Question:
    "What was the exact electricity production of every
    individual hydropower plant in Switzerland in 2024?"

Evidence:
    Only national totals or general hydropower information.

Correct behaviour:
    Explain that the retrieved evidence does not contain the
    required plant-level values.

Incorrect behaviour:
    Guess, infer or fabricate individual plant values.


============================================================
SCORING RULES
============================================================

Score each dimension from 0 to 5.

A score of 5 must be difficult to obtain.


------------------------------------------------------------
1. GROUNDEDNESS
------------------------------------------------------------

5 =
Every important factual claim is directly supported by the
retrieved evidence.

No meaningful hallucination, overstatement, unsupported
inference or misleading interpretation is identifiable.

4 =
Generally well grounded, but there is one minor imprecision,
overstatement or weakly supported formulation.

3 =
At least one meaningful factual claim is unsupported,
overstated or insufficiently supported, but the main answer is
still mostly grounded.

2 =
Several important claims are unsupported or substantially
stronger than the evidence.

1 =
Most important claims are unsupported.

0 =
The answer is fundamentally invented, contradicted or
unsupported.


------------------------------------------------------------
2. CITATION CORRECTNESS
------------------------------------------------------------

5 =
Every important factual claim is appropriately cited and each
cited source supports the associated claim.

4 =
One minor citation issue.

3 =
At least one meaningful claim has an incorrect, weak or missing
citation.

2 =
Several meaningful citation problems.

1 =
Most citations are misleading or fail to support the claims.

0 =
Citations are absent or fundamentally unusable.


------------------------------------------------------------
3. COMPLETENESS
------------------------------------------------------------

5 =
The answer covers all major evidence needed to answer the
question while remaining focused.

4 =
One minor relevant point is missing.

3 =
One meaningful aspect of the question is not addressed.

2 =
Important parts of the question are unanswered.

1 =
The answer is very incomplete.

0 =
The answer does not meaningfully answer the question.

IMPORTANT:

Do NOT penalize the candidate for failing to provide information
that is not available in the retrieved evidence.


------------------------------------------------------------
4. NUMERICAL ACCURACY
------------------------------------------------------------

Evaluate all numerical statements, their units and their
interpretation.

5 =
All numerical statements are correct and interpreted correctly.

If the question requires numbers but the evidence does not
contain them, correctly stating that the evidence is
insufficient also deserves 5.

If the question does not require numbers and the answer makes no
numerical claims, score 5.

4 =
One minor numerical imprecision.

3 =
One meaningful numerical error or interpretation problem.

2 =
Several numerical problems.

1 =
Most numerical information is unreliable.

0 =
Numbers are fabricated or fundamentally incorrect.


------------------------------------------------------------
5. ABSTENTION QUALITY
------------------------------------------------------------

Evaluate whether the candidate correctly understands the limits
of the available evidence.

5 =
The candidate answers supported parts confidently and clearly
limits, qualifies or refuses unsupported parts when necessary.

4 =
Generally well calibrated, with one minor confidence issue.

3 =
Some unnecessary uncertainty or one meaningful unsupported
inference.

2 =
The candidate meaningfully fails to recognize evidence
limitations.

1 =
The candidate confidently provides substantial unsupported
information OR refuses an answer that is clearly supported.

0 =
The candidate fabricates information when the supplied evidence
clearly cannot answer the question.


------------------------------------------------------------
6. RELEVANCE
------------------------------------------------------------

5 =
Directly answers the question with essentially no irrelevant
content.

4 =
Mostly direct, with minor unnecessary information.

3 =
Some noticeable irrelevant or indirect material.

2 =
Significant irrelevant content.

1 =
Mostly off-topic.

0 =
Does not address the question.


------------------------------------------------------------
7. LANGUAGE QUALITY
------------------------------------------------------------

5 =
Clear, professional, natural and easy to understand in the
requested language.

4 =
One or more minor style or language issues.

3 =
Understandable but with noticeable language or writing
problems.

2 =
Difficult to follow.

1 =
Poor language quality.

0 =
Wrong language or unusable.


============================================================
ANTI-INFLATION RULE
============================================================

Do NOT automatically assign 5.

A score of 5 means:

    "I cannot identify any meaningful flaw in this dimension."

If you identify a genuine issue, that dimension should normally
receive 4 or lower.

Be skeptical.

Do not reward answers simply because they sound polished.

Evidence support is more important than writing confidence.


============================================================
CLAIM CHECK OUTPUT
============================================================

For every IMPORTANT factual claim, include a claim check.

Allowed status values:

    directly_supported
    partially_supported
    unsupported
    contradicted

Keep each reason concise.


============================================================
RETURN FORMAT
============================================================

Return ONLY valid JSON.

Do NOT use Markdown code fences.
Do NOT write anything before or after the JSON.

Return this structure:

{{
  "claim_checks": [
    {{
      "claim": "important factual claim",
      "status": "directly_supported",
      "supporting_sources": ["[1]"],
      "reason": "brief explanation"
    }}
  ],

  "groundedness": 0,
  "citation_correctness": 0,
  "completeness": 0,
  "numerical_accuracy": 0,
  "abstention_quality": 0,
  "relevance": 0,
  "language_quality": 0,

  "unsupported_claims": [
    {{
      "claim": "unsupported or overstated claim",
      "reason": "why the supplied evidence does not support it"
    }}
  ],

  "citation_issues": [
    {{
      "citation": "[1]",
      "issue": "why the cited source does not support the claim"
    }}
  ],

  "projection_or_truncation_issues": [
    "description of any projection or truncation misuse"
  ],

  "strengths": [
    "important strength of the candidate answer"
  ],

  "summary":
    "brief critical assessment explaining the assigned scores"
}}
"""


# ============================================================
# 9. EXTRACT TEXT FROM BEDROCK RESPONSE
# ============================================================


def extract_generated_text(
    response,
):
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

    content = response["output"]["message"]["content"]

    text_parts = []

    for item in content:
        if (
            isinstance(
                item,
                dict,
            )
            and "text" in item
        ):
            text_parts.append(item["text"])

    if not text_parts:
        raise ValueError("Judge model returned no text.")

    return "\n".join(text_parts)


# ============================================================
# 10. ROBUSTLY EXTRACT JSON
# ============================================================


def extract_json_object(
    text,
):
    """
    GOAL
    ----------------------------------------------------------
    Parse JSON from the judge response robustly.

    INPUT
    ----------------------------------------------------------
    Raw judge text.

    OUTPUT
    ----------------------------------------------------------
    Python dictionary.

    MAIN PROCESS
    ----------------------------------------------------------
    1. Try direct json.loads().
    2. Remove accidental Markdown code fences.
    3. Extract the first balanced JSON object if extra text
       exists.
    """

    cleaned = text.strip()

    try:
        return json.loads(cleaned)
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
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")

    if start == -1:
        raise ValueError("No JSON object found in judge response.")

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

        if char == "\\" and in_string:
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
                candidate = cleaned[start : index + 1]

                return json.loads(candidate)

    raise ValueError("Could not extract a complete JSON object from judge response.")


# ============================================================
# 11. VALIDATE JUDGE OUTPUT
# ============================================================


def validate_judgement(
    judgement,
):
    """
    GOAL
    ----------------------------------------------------------
    Ensure all required 0-5 scores exist and make optional list
    fields safe.

    INPUT
    ----------------------------------------------------------
    Judge JSON dictionary.

    OUTPUT
    ----------------------------------------------------------
    Cleaned and validated dictionary.
    """

    if not isinstance(
        judgement,
        dict,
    ):
        raise ValueError("Judge response is not a JSON object.")

    for field in SCORE_FIELDS:
        if field not in judgement:
            raise ValueError(f"Judge response is missing '{field}'.")

        try:
            value = float(judgement[field])
        except (
            TypeError,
            ValueError,
        ) as error:
            raise ValueError(f"Judge score '{field}' is not numeric.") from error

        if not (0 <= value <= 5):
            raise ValueError(f"Judge score '{field}' must be between 0 and 5.")

        judgement[field] = value

    for field in [
        "claim_checks",
        "unsupported_claims",
        "citation_issues",
        "projection_or_truncation_issues",
        "strengths",
    ]:
        value = judgement.get(
            field,
            [],
        )

        if not isinstance(
            value,
            list,
        ):
            value = [value]

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
    Evaluate one saved candidate answer with the independent
    Bedrock judge.

    INPUT
    ----------------------------------------------------------
    question
    expected language
    saved SFOE evidence
    candidate answer

    OUTPUT
    ----------------------------------------------------------
    Structured semantic judgement.
    """

    language_names = {
        "en": "English",
        "de": "German",
        "fr": "French",
    }

    expected_language = language_names.get(
        language,
        language,
    )

    evidence_text = build_evidence_text(sources)

    prompt = build_judge_prompt(
        question=question,
        expected_language=expected_language,
        evidence_text=evidence_text,
        candidate_answer=candidate_answer,
    )

    response = bedrock_runtime.converse(
        modelId=JUDGE_MODEL_ID,
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
            "maxTokens": MAX_JUDGE_TOKENS,
            "temperature": 0,
        },
    )

    raw_text = extract_generated_text(response)

    judgement = extract_json_object(raw_text)

    return validate_judgement(judgement)


# ============================================================
# 13. CALCULATE LATENCY SCORE
# ============================================================


def calculate_latency_score(
    latency,
):
    """
    GOAL
    ----------------------------------------------------------
    Convert candidate response time into a normalized 0-1
    operational score.

    INPUT
    ----------------------------------------------------------
    latency:
        Candidate response time saved by the first benchmark.

    OUTPUT
    ----------------------------------------------------------
    Score from 0 to 1.

    NOTE
    ----------------------------------------------------------
    Latency has only 5% weight in the final score.
    """

    if latency is None:
        return 0.0

    try:
        latency = float(latency)
    except (
        TypeError,
        ValueError,
    ):
        return 0.0

    if latency <= 1:
        return 1.0

    if latency <= 2:
        return 0.8

    if latency <= 4:
        return 0.6

    if latency <= 8:
        return 0.4

    return 0.2


# ============================================================
# 14. CALCULATE SEMANTIC + FINAL SCORES
# ============================================================


def calculate_scores(
    judgement,
    latency,
):
    """
    GOAL
    ----------------------------------------------------------
    Calculate:
        1. pure semantic quality
        2. latency score
        3. final combined score

    INPUT
    ----------------------------------------------------------
    judgement:
        Judge scores from 0 to 5.

    latency:
        Candidate model latency from benchmark_rag_models.py.

    OUTPUT
    ----------------------------------------------------------
    semantic_quality_score:
        Pure semantic answer quality from 0 to 1.

    latency_score:
        Operational latency score from 0 to 1.

    final_score:
        95% semantic quality contribution + 5% latency.
    """

    weighted_quality = 0.0

    for (
        field,
        weight,
    ) in SEMANTIC_WEIGHTS.items():
        normalized_score = judgement[field] / 5.0

        weighted_quality += normalized_score * weight

    semantic_quality_score = weighted_quality / SEMANTIC_WEIGHT_TOTAL

    latency_score = calculate_latency_score(latency)

    final_score = weighted_quality + LATENCY_WEIGHT * latency_score

    return {
        "semantic_quality_score": semantic_quality_score,
        "latency_score": latency_score,
        "final_score": final_score,
    }


# ============================================================
# 15. EVALUATE ALL FINALIST ANSWERS
# ============================================================


def evaluate_finalists(
    benchmark_data,
    finalist_ids,
    model_lookup,
    question_lookup,
):
    """
    GOAL
    ----------------------------------------------------------
    Semantically evaluate every finalist answer for every
    benchmark question.

    INPUT
    ----------------------------------------------------------
    benchmark_data
    finalist IDs
    model metadata lookup
    question metadata lookup

    OUTPUT
    ----------------------------------------------------------
    Detailed semantic evaluation records.

    IMPORTANT
    ----------------------------------------------------------
    Candidate models are NOT called here.
    """

    detailed_results = []

    question_results = benchmark_data.get(
        "results",
        [],
    )

    total_calls = len(question_results) * len(finalist_ids)

    print("\n")
    print("=" * 80)
    print("SFOE SEMANTIC RAG EVALUATION")
    print("=" * 80)

    print(f"\nJudge model : {JUDGE_MODEL_ID}")

    print(f"Finalists   : {len(finalist_ids)}")

    print(f"Questions   : {len(question_results)}")

    print(f"Judge calls : {total_calls}")

    if JUDGE_MODEL_ID in finalist_ids:
        print(
            "\nWARNING: The judge model is also one of the "
            "finalists. For a cleaner evaluation, choose a "
            "different JUDGE_MODEL_ID."
        )

    call_number = 0

    for question_result in question_results:
        question_id = question_result["question_id"]

        language = question_result["language"]

        question = question_result["question"]

        question_meta = question_lookup.get(
            question_id,
            {},
        )

        group = infer_question_group(
            question_id,
            question_meta,
        )

        category = question_meta.get(
            "category",
            "",
        )

        sources = question_result.get(
            "sources",
            [],
        )

        model_results = {
            item["model_id"]: item
            for item in question_result.get(
                "model_results",
                [],
            )
        }

        print("\n")
        print("=" * 80)
        print(f"QUESTION {question_id} [{group.upper()}]")
        print("=" * 80)

        print(f"\n{question}")

        for model_id in finalist_ids:
            model_meta = model_lookup.get(
                model_id,
                {
                    "provider": "Unknown",
                    "name": model_id,
                },
            )

            candidate = model_results.get(model_id)

            if not candidate or candidate.get("status") != "SUCCESS":
                print(f"\nSkipping {model_meta['name']} (no successful saved answer).")

                continue

            call_number += 1

            print(
                f"\n[{call_number}/"
                f"{total_calls}] "
                f"Judging "
                f"{model_meta['provider']} | "
                f"{model_meta['name']}"
            )

            try:
                judgement = judge_answer(
                    question=question,
                    language=language,
                    sources=sources,
                    candidate_answer=candidate["answer"],
                )

                scores = calculate_scores(
                    judgement=judgement,
                    latency=candidate.get("latency"),
                )

                record = {
                    "question_id": question_id,
                    "group": group,
                    "category": category,
                    "language": language,
                    "question": question,
                    "provider": model_meta["provider"],
                    "model_name": model_meta["name"],
                    "model_id": model_id,
                    "candidate_answer": candidate["answer"],
                    "previous_auto_score": candidate.get("overall_score"),
                    "candidate_latency": candidate.get("latency"),
                    **scores,
                    **judgement,
                }

                detailed_results.append(record)

                print(
                    f"   Semantic quality    : {scores['semantic_quality_score']:.3f}"
                )

                print(f"   Final score         : {scores['final_score']:.3f}")

                print(f"   Groundedness        : {judgement['groundedness']:.1f}/5")

                print(
                    "   Citation correctness: "
                    f"{judgement['citation_correctness']:.1f}/5"
                )

                print(
                    f"   Numerical accuracy  : {judgement['numerical_accuracy']:.1f}/5"
                )

                print(
                    f"   Abstention quality  : {judgement['abstention_quality']:.1f}/5"
                )

                print(
                    f"   Unsupported claims  : {len(judgement['unsupported_claims'])}"
                )

                print(f"   Citation issues     : {len(judgement['citation_issues'])}")

            except Exception as error:
                print("   FAILED")

                print(f"   {error}")

                detailed_results.append(
                    {
                        "question_id": question_id,
                        "group": group,
                        "category": category,
                        "language": language,
                        "question": question,
                        "provider": model_meta["provider"],
                        "model_name": model_meta["name"],
                        "model_id": model_id,
                        "candidate_answer": candidate.get(
                            "answer",
                            "",
                        ),
                        "previous_auto_score": candidate.get("overall_score"),
                        "candidate_latency": candidate.get("latency"),
                        "semantic_quality_score": None,
                        "latency_score": calculate_latency_score(
                            candidate.get("latency")
                        ),
                        "final_score": None,
                        "error": str(error),
                    }
                )

    return detailed_results


# ============================================================
# 16. AGGREGATION HELPERS
# ============================================================


def safe_mean(
    values,
):
    """
    GOAL
    ----------------------------------------------------------
    Calculate a mean from real numeric values only.
    """

    cleaned = [value for value in values if value is not None]

    if not cleaned:
        return None

    return statistics.mean(cleaned)


def group_quality_mean(
    results,
    group_name,
):
    """
    GOAL
    ----------------------------------------------------------
    Calculate semantic quality for one benchmark group.

    OUTPUT
    ----------------------------------------------------------
    Mean quality or None.
    """

    values = [
        item.get("semantic_quality_score")
        for item in results
        if item.get("group") == group_name
    ]

    return safe_mean(values)


# ============================================================
# 17. BUILD FINAL SEMANTIC SUMMARY
# ============================================================


def build_semantic_summary(
    detailed_results,
    benchmark_data,
):
    """
    GOAL
    ----------------------------------------------------------
    Aggregate semantic results by finalist model.

    OUTPUT
    ----------------------------------------------------------
    Final ranking with:
        - final score
        - semantic quality
        - latency score
        - CORE / EXTENDED / STRESS quality
        - average judge dimensions

    RANKING
    ----------------------------------------------------------
    Primary: final score
    Tiebreaks:
        1. stress quality
        2. fewer unsupported claims
        3. fewer citation issues
        4. lower previous latency
    """

    previous_summary_lookup = {
        item["model_id"]: item
        for item in benchmark_data.get(
            "summary",
            [],
        )
    }

    grouped = {}

    for result in detailed_results:
        if result.get("final_score") is None:
            continue

        model_id = result["model_id"]

        grouped.setdefault(
            model_id,
            [],
        ).append(result)

    summaries = []

    for (
        model_id,
        results,
    ) in grouped.items():
        previous = previous_summary_lookup.get(
            model_id,
            {},
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

        claim_check_count = sum(
            len(
                item.get(
                    "claim_checks",
                    [],
                )
            )
            for item in results
        )

        core_quality = group_quality_mean(
            results,
            "core",
        )

        extended_quality = group_quality_mean(
            results,
            "extended",
        )

        stress_quality = group_quality_mean(
            results,
            "stress",
        )

        summary = {
            "provider": results[0]["provider"],
            "model_name": results[0]["model_name"],
            "model_id": model_id,
            "final_score": statistics.mean(item["final_score"] for item in results),
            "semantic_quality_score": statistics.mean(
                item["semantic_quality_score"] for item in results
            ),
            "latency_score": statistics.mean(item["latency_score"] for item in results),
            "core_quality": core_quality,
            "extended_quality": extended_quality,
            "stress_quality": stress_quality,
            "groundedness": statistics.mean(item["groundedness"] for item in results),
            "citation_correctness": statistics.mean(
                item["citation_correctness"] for item in results
            ),
            "completeness": statistics.mean(item["completeness"] for item in results),
            "numerical_accuracy": statistics.mean(
                item["numerical_accuracy"] for item in results
            ),
            "abstention_quality": statistics.mean(
                item["abstention_quality"] for item in results
            ),
            "relevance": statistics.mean(item["relevance"] for item in results),
            "language_quality": statistics.mean(
                item["language_quality"] for item in results
            ),
            "claim_check_count": claim_check_count,
            "unsupported_claim_count": unsupported_count,
            "citation_issue_count": citation_issue_count,
            "projection_or_truncation_issue_count": projection_issue_count,
            "questions_evaluated": len(results),
            "previous_auto_score": previous.get("average_score"),
            "previous_avg_latency": previous.get("average_latency"),
        }

        summaries.append(summary)

    def sort_key(
        item,
    ):
        stress = item["stress_quality"] if item["stress_quality"] is not None else -1

        latency = (
            item["previous_avg_latency"]
            if item["previous_avg_latency"] is not None
            else 9999
        )

        return (
            item["final_score"],
            stress,
            -item["unsupported_claim_count"],
            -item["citation_issue_count"],
            -latency,
        )

    summaries.sort(
        key=sort_key,
        reverse=True,
    )

    return summaries


# ============================================================
# 18. SAVE DETAILED JSON
# ============================================================


def save_details_json(
    finalist_ids,
    detailed_results,
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Save all semantic evaluations and judge explanations.

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
        "judge_model_id": JUDGE_MODEL_ID,
        "semantic_weights": SEMANTIC_WEIGHTS,
        "semantic_weight_total": SEMANTIC_WEIGHT_TOTAL,
        "latency_weight": LATENCY_WEIGHT,
        "final_score_definition": "95% semantic answer quality + 5% latency",
        "finalist_model_ids": finalist_ids,
        "source_benchmark_file": BENCHMARK_DETAILS_PATH,
        "results": detailed_results,
        "summary": summaries,
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
# 19. SAVE QUESTION-LEVEL CSV
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
        "group",
        "category",
        "language",
        "question",
        "provider",
        "model_name",
        "model_id",
        "semantic_quality_score",
        "latency_score",
        "final_score",
        "groundedness",
        "citation_correctness",
        "completeness",
        "numerical_accuracy",
        "abstention_quality",
        "relevance",
        "language_quality",
        "claim_check_count",
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
                    "question_id": item.get("question_id"),
                    "group": item.get("group"),
                    "category": item.get("category"),
                    "language": item.get("language"),
                    "question": item.get("question"),
                    "provider": item.get("provider"),
                    "model_name": item.get("model_name"),
                    "model_id": item.get("model_id"),
                    "semantic_quality_score": item.get("semantic_quality_score"),
                    "latency_score": item.get("latency_score"),
                    "final_score": item.get("final_score"),
                    "groundedness": item.get("groundedness"),
                    "citation_correctness": item.get("citation_correctness"),
                    "completeness": item.get("completeness"),
                    "numerical_accuracy": item.get("numerical_accuracy"),
                    "abstention_quality": item.get("abstention_quality"),
                    "relevance": item.get("relevance"),
                    "language_quality": item.get("language_quality"),
                    "claim_check_count": len(
                        item.get(
                            "claim_checks",
                            [],
                        )
                    ),
                    "unsupported_claim_count": len(
                        item.get(
                            "unsupported_claims",
                            [],
                        )
                    ),
                    "citation_issue_count": len(
                        item.get(
                            "citation_issues",
                            [],
                        )
                    ),
                    "projection_or_truncation_issue_count": len(
                        item.get(
                            "projection_or_truncation_issues",
                            [],
                        )
                    ),
                    "previous_auto_score": item.get("previous_auto_score"),
                    "candidate_latency": item.get("candidate_latency"),
                    "summary": item.get("summary"),
                    "candidate_answer": item.get("candidate_answer"),
                    "error": item.get("error"),
                }
            )

    return path


# ============================================================
# 20. SAVE FINAL SUMMARY CSV
# ============================================================


def save_summary_csv(
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Save final model-level semantic ranking.

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
            fieldnames=list(summaries[0].keys()),
        )

        writer.writeheader()

        writer.writerows(summaries)

    return path


# ============================================================
# 21. PRINT FINAL RANKING
# ============================================================


def format_optional_score(
    value,
):
    """
    GOAL
    ----------------------------------------------------------
    Format optional 0-1 scores safely for terminal output.
    """

    if value is None:
        return "n/a"

    return f"{value:.3f}"


def print_ranking(
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Present final comparison clearly.

    OUTPUT
    ----------------------------------------------------------
    Ranking + CORE/EXTENDED/STRESS + recommended model.
    """

    print("\n")
    print("=" * 80)
    print("FINAL SEMANTIC RAG RANKING")
    print("=" * 80)

    if not summaries:
        print("\nNo successful semantic evaluations were produced.")

        return

    for rank, model in enumerate(
        summaries,
        start=1,
    ):
        print(f"\n{rank}. {model['provider']} - {model['model_name']}")

        print(f"   Model ID                 : {model['model_id']}")

        print(f"   Final score              : {model['final_score']:.3f}")

        print(f"   Semantic quality         : {model['semantic_quality_score']:.3f}")

        print(
            "   CORE quality             : "
            f"{format_optional_score(model['core_quality'])}"
        )

        print(
            "   EXTENDED quality         : "
            f"{format_optional_score(model['extended_quality'])}"
        )

        print(
            "   STRESS quality           : "
            f"{format_optional_score(model['stress_quality'])}"
        )

        print(f"   Groundedness             : {model['groundedness']:.2f}/5")

        print(f"   Citation correctness     : {model['citation_correctness']:.2f}/5")

        print(f"   Completeness             : {model['completeness']:.2f}/5")

        print(f"   Numerical accuracy       : {model['numerical_accuracy']:.2f}/5")

        print(f"   Abstention quality       : {model['abstention_quality']:.2f}/5")

        print(f"   Relevance                : {model['relevance']:.2f}/5")

        print(f"   Language quality         : {model['language_quality']:.2f}/5")

        print(f"   Latency score            : {model['latency_score']:.3f}")

        print(f"   Unsupported claims       : {model['unsupported_claim_count']}")

        print(f"   Citation issues          : {model['citation_issue_count']}")

        print(
            "   Projection/trunc. issues : "
            f"{model['projection_or_truncation_issue_count']}"
        )

        previous_auto = model.get("previous_auto_score")

        if previous_auto is not None:
            print(f"   Previous auto score      : {previous_auto:.3f}")

        previous_latency = model.get("previous_avg_latency")

        if previous_latency is not None:
            print(f"   Previous avg. latency    : {previous_latency:.2f} s")

    winner = summaries[0]

    print("\n")
    print("=" * 80)
    print("RECOMMENDED MODEL")
    print("=" * 80)

    print(f"\n{winner['provider']} - {winner['model_name']}")

    print("\nModel ID:")

    print(winner["model_id"])

    print("\nIf you accept this result, set:")

    print(f"BEDROCK_MODEL_ID={winner['model_id']}")

    print("\nIMPORTANT:")

    print(
        "The final score uses 95% semantic answer quality "
        "and 5% latency. CORE, EXTENDED and STRESS scores "
        "are shown separately so a strong average cannot "
        "hide weak stress-test behaviour."
    )

    print(
        "\nThis is still an LLM-as-a-judge evaluation. "
        "For an even stronger evaluation, add human-reviewed "
        "reference answers and/or a second independent judge."
    )


# ============================================================
# 22. MAIN
# ============================================================


def main():
    """
    GOAL
    ----------------------------------------------------------
    Run complete second-stage semantic evaluation.

    INPUT
    ----------------------------------------------------------
    rag_benchmark_details.json

    MAIN PROCESS
    ----------------------------------------------------------
    1. Load first-stage benchmark.
    2. Select top N finalists.
    3. Reuse saved evidence and answers.
    4. Judge each finalist/question combination.
    5. Calculate semantic and final scores.
    6. Aggregate overall + CORE/EXTENDED/STRESS.
    7. Save JSON and CSV files.
    8. Print the final ranking.

    OUTPUT
    ----------------------------------------------------------
    benchmark_results/
        semantic_evaluation_details.json
        semantic_evaluation_scores.csv
        semantic_evaluation_summary.csv
    """

    try:
        benchmark_data = load_benchmark()

        finalist_ids = select_finalists(benchmark_data)

        model_lookup = build_model_lookup(benchmark_data)

        question_lookup = build_question_lookup(benchmark_data)

        print("\nSelected finalists:")

        for model_id in finalist_ids:
            model = model_lookup.get(
                model_id,
                {},
            )

            print(f" - {model.get('provider', '')} {model.get('name', model_id)}")

        detailed_results = evaluate_finalists(
            benchmark_data=benchmark_data,
            finalist_ids=finalist_ids,
            model_lookup=model_lookup,
            question_lookup=question_lookup,
        )

        summaries = build_semantic_summary(
            detailed_results=detailed_results,
            benchmark_data=benchmark_data,
        )

        details_path = save_details_json(
            finalist_ids=finalist_ids,
            detailed_results=detailed_results,
            summaries=summaries,
        )

        scores_path = save_scores_csv(detailed_results)

        summary_path = save_summary_csv(summaries)

        print_ranking(summaries)

        print("\n")
        print("=" * 80)
        print("RESULT FILES")
        print("=" * 80)

        print(f"\nDetails : {details_path}")

        print(f"Scores  : {scores_path}")

        print(f"Summary : {summary_path}")

    except (
        ClientError,
        BotoCoreError,
    ) as error:
        print("\nAWS Bedrock error:")

        print(error)

    except Exception as error:
        print("\nUnexpected error:")

        print(error)


# ============================================================
# 23. START SCRIPT
# ============================================================
# INPUT
# ------------------------------------------------------------
# Run:
#
#     python evaluate_rag_semantic.py
#
# OUTPUT
# ------------------------------------------------------------
# Strict second-stage semantic evaluation of the top finalist
# RAG models.
# ============================================================

if __name__ == "__main__":
    main()
