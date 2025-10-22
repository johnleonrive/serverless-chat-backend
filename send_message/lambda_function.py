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
connections_table = dynamodb.Table(os.environ['CONNECTIONS_TABLE'])
messages_table = dynamodb.Table(os.environ['MESSAGES_TABLE'])

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

        message_item = {
            'chatId': chat_id,
            'timestamp': timestamp,
            'messageId': message_id,
            'senderId': sender_id,
            'recipientId': recipient_id,
            'status': 'sent'
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

        # Alternative: Scan for recipient connections (less efficient)
        all_connections = connections_table.scan(
            FilterExpression='userId = :uid',
            ExpressionAttributeValues={':uid': recipient_id}
        )

        apigw_client = get_apigw_client(endpoint_url)

        broadcast_payload = json.dumps({
            'type': 'message',
            'messageId': message_id,
            'chatId': chat_id,
            'senderId': sender_id,
            'text': text,
            'fileKey': file_key,
            'timestamp': float(timestamp)
        })

        for conn in all_connections.get('Items', []):
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
