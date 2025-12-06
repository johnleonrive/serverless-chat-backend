import json
import os
import boto3
import logging
import time
import uuid
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

connections_table = dynamodb.Table(os.environ['CONNECTIONS_TABLE'])
messages_table = dynamodb.Table(os.environ['MESSAGES_TABLE'])

# Moderation function name (set via environment variable)
MODERATION_FUNCTION = os.environ.get('MODERATION_FUNCTION', '9-moderation')


def moderate_message(text: str, message_id: str, sender_id: str, chat_id: str, timestamp: float) -> dict:
    """
    Invoke the moderation Lambda to analyze the message.
    Returns moderation result with flagged status and severity.
    """
    try:
        response = lambda_client.invoke(
            FunctionName=MODERATION_FUNCTION,
            InvocationType='RequestResponse',
            Payload=json.dumps({
                'text': text,
                'messageId': message_id,
                'senderId': sender_id,
                'chatId': chat_id,
                'timestamp': timestamp
            })
        )

        result = json.loads(response['Payload'].read())
        logger.info(f"Raw moderation response: {json.dumps(result)}")

        # Handle Lambda response format
        if 'body' in result:
            parsed = json.loads(result['body'])
            logger.info(f"Parsed moderation result: {json.dumps(parsed)}")
            return parsed
        return result

    except Exception as e:
        logger.error(f"Moderation failed: {e}")
        # On failure, allow the message through but log the error
        return {
            'flagged': False,
            'severity': 'none',
            'error': str(e)
        }

# API Gateway Management API client (created per request)
def get_apigw_client(endpoint):
    return boto3.client('apigatewaymanagementapi', endpoint_url=endpoint)

def lambda_handler(event, context):
    try:
        connection_id = event['requestContext']['connectionId']
        domain_name = event['requestContext']['domainName']
        stage = event['requestContext']['stage']
        endpoint_url = f"https://{domain_name}/{stage}"

        # Parse message payload
        body = json.loads(event.get('body', '{}'))
        chat_id = body.get('chatId')
        text = body.get('text', '')
        file_key = body.get('fileKey')

        # Validation
        if not chat_id:
            return {'statusCode': 400, 'body': 'chatId required'}

        if len(json.dumps(body)) > 2048:
            return {'statusCode': 413, 'body': 'Payload too large'}

        # Get sender's userId from connection
        conn_response = connections_table.get_item(Key={'connectionId': connection_id})
        if 'Item' not in conn_response:
            return {'statusCode': 404, 'body': 'Connection not found'}

        sender_id = conn_response['Item']['userId']

        # Extract recipientId from chatId (assumes format: "user1#user2")
        users = sorted(chat_id.split('#'))
        recipient_id = users[1] if users[0] == sender_id else users[0]

        # Store message in DynamoDB
        timestamp = Decimal(str(time.time()))
        message_id = str(uuid.uuid4())

        # Moderate the message before storing/broadcasting
        moderation_result = {'flagged': False, 'severity': 'none'}
        if text:
            moderation_result = moderate_message(
                text=text,
                message_id=message_id,
                sender_id=sender_id,
                chat_id=chat_id,
                timestamp=float(timestamp)
            )
            logger.info(f"Moderation result: {json.dumps(moderation_result)}")

        message_item = {
            'chatId': chat_id,
            'timestamp': timestamp,
            'messageId': message_id,
            'senderId': sender_id,
            'recipientId': recipient_id,
            'status': 'sent',
            'flagged': moderation_result.get('flagged', False),
            'flagSeverity': moderation_result.get('severity', 'none')
        }

        if text:
            message_item['text'] = text
        if file_key:
            message_item['fileKey'] = file_key

        messages_table.put_item(Item=message_item)

        # Broadcast to recipient's active connections
        recipient_connections = connections_table.query(
            IndexName='userId-index',  # Note: You'll need to create this GSI
            KeyConditionExpression='userId = :uid',
            ExpressionAttributeValues={':uid': recipient_id}
        ) if False else {'Items': []}  # Simplified: query all connections

        # Get all connections for both recipient AND sender (for multi-tab sync)
        recipient_connections = connections_table.scan(
            FilterExpression='userId = :uid',
            ExpressionAttributeValues={':uid': recipient_id}
        )

        sender_connections = connections_table.scan(
            FilterExpression='userId = :uid',
            ExpressionAttributeValues={':uid': sender_id}
        )

        apigw_client = get_apigw_client(endpoint_url)

        broadcast_payload = json.dumps({
            'type': 'message',
            'messageId': message_id,
            'chatId': chat_id,
            'senderId': sender_id,
            'text': text,
            'fileKey': file_key,
            'timestamp': float(timestamp),
            'flagged': moderation_result.get('flagged', False),
            'flagSeverity': moderation_result.get('severity', 'none')
        })

        # Combine recipient and sender connections (include sender to update flagged status)
        all_connections = recipient_connections.get('Items', []) + sender_connections.get('Items', [])

        for conn in all_connections:
            try:
                apigw_client.post_to_connection(
                    ConnectionId=conn['connectionId'],
                    Data=broadcast_payload.encode('utf-8')
                )
            except apigw_client.exceptions.GoneException:
                # Connection is stale, remove it
                connections_table.delete_item(Key={'connectionId': conn['connectionId']})
                logger.info(f"Removed stale connection: {conn['connectionId']}")
            except Exception as e:
                logger.error(f"Failed to send to {conn['connectionId']}: {str(e)}")

        logger.info(json.dumps({
            'event': 'message_sent',
            'messageId': message_id,
            'chatId': chat_id,
            'senderId': sender_id
        }))

        return {'statusCode': 200, 'body': json.dumps({'messageId': message_id})}

    except Exception as e:
        logger.error(json.dumps({
            'event': 'send_message_error',
            'error': str(e)
        }))
        return {'statusCode': 500, 'body': 'Failed to send message'}
