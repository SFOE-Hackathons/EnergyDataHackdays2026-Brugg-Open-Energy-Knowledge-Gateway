import os
import time
import boto3

from dotenv import load_dotenv
from botocore.exceptions import ClientError


# ============================================================
# GOAL OF THIS SCRIPT
# ============================================================
# Discover which Amazon Bedrock text-generation models can
# actually be invoked by the current AWS account.
#
# This script:
#
# 1. Loads AWS credentials and region from .env
# 2. Lists Bedrock foundation models
# 3. Filters out obvious non-generative models
# 4. Tries a small test prompt using Bedrock Converse API
# 5. Classifies the result:
#
#    AVAILABLE
#    ACCESS_DENIED
#    VALIDATION_ERROR
#    RESPONSE_FORMAT_ERROR
#    OTHER_ERROR
#
# 6. Prints a clean summary
#
#
# IMPORTANT
# ============================================================
# A ValidationException does NOT necessarily mean that a model
# is unavailable.
#
# It may mean:
#
# - the model requires an inference profile
# - the model does not support the Converse API in this form
# - the request parameters are not valid for that model
#
# Therefore, only ACCESS_DENIED can be interpreted clearly as
# an authorization/access problem.
# ============================================================


# ============================================================
# INPUT
# ============================================================
# Values expected in .env:
#
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# AWS_REGION=eu-central-1
#
#
# OUTPUT
# ============================================================
# For each model:
#
# - Provider
# - Model name
# - Model ID
# - Status
# - Latency
# - Error message if applicable
#
# ============================================================


# ============================================================
# 1. LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv(override=True)

AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")


# ============================================================
# 2. CREATE AWS BEDROCK CLIENTS
# ============================================================
#
# "bedrock"
#     Control-plane client.
#     Used to list foundation models.
#
# "bedrock-runtime"
#     Runtime client.
#     Used to actually invoke models.
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
# 3. FILTER MODELS
# ============================================================


def is_text_generation_model(model):
    """
    GOAL
    ----------------------------------------------------------
    Decide whether a Bedrock model is probably suitable for
    generating text answers.

    INPUT
    ----------------------------------------------------------
    model:
        One model summary returned by
        list_foundation_models().

    OUTPUT
    ----------------------------------------------------------
    True:
        Model is probably a text-generation model.

    False:
        Model is an embedding, reranking, or non-text model.

    PROCESS
    ----------------------------------------------------------
    1. Check model name and model ID.
    2. Exclude known embedding / reranking models.
    3. Check whether TEXT is an output modality.
    """

    model_id = model.get("modelId", "").lower()

    model_name = model.get("modelName", "").lower()

    # Models that should not be tested as answering LLMs
    excluded_keywords = [
        "embed",
        "embedding",
        "rerank",
    ]

    for keyword in excluded_keywords:
        if keyword in model_id or keyword in model_name:
            return False

    output_modalities = model.get("outputModalities", [])

    # Only keep models capable of producing text
    return "TEXT" in output_modalities


# ============================================================
# 4. EXTRACT TEXT FROM CONVERSE RESPONSE
# ============================================================


def extract_text_from_converse_response(response):
    """
    GOAL
    ----------------------------------------------------------
    Extract generated text from a successful Bedrock Converse
    API response.

    INPUT
    ----------------------------------------------------------
    response:
        Dictionary returned by bedrock_runtime.converse().

    OUTPUT
    ----------------------------------------------------------
    Generated text as a string.

    ERROR
    ----------------------------------------------------------
    Raises ValueError if the response format is unexpected.
    """

    try:
        content = response["output"]["message"]["content"]

        # Search for the first normal text element
        for item in content:
            if "text" in item:
                return item["text"]

        raise ValueError("No 'text' field found in Converse response content.")

    except (KeyError, TypeError, IndexError) as error:
        raise ValueError(f"Unexpected Converse response structure: {error}")


# ============================================================
# 5. TEST ONE MODEL
# ============================================================


def test_model(model_id):
    """
    GOAL
    ----------------------------------------------------------
    Test whether one Bedrock model can be invoked using the
    Converse API.

    INPUT
    ----------------------------------------------------------
    model_id:
        Bedrock foundation model ID.

    OUTPUT
    ----------------------------------------------------------
    Dictionary containing:

        status:
            AVAILABLE
            ACCESS_DENIED
            VALIDATION_ERROR
            RESPONSE_FORMAT_ERROR
            OTHER_ERROR

        latency:
            Request duration in seconds.

        message:
            Generated response or error description.

    PROCESS
    ----------------------------------------------------------
    1. Send a very small prompt.
    2. Measure latency.
    3. Classify success or failure.
    """

    test_prompt = "Reply with exactly: OK"

    start_time = time.perf_counter()

    try:
        response = bedrock_runtime.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": test_prompt}],
                }
            ],
            inferenceConfig={
                "maxTokens": 20,
                "temperature": 0,
            },
        )

        latency = time.perf_counter() - start_time

        try:
            generated_text = extract_text_from_converse_response(response)

        except ValueError as error:
            return {
                "status": "RESPONSE_FORMAT_ERROR",
                "latency": latency,
                "message": str(error),
            }

        return {
            "status": "AVAILABLE",
            "latency": latency,
            "message": generated_text,
        }

    except ClientError as error:
        latency = time.perf_counter() - start_time

        error_code = error.response.get("Error", {}).get("Code", "Unknown")

        error_message = error.response.get("Error", {}).get("Message", str(error))

        # ----------------------------------------------------
        # ACCESS DENIED
        # ----------------------------------------------------

        if error_code == "AccessDeniedException":
            return {
                "status": "ACCESS_DENIED",
                "latency": latency,
                "message": error_message,
            }

        # ----------------------------------------------------
        # VALIDATION ERROR
        # ----------------------------------------------------
        # Important:
        # The model may still be usable, but our Converse
        # request may not be valid for it.
        # ----------------------------------------------------

        if error_code == "ValidationException":
            return {
                "status": "VALIDATION_ERROR",
                "latency": latency,
                "message": error_message,
            }

        # ----------------------------------------------------
        # OTHER AWS API ERROR
        # ----------------------------------------------------

        return {
            "status": "OTHER_ERROR",
            "latency": latency,
            "message": (f"{error_code}: {error_message}"),
        }

    except Exception as error:
        latency = time.perf_counter() - start_time

        return {
            "status": "OTHER_ERROR",
            "latency": latency,
            "message": str(error),
        }


# ============================================================
# 6. TEST ALL TEXT-GENERATION MODELS
# ============================================================


def evaluate_models():
    """
    GOAL
    ----------------------------------------------------------
    List Bedrock foundation models and test each probable
    text-generation model.

    INPUT
    ----------------------------------------------------------
    AWS account
    AWS region
    Bedrock model catalog

    OUTPUT
    ----------------------------------------------------------
    List of dictionaries.

    Each dictionary contains:

        provider
        model_name
        model_id
        status
        latency
        message
    """

    response = bedrock.list_foundation_models()

    models = response["modelSummaries"]

    evaluated_models = []

    print(f"\nTesting Bedrock text-generation models in {AWS_REGION}\n")

    for model in models:
        if not is_text_generation_model(model):
            continue

        provider = model.get("providerName", "Unknown")

        model_name = model.get("modelName", "Unknown")

        model_id = model.get("modelId", "Unknown")

        print(f"Testing: {provider} | {model_name}")

        result = test_model(model_id)

        evaluated_models.append(
            {
                "provider": provider,
                "model_name": model_name,
                "model_id": model_id,
                "status": result["status"],
                "latency": result["latency"],
                "message": result["message"],
            }
        )

        # ----------------------------------------------------
        # CLEAN TERMINAL OUTPUT
        # ----------------------------------------------------

        status = result["status"]

        if status == "AVAILABLE":
            print(f"   AVAILABLE | {result['latency']:.2f} s | {model_id}")

        elif status == "ACCESS_DENIED":
            print("   ACCESS DENIED")

        elif status == "VALIDATION_ERROR":
            print("   VALIDATION ERROR (may require different invocation method)")

        elif status == "RESPONSE_FORMAT_ERROR":
            print("   RESPONSE FORMAT ERROR")

        else:
            print(f"   OTHER ERROR")

    return evaluated_models


# ============================================================
# 7. PRINT AVAILABLE MODELS
# ============================================================


def print_available_models(models):
    """
    GOAL
    ----------------------------------------------------------
    Print only confirmed invocable models.

    INPUT
    ----------------------------------------------------------
    models:
        Results from evaluate_models().

    OUTPUT
    ----------------------------------------------------------
    Available models sorted by latency.
    """

    available_models = [model for model in models if model["status"] == "AVAILABLE"]

    available_models.sort(key=lambda model: model["latency"])

    print("\n")
    print("=" * 80)
    print("CONFIRMED INVOCABLE MODELS")
    print("=" * 80)

    if not available_models:
        print("\nNo models were successfully invoked using Converse.")

        return

    for index, model in enumerate(available_models, start=1):
        print(f"\n{index}. {model['provider']} - {model['model_name']}")

        print(f"   Model ID : {model['model_id']}")

        print(f"   Latency  : {model['latency']:.2f} s")

        print(f"   Response : {model['message']}")


# ============================================================
# 8. PRINT MODELS THAT NEED FURTHER INVESTIGATION
# ============================================================


def print_models_to_investigate(models):
    """
    GOAL
    ----------------------------------------------------------
    Show models that are NOT confirmed unavailable but failed
    because of validation or response-format issues.

    INPUT
    ----------------------------------------------------------
    models:
        Results from evaluate_models().

    OUTPUT
    ----------------------------------------------------------
    Models that may need:
    - different invocation parameters
    - inference profile
    - another API
    """

    interesting_statuses = {
        "VALIDATION_ERROR",
        "RESPONSE_FORMAT_ERROR",
        "OTHER_ERROR",
    }

    models_to_investigate = [
        model for model in models if model["status"] in interesting_statuses
    ]

    print("\n")
    print("=" * 80)
    print("MODELS TO INVESTIGATE")
    print("=" * 80)

    if not models_to_investigate:
        print("\nNo additional models need investigation.")

        return

    for model in models_to_investigate:
        print(f"\n{model['provider']} - {model['model_name']}")

        print(f"Model ID : {model['model_id']}")

        print(f"Status   : {model['status']}")

        print(f"Message  : {model['message']}")


# ============================================================
# 9. PRINT ACCESS-DENIED MODELS
# ============================================================


def print_access_denied_models(models):
    """
    GOAL
    ----------------------------------------------------------
    Show models that the current AWS account clearly does not
    have permission to invoke.

    INPUT
    ----------------------------------------------------------
    models:
        Results from evaluate_models().

    OUTPUT
    ----------------------------------------------------------
    List of access-denied models.
    """

    denied_models = [model for model in models if model["status"] == "ACCESS_DENIED"]

    print("\n")
    print("=" * 80)
    print("ACCESS-DENIED MODELS")
    print("=" * 80)

    if not denied_models:
        print("\nNo access-denied models.")

        return

    for model in denied_models:
        print(f"\n- {model['provider']} | {model['model_name']} | {model['model_id']}")


# ============================================================
# 10. MAIN
# ============================================================


def main():
    """
    GOAL
    ----------------------------------------------------------
    Run the complete Bedrock model evaluation workflow.

    INPUT
    ----------------------------------------------------------
    AWS credentials
    AWS region

    PROCESS
    ----------------------------------------------------------
    1. List models.
    2. Filter text-generation models.
    3. Test each model with Converse.
    4. Classify results.
    5. Print clean summaries.

    OUTPUT
    ----------------------------------------------------------
    Three groups:

    1. Confirmed invocable models
    2. Models requiring further investigation
    3. Models clearly denied by AWS permissions
    """

    models = evaluate_models()

    print_available_models(models)

    print_models_to_investigate(models)

    print_access_denied_models(models)


# ============================================================
# 11. START SCRIPT
# ============================================================
#
# INPUT:
#     python evaluate_models.py
#
# OUTPUT:
#     Bedrock model evaluation report in the terminal.
#
# ============================================================

if __name__ == "__main__":
    main()
