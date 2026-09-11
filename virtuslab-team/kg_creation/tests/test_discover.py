import json

from kb_graph.discover import S3DataSource, _from_classic_s3, _from_managed_connector, resolve_s3_data_source

# Real shape observed from a live KB while building this tool (bucket/account
# renamed here — not the actual production values).
MANAGED_CONNECTOR_DATA_SOURCE = {
    "dataSourceId": "UR5EQP3BUU",
    "name": "data-source-example",
    "dataSourceConfiguration": {
        "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
        "managedKnowledgeBaseConnectorConfiguration": {
            "connectorParameters": json.dumps(
                {
                    "type": "S3",
                    "filterConfiguration": {"maxFileSizeInMegaBytes": "500"},
                    "connectionConfiguration": {
                        "bucketName": "example-data-bucket",
                        "bucketOwnerAccountId": "111111111111",
                        "bucketArn": "arn:aws:s3:::example-data-bucket",
                    },
                    "aclEnabled": False,
                    "version": "1",
                }
            )
        },
    },
}

CLASSIC_S3_DATA_SOURCE = {
    "dataSourceId": "DS123",
    "dataSourceConfiguration": {
        "type": "S3",
        "s3Configuration": {
            "bucketArn": "arn:aws:s3:::classic-bucket",
            "inclusionPrefixes": ["docs/"],
        },
    },
}

NON_S3_MANAGED_DATA_SOURCE = {
    "dataSourceId": "DS456",
    "dataSourceConfiguration": {
        "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
        "managedKnowledgeBaseConnectorConfiguration": {
            "connectorParameters": json.dumps({"type": "SHAREPOINT"})
        },
    },
}


def test_from_managed_connector_parses_s3_bucket_and_owner():
    result = _from_managed_connector(
        MANAGED_CONNECTOR_DATA_SOURCE, MANAGED_CONNECTOR_DATA_SOURCE["dataSourceConfiguration"]
    )
    assert result == S3DataSource(
        data_source_id="UR5EQP3BUU",
        bucket="example-data-bucket",
        prefix="",
        bucket_owner_account_id="111111111111",
    )


def test_from_classic_s3_parses_bucket_and_prefix():
    result = _from_classic_s3(CLASSIC_S3_DATA_SOURCE, CLASSIC_S3_DATA_SOURCE["dataSourceConfiguration"])
    assert result == S3DataSource(data_source_id="DS123", bucket="classic-bucket", prefix="docs/")


def test_from_managed_connector_returns_none_for_non_s3_connector():
    result = _from_managed_connector(
        NON_S3_MANAGED_DATA_SOURCE, NON_S3_MANAGED_DATA_SOURCE["dataSourceConfiguration"]
    )
    assert result is None


class _FakeBedrockAgent:
    def __init__(self, data_source: dict):
        self._data_source = data_source

    def get_data_source(self, knowledgeBaseId, dataSourceId):
        return {"dataSource": self._data_source}


def test_resolve_s3_data_source_handles_managed_connector_end_to_end():
    agent = _FakeBedrockAgent(MANAGED_CONNECTOR_DATA_SOURCE)
    result = resolve_s3_data_source(agent, "KB1", "UR5EQP3BUU")
    assert result.bucket == "example-data-bucket"
    assert result.bucket_owner_account_id == "111111111111"
