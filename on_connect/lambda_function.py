import json
import os
import boto3
import logging
from datetime import datetime, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
connections_table = dynamodb.Table(os.environ['CONNECTIONS_TABLE'])

def lambda_handler(event, context):
    try:
        connection_id = event['requestContext']['connectionId']

        # Extract userId from query string or authorizer context
        query_params = event.get('queryStringParameters') or {}
        user_id = query_params.get('userId')

        # If using Cognito authorizer, extract from authorizer context
        if not user_id:
            authorizer = event['requestContext'].get('authorizer', {})
            user_id = authorizer.get('claims', {}).get('sub') or authorizer.get('principalId')

        if not user_id:
            logger.error("No userId provided")
            return {'statusCode': 401, 'body': 'Unauthorized'}

        # Store connection with TTL (24 hours from now)
        ttl = int((datetime.utcnow() + timedelta(hours=24)).timestamp())

        connections_table.put_item(
            Item={
                'connectionId': connection_id,
                'userId': user_id,
                'connectedAt': datetime.utcnow().isoformat(),
                'ttl': ttl
            }
        )

        logger.info(json.dumps({
            'event': 'connection_established',
            'connectionId': connection_id,
            'userId': user_id
        }))

        return {'statusCode': 200, 'body': 'Connected'}

    except Exception as e:
        logger.error(json.dumps({
            'event': 'connection_error',
            'error': str(e),
            'connectionId': event['requestContext'].get('connectionId')
        }))
        return {'statusCode': 500, 'body': 'Connection failed'}
