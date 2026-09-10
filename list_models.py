import os
import time
import re
import boto3

from dotenv import load_dotenv
from botocore.exceptions import ClientError


# ============================================================
# GOAL OF THIS SCRIPT
# ============================================================
# Discover and benchmark usable Amazon Bedrock text models.
#
# The script performs four main tasks:
#
# 1. List Bedrock foundation models.
# 2. Test direct model invocation.
# 3. Discover Bedrock inference profiles and test models that
#    require an inference profile.
# 4. Run a simple benchmark on all confirmed usable models.
#
#
# FINAL OUTPUT
# ============================================================
# The script prints:
#
# - directly invocable models
# - models usable through inference profiles
# - access-denied models
# - models requiring further investigation
# - benchmark latency
# - citation usage
# - language-following result
# - benchmark score
#
# ============================================================


# ============================================================
# INPUT CONFIGURATION
# ============================================================
# Expected values in .env:
#
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# AWS_REGION=eu-central-1
#
# ============================================================

load_dotenv(override=True)

AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")


# ============================================================
# CREATE AWS CLIENTS
# ============================================================
#
# bedrock:
#   Used for model catalog and inference-profile discovery.
#
# bedrock_runtime:
#   Used for actual LLM invocation.
#
# ============================================================

bedrock = boto3.client(
    "bedrock",
    region_name=AWS_REGION,
)

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION,
)


# ============================================================
# BENCHMARK INPUT
# ============================================================
# GOAL:
#   Give every usable model exactly the same task.
#
# INPUT:
#   One German energy question plus trusted source text.
#
# OUTPUT EXPECTATION:
#   - German answer
#   - only use supplied evidence
#   - citations such as [1], [2]
#
# NOTE:
#   Later we will replace this small synthetic example with
#   real chunks retrieved from your SFOE MCP Gateway.
# ============================================================

BENCHMARK_QUESTION = "Welche Rolle spielt Wasserkraft in der Schweizer Stromversorgung?"

BENCHMARK_CONTEXT = """
[1]
Die Wasserkraft bleibt die mit Abstand wichtigste einheimische
Energiequelle der Schweiz. Lauf- und Speicherkraftwerke deckten
im Jahr 2020 gemäss Schweizerischer Elektrizitätsstatistik
58 Prozent des Strombedarfs.

[2]
Mit zunehmendem Ausbau von Wind- und Solarenergie erhält die
Wasserkraft eine zusätzliche Rolle bei der Integration grosser
Mengen fluktuierender erneuerbarer Stromproduktion.
"""


# ============================================================
# 1. FILTER TEXT-GENERATION MODELS
# ============================================================


def is_text_generation_model(model):
    """
    GOAL
    ----------------------------------------------------------
    Exclude models that clearly are not normal answering LLMs.

    INPUT
    ----------------------------------------------------------
    model:
        One model summary from list_foundation_models().

    OUTPUT
    ----------------------------------------------------------
    True:
        Probably usable for text generation.

    False:
        Embedding / reranking / non-text model.
    """

    model_id = model.get("modelId", "").lower()

    model_name = model.get("modelName", "").lower()

    excluded_keywords = [
        "embed",
        "embedding",
        "rerank",
    ]

    for keyword in excluded_keywords:
        if keyword in model_id or keyword in model_name:
            return False

    output_modalities = model.get("outputModalities", [])

    return "TEXT" in output_modalities


# ============================================================
# 2. EXTRACT GENERATED TEXT
# ============================================================


def extract_text(response):
    """
    GOAL
    ----------------------------------------------------------
    Extract normal generated text from a Converse response.

    INPUT
    ----------------------------------------------------------
    response:
        Dictionary returned by bedrock_runtime.converse().

    OUTPUT
    ----------------------------------------------------------
    Generated text.

    ERROR
    ----------------------------------------------------------
    Raises ValueError if no normal text field can be found.
    """

    try:
        content = response["output"]["message"]["content"]

        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    return item["text"]

        raise ValueError("No normal 'text' field found in Converse response.")

    except (KeyError, TypeError, IndexError) as error:
        raise ValueError(f"Unexpected Converse response structure: {error}")


# ============================================================
# 3. INVOKE ONE BEDROCK TARGET
# ============================================================


def invoke_target(target_id, prompt, max_tokens=100):
    """
    GOAL
    ----------------------------------------------------------
    Invoke either:
    - a foundation model ID, or
    - an inference profile ID / ARN.

    INPUT
    ----------------------------------------------------------
    target_id:
        Bedrock model ID or inference profile ID/ARN.

    prompt:
        Text sent to the model.

    max_tokens:
        Maximum output tokens.

    OUTPUT
    ----------------------------------------------------------
    Dictionary containing:

        status
        latency
        text
        error_code
        error_message

    NOTE
    ----------------------------------------------------------
    Bedrock Converse supports inference profile IDs/ARNs in
    the same modelId parameter.
    """

    start = time.perf_counter()

    try:
        response = bedrock_runtime.converse(
            modelId=target_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ],
            inferenceConfig={
                "maxTokens": max_tokens,
                "temperature": 0,
            },
        )

        latency = time.perf_counter() - start

        try:
            text = extract_text(response)

            return {
                "status": "AVAILABLE",
                "latency": latency,
                "text": text,
                "error_code": None,
                "error_message": None,
            }

        except ValueError as error:
            return {
                "status": "RESPONSE_FORMAT_ERROR",
                "latency": latency,
                "text": None,
                "error_code": None,
                "error_message": str(error),
            }

    except ClientError as error:
        latency = time.perf_counter() - start

        aws_error = error.response.get("Error", {})

        error_code = aws_error.get("Code", "Unknown")

        error_message = aws_error.get("Message", str(error))

        if error_code == "AccessDeniedException":
            status = "ACCESS_DENIED"

        elif error_code == "ValidationException":
            status = "VALIDATION_ERROR"

        else:
            status = "OTHER_ERROR"

        return {
            "status": status,
            "latency": latency,
            "text": None,
            "error_code": error_code,
            "error_message": error_message,
        }

    except Exception as error:
        latency = time.perf_counter() - start

        return {
            "status": "OTHER_ERROR",
            "latency": latency,
            "text": None,
            "error_code": None,
            "error_message": str(error),
        }


# ============================================================
# 4. TEST DIRECT MODEL INVOCATION
# ============================================================


def test_direct_model(model_id):
    """
    GOAL
    ----------------------------------------------------------
    Test whether a raw foundation model ID can be invoked.

    INPUT
    ----------------------------------------------------------
    model_id

    OUTPUT
    ----------------------------------------------------------
    Result from invoke_target().
    """

    return invoke_target(
        model_id,
        "Reply with exactly: OK",
        max_tokens=20,
    )


# ============================================================
# 5. LIST INFERENCE PROFILES
# ============================================================


def list_inference_profiles():
    """
    GOAL
    ----------------------------------------------------------
    Discover Bedrock inference profiles available to the
    current AWS account.

    INPUT
    ----------------------------------------------------------
    AWS account and AWS region.

    OUTPUT
    ----------------------------------------------------------
    List of inference profile summaries.

    Each profile can contain:
        - inferenceProfileId
        - inferenceProfileArn
        - inferenceProfileName
        - models
        - status
        - type

    PROCESS
    ----------------------------------------------------------
    Uses paginator because AWS may return multiple pages.
    """

    profiles = []

    paginator = bedrock.get_paginator("list_inference_profiles")

    for page in paginator.paginate():
        profiles.extend(page.get("inferenceProfileSummaries", []))

    return profiles


# ============================================================
# 6. EXTRACT MODEL ID FROM MODEL ARN
# ============================================================


def model_id_from_arn(model_arn):
    """
    GOAL
    ----------------------------------------------------------
    Convert a Bedrock model ARN into its model ID.

    INPUT
    ----------------------------------------------------------
    Example:
        arn:aws:bedrock:eu-west-1::
        foundation-model/anthropic.claude...

    OUTPUT
    ----------------------------------------------------------
    Example:
        anthropic.claude...
    """

    marker = "foundation-model/"

    if marker not in model_arn:
        return None

    return model_arn.split(marker, 1)[1]


# ============================================================
# 7. MAP MODELS TO INFERENCE PROFILES
# ============================================================


def build_profile_map(profiles):
    """
    GOAL
    ----------------------------------------------------------
    Create a mapping:

        model_id
            ↓
        inference profiles containing that model

    INPUT
    ----------------------------------------------------------
    profiles:
        List returned by list_inference_profiles().

    OUTPUT
    ----------------------------------------------------------
    Dictionary:

        {
            "anthropic.claude...": [
                {
                    profile_id,
                    profile_arn,
                    profile_name
                }
            ]
        }
    """

    profile_map = {}

    for profile in profiles:
        if profile.get("status") != "ACTIVE":
            continue

        for model in profile.get("models", []):
            model_arn = model.get("modelArn", "")

            model_id = model_id_from_arn(model_arn)

            if not model_id:
                continue

            profile_map.setdefault(model_id, [])

            profile_map[model_id].append(
                {
                    "profile_id": profile.get("inferenceProfileId"),
                    "profile_arn": profile.get("inferenceProfileArn"),
                    "profile_name": profile.get("inferenceProfileName"),
                    "profile_type": profile.get("type"),
                }
            )

    return profile_map


# ============================================================
# 8. TEST MODEL THROUGH INFERENCE PROFILE
# ============================================================


def test_model_profiles(model_id, profiles):
    """
    GOAL
    ----------------------------------------------------------
    Retry a model using its available inference profiles.

    INPUT
    ----------------------------------------------------------
    model_id:
        Original foundation model.

    profiles:
        Profiles containing this model.

    OUTPUT
    ----------------------------------------------------------
    First successful profile result.

    If no profile succeeds:
        return failure information.
    """

    attempts = []

    for profile in profiles:
        profile_id = profile["profile_id"]

        result = invoke_target(
            profile_id,
            "Reply with exactly: OK",
            max_tokens=20,
        )

        attempts.append(
            {
                "profile": profile,
                "result": result,
            }
        )

        if result["status"] == "AVAILABLE":
            return {
                "success": True,
                "profile": profile,
                "result": result,
                "attempts": attempts,
            }

    return {
        "success": False,
        "profile": None,
        "result": None,
        "attempts": attempts,
    }


# ============================================================
# 9. DISCOVER ALL USABLE MODELS
# ============================================================


def discover_usable_models():
    """
    GOAL
    ----------------------------------------------------------
    Determine the real set of usable Bedrock models.

    INPUT
    ----------------------------------------------------------
    Foundation model catalog
    Inference profile catalog

    PROCESS
    ----------------------------------------------------------
    For every text-generation model:

    1. Try direct invocation.
    2. If direct invocation fails with ValidationException,
       check whether an inference profile exists.
    3. Try available profiles.
    4. Record the usable invocation target.

    OUTPUT
    ----------------------------------------------------------
    evaluated_models:
        Full diagnostic list.

    usable_models:
        Confirmed usable candidates for benchmarking.
    """

    model_response = bedrock.list_foundation_models()

    models = model_response["modelSummaries"]

    profiles = list_inference_profiles()

    profile_map = build_profile_map(profiles)

    evaluated_models = []
    usable_models = []

    print(f"\nFound {len(profiles)} inference profiles.")

    print(f"\nTesting Bedrock models in {AWS_REGION}\n")

    for model in models:
        if not is_text_generation_model(model):
            continue

        provider = model.get("providerName", "Unknown")

        model_name = model.get("modelName", "Unknown")

        model_id = model.get("modelId", "Unknown")

        print(f"Testing: {provider} | {model_name}")

        direct_result = test_direct_model(model_id)

        record = {
            "provider": provider,
            "model_name": model_name,
            "model_id": model_id,
            "direct_status": direct_result["status"],
            "usable": False,
            "invocation_target": None,
            "invocation_type": None,
            "latency": None,
            "message": None,
        }

        # ----------------------------------------------------
        # DIRECT INVOCATION SUCCESS
        # ----------------------------------------------------

        if direct_result["status"] == "AVAILABLE":
            print(f"   AVAILABLE DIRECT | {direct_result['latency']:.2f} s")

            record.update(
                {
                    "usable": True,
                    "invocation_target": model_id,
                    "invocation_type": "DIRECT",
                    "latency": direct_result["latency"],
                    "message": direct_result["text"],
                }
            )

            usable_models.append(record.copy())

        # ----------------------------------------------------
        # VALIDATION ERROR → TRY INFERENCE PROFILE
        # ----------------------------------------------------

        elif direct_result["status"] == "VALIDATION_ERROR":
            available_profiles = profile_map.get(model_id, [])

            if available_profiles:
                profile_test = test_model_profiles(
                    model_id,
                    available_profiles,
                )

                if profile_test["success"]:
                    profile = profile_test["profile"]

                    result = profile_test["result"]

                    print(
                        f"   AVAILABLE VIA PROFILE | "
                        f"{profile['profile_id']} | "
                        f"{result['latency']:.2f} s"
                    )

                    record.update(
                        {
                            "usable": True,
                            "invocation_target": profile["profile_id"],
                            "invocation_type": "INFERENCE_PROFILE",
                            "latency": result["latency"],
                            "message": result["text"],
                        }
                    )

                    usable_models.append(record.copy())

                else:
                    print("   PROFILE FOUND, BUT INVOCATION FAILED")

                    record["message"] = direct_result["error_message"]

            else:
                print("   VALIDATION ERROR | NO MATCHING PROFILE FOUND")

                record["message"] = direct_result["error_message"]

        # ----------------------------------------------------
        # RESPONSE FORMAT ERROR
        # ----------------------------------------------------

        elif direct_result["status"] == "RESPONSE_FORMAT_ERROR":
            print("   RESPONSE FORMAT ERROR")

            record["message"] = direct_result["error_message"]

        # ----------------------------------------------------
        # ACCESS DENIED
        # ----------------------------------------------------

        elif direct_result["status"] == "ACCESS_DENIED":
            print("   ACCESS DENIED")

            record["message"] = direct_result["error_message"]

        else:
            print("   OTHER ERROR")

            record["message"] = direct_result["error_message"]

        evaluated_models.append(record)

    return (
        evaluated_models,
        usable_models,
    )


# ============================================================
# 10. BUILD BENCHMARK PROMPT
# ============================================================


def build_benchmark_prompt():
    """
    GOAL
    ----------------------------------------------------------
    Create the exact same benchmark prompt for every model.

    INPUT
    ----------------------------------------------------------
    BENCHMARK_QUESTION
    BENCHMARK_CONTEXT

    OUTPUT
    ----------------------------------------------------------
    One grounded RAG-style prompt.
    """

    return f"""
You are evaluating a Swiss energy knowledge assistant.

Answer the question using ONLY the supplied sources.

Rules:
- Answer in German.
- Do not use outside information.
- Cite factual claims using [1] and [2].
- Be concise.
- If the sources are insufficient, say so.

QUESTION:
{BENCHMARK_QUESTION}

SOURCES:
{BENCHMARK_CONTEXT}
"""


# ============================================================
# 11. CHECK SIMPLE BENCHMARK CRITERIA
# ============================================================


def evaluate_answer(answer):
    """
    GOAL
    ----------------------------------------------------------
    Perform simple automatic checks on one answer.

    INPUT
    ----------------------------------------------------------
    answer:
        Generated LLM answer.

    OUTPUT
    ----------------------------------------------------------
    Dictionary with:
        citation_score
        german_score
        concise_score

    NOTE
    ----------------------------------------------------------
    These checks are intentionally simple.
    They are NOT yet a full semantic quality evaluation.
    """

    # --------------------------------------------------------
    # CITATION CHECK
    # --------------------------------------------------------
    # Full score if answer uses both [1] and [2].
    # Partial score if one appears.
    # --------------------------------------------------------

    citations_found = set(re.findall(r"\[(1|2)\]", answer))

    if len(citations_found) == 2:
        citation_score = 1.0

    elif len(citations_found) == 1:
        citation_score = 0.5

    else:
        citation_score = 0.0

    # --------------------------------------------------------
    # BASIC GERMAN CHECK
    # --------------------------------------------------------
    # This is only a lightweight heuristic.
    # --------------------------------------------------------

    german_markers = [
        "die ",
        "der ",
        "und ",
        "ist ",
        "Schweiz",
        "Wasserkraft",
    ]

    answer_lower = answer.lower()

    german_hits = sum(marker.lower() in answer_lower for marker in german_markers)

    german_score = min(german_hits / 3, 1.0)

    # --------------------------------------------------------
    # CONCISENESS CHECK
    # --------------------------------------------------------

    word_count = len(answer.split())

    if word_count <= 150:
        concise_score = 1.0

    elif word_count <= 250:
        concise_score = 0.5

    else:
        concise_score = 0.0

    return {
        "citation_score": citation_score,
        "german_score": german_score,
        "concise_score": concise_score,
        "word_count": word_count,
    }


# ============================================================
# 12. BENCHMARK ONE MODEL
# ============================================================


def benchmark_model(model):
    """
    GOAL
    ----------------------------------------------------------
    Run the same benchmark against one confirmed usable model.

    INPUT
    ----------------------------------------------------------
    model:
        Usable-model record.

    OUTPUT
    ----------------------------------------------------------
    Benchmark result containing:
        answer
        latency
        citation score
        German score
        conciseness score
        automatic total score
    """

    prompt = build_benchmark_prompt()

    result = invoke_target(
        model["invocation_target"],
        prompt,
        max_tokens=500,
    )

    if result["status"] != "AVAILABLE":
        return {
            **model,
            "benchmark_status": "FAILED",
            "benchmark_error": result["error_message"],
        }

    answer = result["text"]

    checks = evaluate_answer(answer)

    # --------------------------------------------------------
    # BASIC AUTOMATIC SCORE
    # --------------------------------------------------------
    #
    # Citation presence     50%
    # German output         30%
    # Conciseness           20%
    #
    # IMPORTANT:
    # This is NOT yet the final quality score.
    # We still need semantic groundedness and answer quality.
    # --------------------------------------------------------

    automatic_score = (
        0.50 * checks["citation_score"]
        + 0.30 * checks["german_score"]
        + 0.20 * checks["concise_score"]
    )

    return {
        **model,
        "benchmark_status": "SUCCESS",
        "benchmark_latency": result["latency"],
        "answer": answer,
        **checks,
        "automatic_score": automatic_score,
    }


# ============================================================
# 13. BENCHMARK ALL USABLE MODELS
# ============================================================


def benchmark_models(usable_models):
    """
    GOAL
    ----------------------------------------------------------
    Benchmark every usable model on exactly the same task.

    INPUT
    ----------------------------------------------------------
    usable_models:
        Confirmed models from discovery phase.

    OUTPUT
    ----------------------------------------------------------
    List of benchmark results.
    """

    results = []

    print("\n")
    print("=" * 80)
    print("RUNNING BASIC BENCHMARK")
    print("=" * 80)

    for model in usable_models:
        print(f"\nBenchmarking: {model['provider']} | {model['model_name']}")

        result = benchmark_model(model)

        results.append(result)

        if result["benchmark_status"] == "SUCCESS":
            print(f"   Score   : {result['automatic_score']:.2f}")

            print(f"   Latency : {result['benchmark_latency']:.2f} s")

        else:
            print("   BENCHMARK FAILED")

    return results


# ============================================================
# 14. PRINT BENCHMARK RANKING
# ============================================================


def print_benchmark_ranking(results):
    """
    GOAL
    ----------------------------------------------------------
    Rank successfully benchmarked models.

    INPUT
    ----------------------------------------------------------
    Benchmark results.

    OUTPUT
    ----------------------------------------------------------
    Sorted ranking by automatic benchmark score.
    """

    successful = [
        result for result in results if (result.get("benchmark_status") == "SUCCESS")
    ]

    successful.sort(
        key=lambda item: (
            item["automatic_score"],
            -item["benchmark_latency"],
        ),
        reverse=True,
    )

    print("\n")
    print("=" * 80)
    print("BASIC BENCHMARK RANKING")
    print("=" * 80)

    if not successful:
        print("\nNo model completed the benchmark.")

        return

    for rank, model in enumerate(successful, start=1):
        print(f"\n{rank}. {model['provider']} - {model['model_name']}")

        print(f"   Invocation : {model['invocation_type']}")

        print(f"   Target     : {model['invocation_target']}")

        print(f"   Score      : {model['automatic_score']:.2f}")

        print(f"   Citations  : {model['citation_score']:.2f}")

        print(f"   German     : {model['german_score']:.2f}")

        print(f"   Concise    : {model['concise_score']:.2f}")

        print(f"   Latency    : {model['benchmark_latency']:.2f} s")

        print(f"   Words      : {model['word_count']}")

        print("\n   Answer:")
        print("   " + model["answer"].replace("\n", "\n   "))


# ============================================================
# 15. MAIN
# ============================================================


def main():
    """
    GOAL
    ----------------------------------------------------------
    Run the complete discovery + benchmark workflow.

    INPUT
    ----------------------------------------------------------
    AWS Bedrock account
    AWS region
    benchmark question/context

    PROCESS
    ----------------------------------------------------------
    1. Find usable models.
    2. Test inference profiles where necessary.
    3. Benchmark every usable model.
    4. Rank results.

    OUTPUT
    ----------------------------------------------------------
    Model compatibility report and basic benchmark ranking.
    """

    (
        evaluated_models,
        usable_models,
    ) = discover_usable_models()

    print("\n")
    print("=" * 80)
    print("DISCOVERY SUMMARY")
    print("=" * 80)

    print(f"\nUsable models found: {len(usable_models)}")

    for model in usable_models:
        print(f"\n- {model['provider']} | {model['model_name']}")

        print(f"  Invocation: {model['invocation_type']}")

        print(f"  Target: {model['invocation_target']}")

    # --------------------------------------------------------
    # RUN BASIC BENCHMARK
    # --------------------------------------------------------

    benchmark_results = benchmark_models(usable_models)

    # --------------------------------------------------------
    # PRINT FINAL RANKING
    # --------------------------------------------------------

    print_benchmark_ranking(benchmark_results)


# ============================================================
# 16. START SCRIPT
# ============================================================
#
# INPUT:
#
#     python evaluate_models.py
#
# OUTPUT:
#
#     1. Bedrock model discovery
#     2. Inference-profile discovery
#     3. Usable model list
#     4. Basic benchmark
#     5. Ranked results
#
# ============================================================

if __name__ == "__main__":
    main()
