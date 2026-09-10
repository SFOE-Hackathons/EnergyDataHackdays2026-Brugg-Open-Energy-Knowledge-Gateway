import os
import re
import csv
import json
import time
import statistics

import boto3
import requests

from dotenv import load_dotenv
from botocore.exceptions import BotoCoreError, ClientError


# ============================================================
# GOAL OF THIS SCRIPT
# ============================================================
# Benchmark the confirmed usable Amazon Bedrock LLMs for the
# SFOE Open Energy Knowledge Gateway RAG application.
#
# COMPLETE DATA FLOW
# ------------------------------------------------------------
# Benchmark question
#       ↓
# Optional Cognito authentication
#       ↓
# Current SFOE MCP Gateway
#       ↓
# bfe-energy___search_energy_knowledge
#       ↓
# Retrieve REAL SFOE passages ONCE
#       ↓
# Resolve source_id → official source metadata
#       ↓
# Give exactly the SAME evidence to every candidate LLM
#       ↓
# Generate answers + citations
#       ↓
# Automatic proxy evaluation
#       ↓
# Ranking + preliminary recommended model
#
# WHY RETRIEVE ONLY ONCE PER QUESTION?
# ------------------------------------------------------------
# Every model must receive the same evidence. Otherwise we
# would be comparing retrieval quality and model quality at
# the same time instead of comparing only the LLMs.
# ============================================================


# ============================================================
# 1. LOAD CONFIGURATION
# ============================================================
# INPUT
# ------------------------------------------------------------
# Required in .env:
#   GATEWAY_URL
#
# AWS credentials must also be available to boto3, either
# through .env or another standard AWS credential mechanism.
#
# Optional in .env:
#   CLIENT_ID
#   CLIENT_SECRET
#   TOKEN_URL
#   AWS_REGION
#   TOP_K_CONTEXT
#
# OUTPUT
# ------------------------------------------------------------
# Runtime configuration used by the benchmark.
# ============================================================

load_dotenv(override=True)

GATEWAY_URL = os.environ["GATEWAY_URL"]

AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")
TOP_K_CONTEXT = int(os.getenv("TOP_K_CONTEXT", "5"))

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TOKEN_URL = os.getenv("TOKEN_URL")


# ============================================================
# 2. MCP CONFIGURATION
# ============================================================
# GOAL
# ------------------------------------------------------------
# Use the current SFOE semantic knowledge-search tool.
#
# OUTPUT
# ------------------------------------------------------------
# Current protocol version and MCP tool name.
# ============================================================

MCP_PROTOCOL_VERSION = "2026-07-28"

TOOL_NAME = "bfe-energy___search_energy_knowledge"


# ============================================================
# 3. BENCHMARK CONFIGURATION
# ============================================================

MAX_TOKENS = 500

OUTPUT_DIRECTORY = "benchmark_results"


# ============================================================
# 4. CONFIRMED USABLE MODELS
# ============================================================
# INPUT
# ------------------------------------------------------------
# These are the six models that previously succeeded with the
# Bedrock Converse API in this AWS account.
#
# OUTPUT
# ------------------------------------------------------------
# Candidate models used for the RAG benchmark.
# ============================================================

CANDIDATE_MODELS = [
    {
        "provider": "Qwen",
        "name": "Qwen3 235B A22B 2507",
        "model_id": "qwen.qwen3-235b-a22b-2507-v1:0",
    },
    {
        "provider": "Z.AI",
        "name": "GLM 4.7 Flash",
        "model_id": "zai.glm-4.7-flash",
    },
    {
        "provider": "Mistral AI",
        "name": "Devstral 2 123B",
        "model_id": "mistral.devstral-2-123b",
    },
    {
        "provider": "Qwen",
        "name": "Qwen3-Coder-30B-A3B-Instruct",
        "model_id": "qwen.qwen3-coder-30b-a3b-v1:0",
    },
    {
        "provider": "NVIDIA",
        "name": "NVIDIA Nemotron 3 Super 120B A12B",
        "model_id": "nvidia.nemotron-super-3-120b",
    },
    {
        "provider": "Qwen",
        "name": "Qwen3 32B",
        "model_id": "qwen.qwen3-32b-v1:0",
    },
]


# ============================================================
# 5. MULTILINGUAL BENCHMARK QUESTIONS
# ============================================================
# GOAL
# ------------------------------------------------------------
# Test models with the same SFOE RAG workflow in German,
# English and French.
#
# INPUT
# ------------------------------------------------------------
# Natural-language questions.
#
# OUTPUT
# ------------------------------------------------------------
# Each question is retrieved once and evaluated across every
# candidate model.
# ============================================================

BENCHMARK_QUESTIONS = [
    {
        "id": "DE_01",
        "language": "de",
        "question": "Welche Rolle spielt Wasserkraft in der Schweizer Stromversorgung?",
    },
    {
        "id": "DE_02",
        "language": "de",
        "question": "Welche Bedeutung hat Photovoltaik "
        "für die Schweizer Energieversorgung?",
    },
    {
        "id": "EN_01",
        "language": "en",
        "question": "What role does hydropower play "
        "in Switzerland's electricity supply?",
    },
    {
        "id": "EN_02",
        "language": "en",
        "question": "What does SFOE say about hydrogen in Switzerland's energy system?",
    },
    {
        "id": "FR_01",
        "language": "fr",
        "question": "Quel rôle joue l'hydroélectricité "
        "dans l'approvisionnement électrique suisse ?",
    },
    {
        "id": "FR_02",
        "language": "fr",
        "question": "Quelle importance le photovoltaïque "
        "a-t-il pour l'approvisionnement énergétique suisse ?",
    },
]


# ============================================================
# 6. CREATE BEDROCK RUNTIME CLIENT
# ============================================================
# GOAL
# ------------------------------------------------------------
# Connect to Amazon Bedrock Runtime.
#
# INPUT
# ------------------------------------------------------------
# AWS credentials and AWS_REGION.
#
# OUTPUT
# ------------------------------------------------------------
# Bedrock client used by all candidate models.
# ============================================================

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    region_name=AWS_REGION,
)


# ============================================================
# 7. OPTIONAL COGNITO AUTHENTICATION
# ============================================================


def fetch_access_token():
    """
    GOAL
    ----------------------------------------------------------
    Obtain a Cognito OAuth token when Cognito configuration is
    present.

    INPUT
    ----------------------------------------------------------
    CLIENT_ID
    CLIENT_SECRET
    TOKEN_URL

    OUTPUT
    ----------------------------------------------------------
    Access token, or None when Cognito is not configured.

    NOTE
    ----------------------------------------------------------
    The current open SFOE Gateway does not require inbound
    authorization, but keeping this optional support makes the
    benchmark compatible with authenticated gateways too.
    """

    if not all([CLIENT_ID, CLIENT_SECRET, TOKEN_URL]):
        return None

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    response.raise_for_status()

    return response.json()["access_token"]


# ============================================================
# 8. BUILD MCP HEADERS
# ============================================================


def build_mcp_headers(access_token=None):
    """
    GOAL
    ----------------------------------------------------------
    Build HTTP headers for the SFOE MCP tools/call request.

    INPUT
    ----------------------------------------------------------
    Optional Cognito access token.

    OUTPUT
    ----------------------------------------------------------
    HTTP headers dictionary.
    """

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": TOOL_NAME,
    }

    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    return headers


# ============================================================
# 9. RETRIEVE REAL SFOE KNOWLEDGE
# ============================================================


def retrieve_sfoe(
    question,
    access_token=None,
):
    """
    GOAL
    ----------------------------------------------------------
    Search official SFOE publications through the CURRENT MCP
    search tool.

    INPUT
    ----------------------------------------------------------
    question:
        Benchmark question.

    access_token:
        Optional Cognito token.

    OUTPUT
    ----------------------------------------------------------
    Complete parsed search response:

        {
            "result_count": ...,
            "sources": {...},
            "results": [...]
        }

    IMPORTANT
    ----------------------------------------------------------
    The current tool expects:

        {"query": question}

    It does NOT use the old:
        {"retrievalQuery": {"text": question}}
    """

    payload = {
        "jsonrpc": "2.0",
        "id": "benchmark-search",
        "method": "tools/call",
        "params": {
            "name": TOOL_NAME,
            "arguments": {"query": question},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "sfoe-rag-benchmark",
                    "version": "1.0.0",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }

    response = requests.post(
        GATEWAY_URL,
        headers=build_mcp_headers(access_token),
        json=payload,
        timeout=120,
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(
            "MCP Gateway error:\n"
            + json.dumps(
                data["error"],
                indent=2,
                ensure_ascii=False,
            )
        )

    if "result" not in data:
        raise RuntimeError(
            "Unexpected MCP response:\n"
            + json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            )
        )

    content = data["result"].get("content", [])

    if not content:
        raise RuntimeError("MCP response contains no content.")

    text_content = None

    for item in content:
        if isinstance(item, dict) and "text" in item:
            text_content = item["text"]
            break

    if text_content is None:
        raise RuntimeError("MCP response contains no text content.")

    try:
        search_data = json.loads(text_content)

    except json.JSONDecodeError as error:
        raise RuntimeError(
            "The SFOE MCP tool returned text "
            "but it was not valid JSON:\n"
            f"{text_content}"
        ) from error

    return search_data


# ============================================================
# 10. NORMALIZE CURRENT SFOE SEARCH RESULTS
# ============================================================


def prepare_evidence(search_data):
    """
    GOAL
    ----------------------------------------------------------
    Convert the current SFOE response into a simple evidence
    list shared by every LLM.

    INPUT
    ----------------------------------------------------------
    search_data with:
        result_count
        sources
        results

    OUTPUT
    ----------------------------------------------------------
    Up to TOP_K_CONTEXT normalized evidence items containing:
        source_number
        score
        text
        source_id
        document
        published_at
        url
        years_covered
        bases
        is_projection
        is_truncated
        truncation_reasons

    PROCESS
    ----------------------------------------------------------
    1. Read ranked results.
    2. Resolve each source_id through the top-level sources map.
    3. Remove exact duplicate passages.
    4. Keep the first TOP_K_CONTEXT unique passages.
    """

    if not isinstance(search_data, dict):
        raise RuntimeError("Unexpected SFOE search response type.")

    results = search_data.get("results", [])

    sources = search_data.get("sources", {})

    evidence = []
    seen_texts = set()

    for item in results:
        if not isinstance(item, dict):
            continue

        text = (
            item.get("text") or item.get("passage") or item.get("content") or ""
        ).strip()

        if not text:
            continue

        # Exact duplicate passages should not be sent twice.
        if text in seen_texts:
            continue

        seen_texts.add(text)

        source_id = item.get("source_id")

        source = sources.get(source_id, {}) if source_id else {}

        evidence.append(
            {
                "source_number": len(evidence) + 1,
                "score": item.get("score", 0),
                "text": text,
                "source_id": source_id,
                "document": (
                    source.get("title")
                    or source.get("document_title")
                    or "Unknown document"
                ),
                "published_at": source.get("published_at"),
                "url": source.get("download_url"),
                "years_covered": item.get("years_covered"),
                "bases": item.get("bases", []),
                "is_projection": item.get("is_projection", False),
                "is_truncated": item.get("is_truncated", False),
                "truncation_reasons": item.get("truncation_reasons", []),
            }
        )

        if len(evidence) >= TOP_K_CONTEXT:
            break

    return evidence


# ============================================================
# 11. BUILD IDENTICAL RAG CONTEXT FOR EVERY MODEL
# ============================================================


def build_context(evidence):
    """
    GOAL
    ----------------------------------------------------------
    Convert normalized evidence into numbered source blocks.

    INPUT
    ----------------------------------------------------------
    Normalized SFOE evidence.

    OUTPUT
    ----------------------------------------------------------
    One context string given IDENTICALLY to every candidate
    model.
    """

    context_parts = []

    for item in evidence:
        context_parts.append(
            f"""
[SOURCE {item["source_number"]}]
Document: {item["document"]}
Published: {item["published_at"] or "Unknown"}
URL: {item["url"] or "Not available"}
Relevance score: {item["score"]}
Years covered: {item["years_covered"]}
Projection: {item["is_projection"]}
Truncated: {item["is_truncated"]}

Passage:
{item["text"]}
"""
        )

    return "\n".join(context_parts)


# ============================================================
# 12. BUILD RAG PROMPT
# ============================================================


def build_prompt(
    question,
    language,
    context,
):
    """
    GOAL
    ----------------------------------------------------------
    Create identical grounded instructions for every model.

    INPUT
    ----------------------------------------------------------
    question:
        Benchmark question.

    language:
        Expected output language.

    context:
        Same retrieved SFOE evidence for every candidate.

    OUTPUT
    ----------------------------------------------------------
    Prompt sent to each LLM.
    """

    language_names = {
        "de": "German",
        "en": "English",
        "fr": "French",
    }

    expected_language = language_names.get(
        language, "the same language as the question"
    )

    return f"""
You are an assistant for the Swiss Federal Office of Energy
(SFOE / BFE) Open Energy Knowledge Gateway.

Answer the question using ONLY the retrieved SFOE evidence
provided below.

RULES:

1. Use only information contained in the sources.
2. Do not invent facts.
3. Do not use unsupported external knowledge.
4. Answer in {expected_language}.
5. Cite factual claims using [1], [2], [3], etc.
6. Citation numbers must refer to the numbered sources below.
7. If the available evidence is insufficient, clearly say so.
8. Be concise, clear and factual.
9. Do not include a separate bibliography.
10. If Projection: True, do NOT present forward-looking
    statements as measured historical facts.
11. If Truncated: True, treat the passage cautiously because
    it may be incomplete.
12. Do not invent page numbers because page numbers are not
    available in this corpus.


QUESTION:

{question}


RETRIEVED SFOE EVIDENCE:

{context}


ANSWER:
"""


# ============================================================
# 13. EXTRACT TEXT FROM BEDROCK RESPONSE
# ============================================================


def extract_generated_text(response):
    """
    GOAL
    ----------------------------------------------------------
    Extract normal generated text from Bedrock Converse.

    INPUT
    ----------------------------------------------------------
    Bedrock Converse API response.

    OUTPUT
    ----------------------------------------------------------
    Generated answer string.
    """

    content = response["output"]["message"]["content"]

    for item in content:
        if isinstance(item, dict) and "text" in item:
            return item["text"]

    raise ValueError("No generated text found in Bedrock response.")


# ============================================================
# 14. INVOKE ONE MODEL
# ============================================================


def generate_answer(
    model_id,
    prompt,
):
    """
    GOAL
    ----------------------------------------------------------
    Generate one grounded answer using one candidate LLM.

    INPUT
    ----------------------------------------------------------
    model_id:
        Bedrock model ID.

    prompt:
        Question + identical real SFOE evidence.

    OUTPUT
    ----------------------------------------------------------
    Dictionary containing:
        status
        answer
        latency
        error
    """

    start_time = time.perf_counter()

    try:
        response = bedrock_runtime.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ],
            inferenceConfig={
                "maxTokens": MAX_TOKENS,
                "temperature": 0,
            },
        )

        latency = time.perf_counter() - start_time

        answer = extract_generated_text(response)

        return {
            "status": "SUCCESS",
            "answer": answer,
            "latency": latency,
            "error": None,
        }

    except ClientError as error:
        latency = time.perf_counter() - start_time

        aws_error = error.response.get("Error", {})

        return {
            "status": "FAILED",
            "answer": "",
            "latency": latency,
            "error": (f"{aws_error.get('Code')}: {aws_error.get('Message')}"),
        }

    except BotoCoreError as error:
        latency = time.perf_counter() - start_time

        return {
            "status": "FAILED",
            "answer": "",
            "latency": latency,
            "error": str(error),
        }

    except Exception as error:
        latency = time.perf_counter() - start_time

        return {
            "status": "FAILED",
            "answer": "",
            "latency": latency,
            "error": str(error),
        }


# ============================================================
# 15. TEXT TOKENIZATION
# ============================================================


def tokenize(text):
    """
    GOAL
    ----------------------------------------------------------
    Convert text into normalized words for simple evaluation.

    INPUT
    ----------------------------------------------------------
    Any text string.

    OUTPUT
    ----------------------------------------------------------
    List of lowercase alphabetic words.
    """

    return re.findall(r"[A-Za-zÀ-ÿÄÖÜäöüß]+", text.lower())


# ============================================================
# 16. LANGUAGE DETECTION PROXY
# ============================================================

LANGUAGE_MARKERS = {
    "de": {
        "der",
        "die",
        "das",
        "und",
        "ist",
        "sind",
        "für",
        "mit",
        "von",
        "eine",
        "einer",
        "im",
    },
    "en": {
        "the",
        "and",
        "is",
        "are",
        "for",
        "with",
        "of",
        "in",
        "to",
        "a",
        "an",
        "this",
    },
    "fr": {
        "le",
        "la",
        "les",
        "et",
        "est",
        "sont",
        "pour",
        "avec",
        "de",
        "des",
        "dans",
        "une",
    },
}


def language_score(
    answer,
    expected_language,
):
    """
    GOAL
    ----------------------------------------------------------
    Estimate whether the answer is in the requested language.

    INPUT
    ----------------------------------------------------------
    answer
    expected_language: de, en or fr

    OUTPUT
    ----------------------------------------------------------
    1.0 = expected language detected
    0.0 = another language detected
    0.5 = no reliable language signal
    """

    tokens = tokenize(answer)

    counts = {}

    for language, markers in LANGUAGE_MARKERS.items():
        counts[language] = sum(token in markers for token in tokens)

    predicted_language = max(counts, key=counts.get)

    if counts[predicted_language] == 0:
        return 0.5

    return 1.0 if predicted_language == expected_language else 0.0


# ============================================================
# 17. CITATION QUALITY PROXY
# ============================================================


def citation_score(
    answer,
    number_of_sources,
):
    """
    GOAL
    ----------------------------------------------------------
    Check whether citations exist and reference valid source
    numbers.

    INPUT
    ----------------------------------------------------------
    answer
    number_of_sources

    OUTPUT
    ----------------------------------------------------------
    1.0 = citations exist and all are valid
    0.5 = mixture of valid and invalid citations
    0.0 = no citations or all invalid

    NOTE
    ----------------------------------------------------------
    This checks citation syntax/validity, not whether every
    claim is supported by the cited source.
    """

    citations = [int(number) for number in re.findall(r"\[(\d+)\]", answer)]

    if not citations:
        return 0.0

    valid = [citation for citation in citations if (1 <= citation <= number_of_sources)]

    if len(valid) == len(citations):
        return 1.0

    if valid:
        return 0.5

    return 0.0


# ============================================================
# 18. LEXICAL GROUNDING PROXY
# ============================================================

STOPWORDS = LANGUAGE_MARKERS["de"] | LANGUAGE_MARKERS["en"] | LANGUAGE_MARKERS["fr"]


def lexical_grounding_score(
    answer,
    context,
):
    """
    GOAL
    ----------------------------------------------------------
    Estimate how much answer vocabulary appears in the
    retrieved context.

    INPUT
    ----------------------------------------------------------
    answer
    context

    OUTPUT
    ----------------------------------------------------------
    Score from 0 to 1.

    IMPORTANT
    ----------------------------------------------------------
    This is only a proxy. Good paraphrases can use different
    words while still being fully grounded.
    """

    clean_answer = re.sub(r"\[\d+\]", "", answer)

    answer_tokens = {
        token
        for token in tokenize(clean_answer)
        if (len(token) >= 4 and token not in STOPWORDS)
    }

    context_tokens = {
        token
        for token in tokenize(context)
        if (len(token) >= 4 and token not in STOPWORDS)
    }

    if not answer_tokens:
        return 0.0

    overlap = answer_tokens & context_tokens

    return min(len(overlap) / len(answer_tokens), 1.0)


# ============================================================
# 19. NUMERIC GROUNDING PROXY
# ============================================================


def normalize_number(number):
    """
    GOAL
    ----------------------------------------------------------
    Normalize decimal separators for numeric comparison.

    INPUT
    ----------------------------------------------------------
    Number string.

    OUTPUT
    ----------------------------------------------------------
    Normalized number string.
    """

    return number.replace(",", ".")


def numeric_grounding_score(
    answer,
    context,
):
    """
    GOAL
    ----------------------------------------------------------
    Check whether numbers stated in the answer also occur in
    the SFOE evidence.

    INPUT
    ----------------------------------------------------------
    answer
    context

    OUTPUT
    ----------------------------------------------------------
    Proportion of answer numbers found in context.

    NOTE
    ----------------------------------------------------------
    Citation numbers [1], [2], ... are removed first.
    """

    clean_answer = re.sub(r"\[\d+\]", "", answer)

    answer_numbers = {
        normalize_number(value)
        for value in re.findall(r"\b\d+(?:[.,]\d+)?\b", clean_answer)
    }

    context_numbers = {
        normalize_number(value) for value in re.findall(r"\b\d+(?:[.,]\d+)?\b", context)
    }

    if not answer_numbers:
        return 1.0

    supported = answer_numbers & context_numbers

    return len(supported) / len(answer_numbers)


# ============================================================
# 20. QUESTION RELEVANCE PROXY
# ============================================================


def question_relevance_score(
    question,
    answer,
):
    """
    GOAL
    ----------------------------------------------------------
    Estimate whether important question terms are reflected in
    the generated answer.

    INPUT
    ----------------------------------------------------------
    question
    answer

    OUTPUT
    ----------------------------------------------------------
    Score from 0 to 1.
    """

    question_tokens = {
        token
        for token in tokenize(question)
        if (len(token) >= 4 and token not in STOPWORDS)
    }

    answer_tokens = set(tokenize(answer))

    if not question_tokens:
        return 1.0

    overlap = question_tokens & answer_tokens

    return min(len(overlap) / len(question_tokens), 1.0)


# ============================================================
# 21. CONCISENESS
# ============================================================


def conciseness_score(answer):
    """
    GOAL
    ----------------------------------------------------------
    Reward useful answers that are not excessively long.

    INPUT
    ----------------------------------------------------------
    Generated answer.

    OUTPUT
    ----------------------------------------------------------
    score
    word_count
    """

    word_count = len(answer.split())

    if 15 <= word_count <= 180:
        score = 1.0

    elif 8 <= word_count <= 250:
        score = 0.7

    else:
        score = 0.3

    return score, word_count


# ============================================================
# 22. LATENCY SCORE
# ============================================================


def latency_score(latency):
    """
    GOAL
    ----------------------------------------------------------
    Convert response latency into a simple 0-1 score.

    INPUT
    ----------------------------------------------------------
    Latency in seconds.

    OUTPUT
    ----------------------------------------------------------
    Faster model → higher score.
    """

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
# 23. COMPLETE ANSWER EVALUATION
# ============================================================


def evaluate_answer(
    question,
    expected_language,
    answer,
    context,
    number_of_sources,
    latency,
):
    """
    GOAL
    ----------------------------------------------------------
    Calculate one preliminary automatic score for an answer.

    INPUT
    ----------------------------------------------------------
    question
    expected_language
    answer
    shared SFOE context
    number_of_sources
    latency

    OUTPUT
    ----------------------------------------------------------
    Individual metrics + weighted overall score.

    WEIGHTS
    ----------------------------------------------------------
    Groundedness:
        lexical grounding       25%
        numeric grounding       15%

    Answer quality:
        question relevance      15%
        conciseness             10%

    Citation validity           20%
    Language                    10%
    Latency                      5%

    TOTAL                       100%

    IMPORTANT
    ----------------------------------------------------------
    These are proxy metrics, not a definitive semantic judge.
    """

    citation = citation_score(answer, number_of_sources)

    language = language_score(answer, expected_language)

    lexical_grounding = lexical_grounding_score(answer, context)

    numeric_grounding = numeric_grounding_score(answer, context)

    relevance = question_relevance_score(question, answer)

    concise, word_count = conciseness_score(answer)

    latency_metric = latency_score(latency)

    overall_score = (
        0.25 * lexical_grounding
        + 0.15 * numeric_grounding
        + 0.15 * relevance
        + 0.10 * concise
        + 0.20 * citation
        + 0.10 * language
        + 0.05 * latency_metric
    )

    return {
        "lexical_grounding": lexical_grounding,
        "numeric_grounding": numeric_grounding,
        "question_relevance": relevance,
        "conciseness": concise,
        "citation_score": citation,
        "language_score": language,
        "latency_score": latency_metric,
        "word_count": word_count,
        "overall_score": overall_score,
    }


# ============================================================
# 24. BENCHMARK ONE QUESTION
# ============================================================


def benchmark_question(
    access_token,
    question_config,
):
    """
    GOAL
    ----------------------------------------------------------
    Benchmark every candidate model on one question.

    INPUT
    ----------------------------------------------------------
    Optional Cognito token.

    question_config:
        id
        language
        question

    PROCESS
    ----------------------------------------------------------
    1. Retrieve SFOE evidence ONCE.
    2. Normalize the current SFOE response.
    3. Build one shared context.
    4. Give the exact same prompt/context to every model.
    5. Evaluate every answer.

    OUTPUT
    ----------------------------------------------------------
    Detailed question-level benchmark result.
    """

    question_id = question_config["id"]

    language = question_config["language"]

    question = question_config["question"]

    print("\n")
    print("=" * 80)
    print(f"QUESTION {question_id}")
    print("=" * 80)

    print(f"\n{question}")

    # --------------------------------------------------------
    # STEP 1: REAL MCP RETRIEVAL - ONCE
    # --------------------------------------------------------

    print("\nRetrieving SFOE evidence...")

    search_data = retrieve_sfoe(
        question=question,
        access_token=access_token,
    )

    evidence = prepare_evidence(search_data)

    print(f"Gateway returned {search_data.get('result_count', len(evidence))} results.")

    print(f"Using {len(evidence)} unique passages for all models.")

    if not evidence:
        return {
            "question_id": question_id,
            "language": language,
            "question": question,
            "sources": [],
            "model_results": [],
            "error": "No retrieval results",
        }

    # --------------------------------------------------------
    # STEP 2: CREATE SHARED CONTEXT
    # --------------------------------------------------------

    context = build_context(evidence)

    prompt = build_prompt(question, language, context)

    model_results = []

    # --------------------------------------------------------
    # STEP 3: TEST EVERY MODEL WITH EXACTLY SAME EVIDENCE
    # --------------------------------------------------------

    for model in CANDIDATE_MODELS:
        print(f"\nTesting {model['provider']} | {model['name']}")

        generation = generate_answer(model["model_id"], prompt)

        if generation["status"] != "SUCCESS":
            print("   FAILED")

            print(f"   {generation['error']}")

            model_results.append(
                {
                    **model,
                    "status": "FAILED",
                    "error": generation["error"],
                }
            )

            continue

        answer = generation["answer"]

        latency = generation["latency"]

        metrics = evaluate_answer(
            question=question,
            expected_language=language,
            answer=answer,
            context=context,
            number_of_sources=len(evidence),
            latency=latency,
        )

        result = {
            **model,
            "status": "SUCCESS",
            "latency": latency,
            "answer": answer,
            **metrics,
        }

        model_results.append(result)

        print(f"   Score   : {metrics['overall_score']:.3f}")

        print(f"   Latency : {latency:.2f} s")

    return {
        "question_id": question_id,
        "language": language,
        "question": question,
        "sources": evidence,
        "model_results": model_results,
        "error": None,
    }


# ============================================================
# 25. RUN COMPLETE BENCHMARK
# ============================================================


def run_benchmark():
    """
    GOAL
    ----------------------------------------------------------
    Run all benchmark questions against all candidate models.

    INPUT
    ----------------------------------------------------------
    BENCHMARK_QUESTIONS
    CANDIDATE_MODELS
    current SFOE MCP Gateway

    OUTPUT
    ----------------------------------------------------------
    Detailed results for every question/model combination.
    """

    print("\n")
    print("=" * 80)
    print("SFOE RAG MODEL BENCHMARK")
    print("=" * 80)

    print(f"\nQuestions : {len(BENCHMARK_QUESTIONS)}")

    print(f"Models    : {len(CANDIDATE_MODELS)}")

    print(
        f"Total possible LLM calls: {len(BENCHMARK_QUESTIONS) * len(CANDIDATE_MODELS)}"
    )

    # --------------------------------------------------------
    # OPTIONAL COGNITO AUTHENTICATION
    # --------------------------------------------------------

    access_token = None

    if all(
        [
            CLIENT_ID,
            CLIENT_SECRET,
            TOKEN_URL,
        ]
    ):
        print("\nAuthenticating with Cognito...")

        access_token = fetch_access_token()

        print("Authentication successful.")

    else:
        print("\nCognito not configured.")

        print("Using open MCP Gateway without authentication.")

    results = []

    for question_config in BENCHMARK_QUESTIONS:
        result = benchmark_question(access_token, question_config)

        results.append(result)

    return results


# ============================================================
# 26. BUILD MODEL SUMMARY
# ============================================================


def build_model_summary(
    benchmark_results,
):
    """
    GOAL
    ----------------------------------------------------------
    Aggregate question-level results into one summary per model.

    INPUT
    ----------------------------------------------------------
    Full benchmark results.

    OUTPUT
    ----------------------------------------------------------
    Model-level ranking data:
        average score
        average latency
        success rate
        average component scores
    """

    grouped = {}

    for question_result in benchmark_results:
        for result in question_result["model_results"]:
            model_id = result["model_id"]

            if model_id not in grouped:
                grouped[model_id] = {
                    "provider": result["provider"],
                    "name": result["name"],
                    "model_id": model_id,
                    "results": [],
                }

            grouped[model_id]["results"].append(result)

    summaries = []

    total_questions = len(BENCHMARK_QUESTIONS)

    for model in grouped.values():
        successful = [
            result for result in model["results"] if (result["status"] == "SUCCESS")
        ]

        success_rate = len(successful) / total_questions

        if not successful:
            continue

        def average(field):
            return statistics.mean(result[field] for result in successful)

        summary = {
            "provider": model["provider"],
            "name": model["name"],
            "model_id": model["model_id"],
            "success_rate": success_rate,
            "average_score": average("overall_score"),
            "average_latency": average("latency"),
            "lexical_grounding": average("lexical_grounding"),
            "numeric_grounding": average("numeric_grounding"),
            "question_relevance": average("question_relevance"),
            "citation_score": average("citation_score"),
            "language_score": average("language_score"),
            "conciseness": average("conciseness"),
        }

        summaries.append(summary)

    summaries.sort(
        key=lambda item: (
            item["average_score"],
            item["success_rate"],
            -item["average_latency"],
        ),
        reverse=True,
    )

    return summaries


# ============================================================
# 27. SAVE DETAILED JSON
# ============================================================


def save_json(
    benchmark_results,
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Save full benchmark evidence and model answers.

    INPUT
    ----------------------------------------------------------
    Detailed benchmark results + summaries.

    OUTPUT
    ----------------------------------------------------------
    benchmark_results/rag_benchmark_details.json
    """

    os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)

    path = os.path.join(OUTPUT_DIRECTORY, "rag_benchmark_details.json")

    data = {
        "region": AWS_REGION,
        "gateway_url": GATEWAY_URL,
        "tool_name": TOOL_NAME,
        "top_k_context": TOP_K_CONTEXT,
        "models": CANDIDATE_MODELS,
        "questions": BENCHMARK_QUESTIONS,
        "results": benchmark_results,
        "summary": summaries,
    }

    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )

    return path


# ============================================================
# 28. SAVE QUESTION-LEVEL CSV
# ============================================================


def save_scores_csv(
    benchmark_results,
):
    """
    GOAL
    ----------------------------------------------------------
    Save one CSV row for every model/question combination.

    INPUT
    ----------------------------------------------------------
    Detailed benchmark results.

    OUTPUT
    ----------------------------------------------------------
    benchmark_results/rag_benchmark_scores.csv
    """

    os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)

    path = os.path.join(OUTPUT_DIRECTORY, "rag_benchmark_scores.csv")

    fields = [
        "question_id",
        "language",
        "question",
        "provider",
        "model_name",
        "model_id",
        "status",
        "overall_score",
        "lexical_grounding",
        "numeric_grounding",
        "question_relevance",
        "citation_score",
        "language_score",
        "conciseness",
        "latency",
        "word_count",
        "answer",
        "error",
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)

        writer.writeheader()

        for question_result in benchmark_results:
            for result in question_result["model_results"]:
                writer.writerow(
                    {
                        "question_id": question_result["question_id"],
                        "language": question_result["language"],
                        "question": question_result["question"],
                        "provider": result.get("provider"),
                        "model_name": result.get("name"),
                        "model_id": result.get("model_id"),
                        "status": result.get("status"),
                        "overall_score": result.get("overall_score"),
                        "lexical_grounding": result.get("lexical_grounding"),
                        "numeric_grounding": result.get("numeric_grounding"),
                        "question_relevance": result.get("question_relevance"),
                        "citation_score": result.get("citation_score"),
                        "language_score": result.get("language_score"),
                        "conciseness": result.get("conciseness"),
                        "latency": result.get("latency"),
                        "word_count": result.get("word_count"),
                        "answer": result.get("answer"),
                        "error": result.get("error"),
                    }
                )

    return path


# ============================================================
# 29. SAVE MODEL SUMMARY CSV
# ============================================================


def save_summary_csv(
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Save final model-level ranking.

    INPUT
    ----------------------------------------------------------
    Aggregated model summaries.

    OUTPUT
    ----------------------------------------------------------
    benchmark_results/rag_benchmark_summary.csv
    """

    os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)

    path = os.path.join(OUTPUT_DIRECTORY, "rag_benchmark_summary.csv")

    if not summaries:
        return path

    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(summaries[0].keys()))

        writer.writeheader()

        writer.writerows(summaries)

    return path


# ============================================================
# 30. PRINT FINAL RANKING
# ============================================================


def print_ranking(
    summaries,
):
    """
    GOAL
    ----------------------------------------------------------
    Show the final preliminary model ranking.

    INPUT
    ----------------------------------------------------------
    Aggregated benchmark summaries.

    OUTPUT
    ----------------------------------------------------------
    Human-readable ranking + preliminary recommendation.
    """

    print("\n")
    print("=" * 80)
    print("FINAL RAG MODEL RANKING")
    print("=" * 80)

    if not summaries:
        print("\nNo model successfully completed the benchmark.")

        return

    for rank, model in enumerate(summaries, start=1):
        print(f"\n{rank}. {model['provider']} - {model['name']}")

        print(f"   Model ID          : {model['model_id']}")

        print(f"   Overall score     : {model['average_score']:.3f}")

        print(f"   Grounding lexical : {model['lexical_grounding']:.3f}")

        print(f"   Grounding numeric : {model['numeric_grounding']:.3f}")

        print(f"   Relevance         : {model['question_relevance']:.3f}")

        print(f"   Citations         : {model['citation_score']:.3f}")

        print(f"   Language          : {model['language_score']:.3f}")

        print(f"   Avg. latency      : {model['average_latency']:.2f} s")

        print(f"   Success rate      : {model['success_rate']:.0%}")

    winner = summaries[0]

    print("\n")
    print("=" * 80)
    print("PRELIMINARY RECOMMENDED MODEL")
    print("=" * 80)

    print(f"\n{winner['provider']} - {winner['name']}")

    print("\nModel ID:")

    print(winner["model_id"])

    print("\nTo use it in app.py / .env:")

    print(f"BEDROCK_MODEL_ID={winner['model_id']}")

    print("\nIMPORTANT:")

    print(
        "This ranking uses automatic proxy metrics. "
        "It is useful for initial comparison, but it is "
        "not yet a definitive semantic quality evaluation."
    )


# ============================================================
# 31. MAIN
# ============================================================


def main():
    """
    GOAL
    ----------------------------------------------------------
    Run the complete real SFOE RAG benchmark.

    INPUT
    ----------------------------------------------------------
    - current SFOE MCP Gateway
    - optional Cognito credentials
    - AWS credentials
    - candidate LLMs
    - multilingual benchmark questions

    PROCESS
    ----------------------------------------------------------
    1. Optionally authenticate.
    2. Retrieve each question once.
    3. Resolve current SFOE source metadata.
    4. Give identical evidence to every model.
    5. Generate grounded answers.
    6. Evaluate answers.
    7. Aggregate scores.
    8. Save JSON/CSV results.
    9. Print preliminary winner.

    OUTPUT
    ----------------------------------------------------------
    Terminal ranking + benchmark result files.
    """

    try:
        benchmark_results = run_benchmark()

        summaries = build_model_summary(benchmark_results)

        details_path = save_json(benchmark_results, summaries)

        scores_path = save_scores_csv(benchmark_results)

        summary_path = save_summary_csv(summaries)

        print_ranking(summaries)

        print("\n")
        print("=" * 80)
        print("RESULT FILES")
        print("=" * 80)

        print(f"\nDetails : {details_path}")

        print(f"Scores  : {scores_path}")

        print(f"Summary : {summary_path}")

    except requests.exceptions.RequestException as error:
        print("\nMCP / HTTP connection error:")

        print(error)

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
# 32. START SCRIPT
# ============================================================
# INPUT
# ------------------------------------------------------------
# Run:
#
#     python benchmark_rag_models.py
#
# OUTPUT
# ------------------------------------------------------------
# Real multilingual SFOE RAG model benchmark.
# ============================================================

if __name__ == "__main__":
    main()
