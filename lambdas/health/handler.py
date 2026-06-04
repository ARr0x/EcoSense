import json
import os


def handler(event, context):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "status": "ok",
            "region": os.environ.get("AWS_REGION", "unknown"),
            "service": "EcoSense",
        }),
    }
