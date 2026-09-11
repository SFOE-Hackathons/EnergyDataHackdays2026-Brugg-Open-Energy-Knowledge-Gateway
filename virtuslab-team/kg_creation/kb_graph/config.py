"""Runtime configuration, resolved entirely from environment variables.

No real AWS IDs/names live in this repo — every value here is a placeholder
until the caller supplies real ones via `.env` (untracked) or the shell
environment. See `.env.example` for the full list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_REQUIRED = ("BEDROCK_KB_ID",)

# Passed to kg-gen as its `context`. Without a domain hint the relation pass
# invents endpoints freely — verb phrases, clauses and bare years turned up as
# "entities" in the first build. Steering it costs nothing per call.
#
# Assumes an English-language corpus. Source documents in another language will
# yield terms in that language, which will not match an English query: term
# identity here is normalized text, not meaning.
DEFAULT_EXTRACTION_CONTEXT = (
    "Swiss federal energy-sector monitoring and policy documents, in German or French or English (mostly German)."
    "Language used in the file is always the same as the title of the file."
    "Entities are named things: organizations and authorities, laws, ordinances "
    "and bills, technologies, facilities, energy carriers, and named targets or "
    "metrics. Do not treat actions, verb phrases, or bare dates as entities."
)


class MissingConfig(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    aws_profile: str | None
    aws_region: str | None
    kb_id: str
    kb_data_source_id: str | None
    kg_base_uri: str
    kg_output_path: Path
    pdf_max_concurrency: int
    pdf_text_max_chars: int
    pdf_scratch_dir: Path
    pdf_manifest_path: Path
    checkpoint_dir: Path
    entity_extraction_enabled: bool
    entity_extraction_model: str
    entity_extraction_api_key: str | None
    entity_extraction_temperature: float
    entity_extraction_max_chars: int
    entity_extraction_chunk_size: int
    entity_extraction_cluster: bool
    entity_extraction_context: str

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> "Settings":
        if env_file:
            load_dotenv(env_file, override=False)

        missing = [name for name in _REQUIRED if not os.environ.get(name)]
        if missing:
            raise MissingConfig(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill in real values."
            )

        return cls(
            aws_profile=os.environ.get("AWS_PROFILE"),
            aws_region=os.environ.get("AWS_REGION"),
            kb_id=os.environ["BEDROCK_KB_ID"],
            kb_data_source_id=os.environ.get("BEDROCK_KB_DATA_SOURCE_ID") or None,
            kg_base_uri=os.environ.get("KG_BASE_URI", "https://example.org/kg/"),
            kg_output_path=Path(
                os.environ.get("KG_OUTPUT_PATH", "data/knowledge-graph.ttl")
            ),
            pdf_max_concurrency=int(os.environ.get("PDF_MAX_CONCURRENCY", "3")),
            pdf_text_max_chars=int(os.environ.get("PDF_TEXT_MAX_CHARS", "500000")),
            pdf_scratch_dir=Path(os.environ.get("PDF_SCRATCH_DIR", "data/scratch")),
            checkpoint_dir=Path(os.environ.get("CHECKPOINT_DIR", "data/checkpoint")),
            pdf_manifest_path=Path(
                os.environ.get("PDF_MANIFEST_PATH", "data/pdf-ingest-manifest.json")
            ),
            entity_extraction_enabled=os.environ.get(
                "ENTITY_EXTRACTION_ENABLED", "false"
            ).lower()
            == "true",
            entity_extraction_model=os.environ.get(
                "ENTITY_EXTRACTION_MODEL", "openai/gpt-4o"
            ),
            entity_extraction_api_key=os.environ.get("ENTITY_EXTRACTION_API_KEY")
            or None,
            entity_extraction_temperature=float(
                os.environ.get("ENTITY_EXTRACTION_TEMPERATURE", "0.0")
            ),
            # 0 = no cap. A cap silently indexes only the head of every
            # document, which makes the term index confidently incomplete
            # rather than merely smaller.
            entity_extraction_max_chars=int(
                os.environ.get("ENTITY_EXTRACTION_MAX_CHARS", "0")
            ),
            entity_extraction_chunk_size=int(
                os.environ.get("ENTITY_EXTRACTION_CHUNK_SIZE", "5000")
            ),
            entity_extraction_cluster=os.environ.get(
                "ENTITY_EXTRACTION_CLUSTER", "true"
            ).lower()
            == "true",
            entity_extraction_context=os.environ.get("ENTITY_EXTRACTION_CONTEXT")
            or DEFAULT_EXTRACTION_CONTEXT,
        )
