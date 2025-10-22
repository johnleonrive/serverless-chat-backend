import json
import os
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
connections_table = dynamodb.Table(os.environ['CONNECTIONS_TABLE'])

def lambda_handler(event, context):
    try:
        connection_id = event['requestContext']['connectionId']

        # Remove connection from table
        connections_table.delete_item(
            Key={'connectionId': connection_id}
        )

        logger.info(json.dumps({
            'event': 'connection_closed',
            'connectionId': connection_id
        }))

        return {'statusCode': 200, 'body': 'Disconnected'}

    except Exception as e:
        logger.error(json.dumps({
            'event': 'disconnect_error',
            'error': str(e),
            'connectionId': event['requestContext'].get('connectionId')
        }))
        return {'statusCode': 500, 'body': 'Disconnect failed'}
