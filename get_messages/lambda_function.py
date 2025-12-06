import json
import os
import boto3
import logging
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
messages_table = dynamodb.Table(os.environ['MESSAGES_TABLE'])

class DecimalEncoder(json.JSONEncoder):
    """Handle Decimal types from DynamoDB."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def lambda_handler(event, context):
    """
    GET /messages?chatId=alice#bob&limit=50&lastTimestamp=xxx

    Returns messages for a chat, paginated by timestamp.
    """
    try:
        # Parse query parameters
        params = event.get('queryStringParameters') or {}
        chat_id = params.get('chatId')
        limit = min(int(params.get('limit', 50)), 100)  # Max 100 messages
        last_timestamp = params.get('lastTimestamp')  # For pagination

        if not chat_id:
            return {
                'statusCode': 400,
                'headers': cors_headers(),
                'body': json.dumps({'error': 'chatId is required'})
            }

        # Query messages from DynamoDB
        query_params = {
            'KeyConditionExpression': 'chatId = :cid',
            'ExpressionAttributeValues': {':cid': chat_id},
            'Limit': limit,
            'ScanIndexForward': False  # Most recent first
        }

        # Pagination: get messages before lastTimestamp
        if last_timestamp:
            query_params['KeyConditionExpression'] += ' AND #ts < :ts'
            query_params['ExpressionAttributeNames'] = {'#ts': 'timestamp'}
            query_params['ExpressionAttributeValues'][':ts'] = Decimal(last_timestamp)

        response = messages_table.query(**query_params)

        # Format messages for frontend
        messages = []
        for item in response.get('Items', []):
            messages.append({
                'messageId': item.get('messageId'),
                'chatId': item.get('chatId'),
                'senderId': item.get('senderId'),
                'text': item.get('text', ''),
                'timestamp': float(item.get('timestamp', 0)),
                'fileKey': item.get('fileKey'),
                'flagged': item.get('flagged', False),
                'flagSeverity': item.get('flagSeverity', 'none')
            })

        # Reverse to get chronological order (oldest first)
        messages.reverse()

        # Check if there are more messages
        has_more = 'LastEvaluatedKey' in response

        logger.info(json.dumps({
            'event': 'messages_fetched',
            'chatId': chat_id,
            'count': len(messages),
            'hasMore': has_more
        }))

        return {
            'statusCode': 200,
            'headers': cors_headers(),
            'body': json.dumps({
                'messages': messages,
                'hasMore': has_more
            }, cls=DecimalEncoder)
        }

    except Exception as e:
        logger.error(json.dumps({
            'event': 'get_messages_error',
            'error': str(e)
        }))
        return {
            'statusCode': 500,
            'headers': cors_headers(),
            'body': json.dumps({'error': 'Failed to fetch messages'})
        }

def cors_headers():
    """Return CORS headers for browser requests."""
    return {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'GET,OPTIONS'
    }
