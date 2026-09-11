"""boto3 session/client construction from Settings — no hardcoded region/profile."""
from __future__ import annotations

import boto3

from .config import Settings


def build_session(settings: Settings) -> boto3.Session:
    return boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)


def bedrock_agent_client(session: boto3.Session):
    return session.client("bedrock-agent")


def bedrock_agent_runtime_client(session: boto3.Session):
    return session.client("bedrock-agent-runtime")


def s3_client(session: boto3.Session):
    return session.client("s3")
