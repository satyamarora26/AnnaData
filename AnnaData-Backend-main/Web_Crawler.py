"""
AWS Bedrock knowledge base lookup (government schemes, cold storage).

The boto3 client is built lazily so that missing AWS credentials do not raise at
import time. query_kb returns the answer text or None; the agent falls through
to its normal tool path when it gets None.

boto3 itself is imported lazily too. Retrieval runs on pgvector now and this
path is only a fallback for anyone who still has Bedrock configured, so paying
for a large AWS SDK in the container image - and in every cold start - to
support a route almost nobody takes is the wrong trade.
"""
from config import (
    KNOWLEDGE_BASE_ID,
    AWS_REGION,
    AWS_ACCESS_KEY,
    AWS_SECRET_KEY,
)

MODEL_ARN = f"arn:aws:bedrock:{AWS_REGION}::foundation-model/meta.llama3-70b-instruct-v1:0"

_client = None


def is_available() -> bool:
    return bool(KNOWLEDGE_BASE_ID and AWS_ACCESS_KEY and AWS_SECRET_KEY)


def _get_client():
    global _client
    if _client is None:
        try:
            import boto3
        except ImportError:
            print("boto3 is not installed; the Bedrock fallback is unavailable")
            return None
        _client = boto3.client(
            "bedrock-agent-runtime",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
        )
    return _client


def query_kb(question: str) -> str | None:
    """Retrieve from the knowledge base and generate an answer, or None."""
    if not is_available():
        print("Knowledge base skipped: AWS credentials or KNOWLEDGE_BASE_ID not set")
        return None

    client = _get_client()
    if client is None:
        return None

    try:
        response = client.retrieve_and_generate(
            input={"text": question},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": KNOWLEDGE_BASE_ID,
                    "modelArn": MODEL_ARN,
                    "retrievalConfiguration": {
                        "vectorSearchConfiguration": {"numberOfResults": 8}
                    },
                    "generationConfiguration": {
                        "inferenceConfig": {
                            "textInferenceConfig": {
                                "temperature": 0.2,
                                "topP": 0.9,
                                "maxTokens": 1024,
                            }
                        }
                    },
                    "orchestrationConfiguration": {
                        "inferenceConfig": {
                            "textInferenceConfig": {
                                "temperature": 0.2,
                                "topP": 0.9,
                                "maxTokens": 512,
                            }
                        }
                    },
                },
            },
        )
        return response["output"]["text"]

    except Exception as e:
        print(f"Knowledge base query failed: {e}")
        return None
