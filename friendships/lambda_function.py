import json
import os
import boto3
import logging
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
friendships_table = dynamodb.Table(os.environ['FRIENDSHIPS_TABLE'])
connections_table = dynamodb.Table(os.environ.get('CONNECTIONS_TABLE', 'ChatConnections'))
websocket_endpoint = os.environ.get('WEBSOCKET_ENDPOINT', '')


def get_apigw_client():
    """Get API Gateway Management API client for WebSocket"""
    if not websocket_endpoint:
        return None
    return boto3.client('apigatewaymanagementapi', endpoint_url=websocket_endpoint)


def send_ws_notification(user_id, payload):
    """Send WebSocket notification to all connections of a user"""
    if not websocket_endpoint:
        logger.warning('WebSocket endpoint not configured, skipping notification')
        return

    try:
        # Find all connections for this user
        connections = connections_table.scan(
            FilterExpression='userId = :uid',
            ExpressionAttributeValues={':uid': user_id}
        )

        apigw_client = get_apigw_client()
        if not apigw_client:
            return

        data = json.dumps(payload).encode('utf-8')

        for conn in connections.get('Items', []):
            try:
                apigw_client.post_to_connection(
                    ConnectionId=conn['connectionId'],
                    Data=data
                )
                logger.info(f"Sent notification to {conn['connectionId']}")
            except apigw_client.exceptions.GoneException:
                # Connection is stale, remove it
                connections_table.delete_item(Key={'connectionId': conn['connectionId']})
                logger.info(f"Removed stale connection: {conn['connectionId']}")
            except Exception as e:
                logger.error(f"Failed to send to {conn['connectionId']}: {str(e)}")

    except Exception as e:
        logger.error(f"Error sending WebSocket notification: {e}")

# DynamoDB Schema:
# PK: USER#<userId>
# SK: FRIEND#<friendId> (for accepted friends) or REQUEST#<requesterId> (for incoming requests)
# GSI1PK: USER#<friendId> (reverse lookup)
# GSI1SK: Same as SK
# status: 'pending' | 'accepted'
# createdAt: timestamp


def json_response(status_code, body):
    """Helper to create API Gateway response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
        },
        'body': json.dumps(body, default=str)
    }


def get_user_from_event(event):
    """Extract userId from request (via query param or auth header)"""
    # Try query params first
    params = event.get('queryStringParameters') or {}
    user_id = params.get('userId')

    if not user_id:
        # Try body for POST requests
        body = event.get('body')
        if body:
            try:
                data = json.loads(body)
                user_id = data.get('userId')
            except json.JSONDecodeError:
                pass

    return user_id


def list_friends(user_id):
    """Get all accepted friends for a user"""
    try:
        response = friendships_table.query(
            KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
            ExpressionAttributeValues={
                ':pk': f'USER#{user_id}',
                ':sk': 'FRIEND#'
            }
        )

        friends = []
        for item in response.get('Items', []):
            friend_id = item['SK'].replace('FRIEND#', '')
            friends.append({
                'id': friend_id,
                'name': item.get('friendName', friend_id),
                'since': item.get('createdAt')
            })

        return json_response(200, {'friends': friends})

    except Exception as e:
        logger.error(f'Error listing friends: {e}')
        return json_response(500, {'error': 'Failed to list friends'})


def list_requests(user_id):
    """Get pending friend requests (both incoming and outgoing)"""
    try:
        # Get incoming requests (where current user is the recipient)
        incoming_response = friendships_table.query(
            KeyConditionExpression='PK = :pk AND begins_with(SK, :sk)',
            ExpressionAttributeValues={
                ':pk': f'USER#{user_id}',
                ':sk': 'REQUEST#'
            }
        )

        incoming = []
        for item in incoming_response.get('Items', []):
            requester_id = item['SK'].replace('REQUEST#', '')
            incoming.append({
                'from': requester_id,
                'name': item.get('requesterName', requester_id),
                'createdAt': item.get('createdAt')
            })

        # Get outgoing requests (where current user is the requester)
        outgoing_response = friendships_table.query(
            IndexName='GSI1',
            KeyConditionExpression='GSI1PK = :pk AND begins_with(GSI1SK, :sk)',
            ExpressionAttributeValues={
                ':pk': f'USER#{user_id}',
                ':sk': 'REQUEST#'
            }
        )

        outgoing = []
        for item in outgoing_response.get('Items', []):
            recipient_id = item['PK'].replace('USER#', '')
            outgoing.append({
                'to': recipient_id,
                'name': item.get('recipientName', recipient_id),
                'createdAt': item.get('createdAt')
            })

        return json_response(200, {
            'incoming': incoming,
            'outgoing': outgoing
        })

    except Exception as e:
        logger.error(f'Error listing requests: {e}')
        return json_response(500, {'error': 'Failed to list requests'})


def send_request(user_id, target_id, user_name=None, target_name=None):
    """Send a friend request to another user"""
    if user_id == target_id:
        return json_response(400, {'error': 'Cannot send friend request to yourself'})

    try:
        # Check if already friends or request exists
        existing = friendships_table.get_item(
            Key={
                'PK': f'USER#{target_id}',
                'SK': f'FRIEND#{user_id}'
            }
        )

        if existing.get('Item'):
            return json_response(400, {'error': 'Already friends'})

        # Check if request already sent
        existing_request = friendships_table.get_item(
            Key={
                'PK': f'USER#{target_id}',
                'SK': f'REQUEST#{user_id}'
            }
        )

        if existing_request.get('Item'):
            return json_response(400, {'error': 'Friend request already sent'})

        # Check if they already sent us a request (auto-accept)
        reverse_request = friendships_table.get_item(
            Key={
                'PK': f'USER#{user_id}',
                'SK': f'REQUEST#{target_id}'
            }
        )

        if reverse_request.get('Item'):
            # Auto-accept: they already sent us a request
            return accept_request(user_id, target_id, user_name, target_name)

        # Create friend request
        now = datetime.utcnow().isoformat()

        friendships_table.put_item(
            Item={
                'PK': f'USER#{target_id}',
                'SK': f'REQUEST#{user_id}',
                'GSI1PK': f'USER#{user_id}',
                'GSI1SK': f'REQUEST#{user_id}',
                'status': 'pending',
                'requesterName': user_name or user_id,
                'recipientName': target_name or target_id,
                'createdAt': now
            }
        )

        # Send WebSocket notification to recipient
        send_ws_notification(target_id, {
            'type': 'friend_request',
            'from': user_id,
            'name': user_name or user_id,
            'createdAt': now
        })

        logger.info(f'Friend request sent from {user_id} to {target_id}')
        return json_response(200, {'message': 'Friend request sent'})

    except Exception as e:
        logger.error(f'Error sending request: {e}')
        return json_response(500, {'error': 'Failed to send friend request'})


def accept_request(user_id, requester_id, user_name=None, requester_name=None):
    """Accept a friend request"""
    try:
        # Verify request exists
        existing = friendships_table.get_item(
            Key={
                'PK': f'USER#{user_id}',
                'SK': f'REQUEST#{requester_id}'
            }
        )

        if not existing.get('Item'):
            return json_response(404, {'error': 'Friend request not found'})

        now = datetime.utcnow().isoformat()
        requester_name = existing['Item'].get('requesterName', requester_id)
        user_name = existing['Item'].get('recipientName', user_id)

        # Delete the request and create friendship (both directions)
        # Using batch write for atomicity
        with friendships_table.batch_writer() as batch:
            # Delete the request
            batch.delete_item(
                Key={
                    'PK': f'USER#{user_id}',
                    'SK': f'REQUEST#{requester_id}'
                }
            )

            # Create friendship: user -> requester
            batch.put_item(
                Item={
                    'PK': f'USER#{user_id}',
                    'SK': f'FRIEND#{requester_id}',
                    'GSI1PK': f'USER#{requester_id}',
                    'GSI1SK': f'FRIEND#{user_id}',
                    'status': 'accepted',
                    'friendName': requester_name,
                    'createdAt': now
                }
            )

            # Create friendship: requester -> user
            batch.put_item(
                Item={
                    'PK': f'USER#{requester_id}',
                    'SK': f'FRIEND#{user_id}',
                    'GSI1PK': f'USER#{user_id}',
                    'GSI1SK': f'FRIEND#{requester_id}',
                    'status': 'accepted',
                    'friendName': user_name,
                    'createdAt': now
                }
            )

        # Send WebSocket notification to the original requester
        send_ws_notification(requester_id, {
            'type': 'friend_accepted',
            'by': user_id,
            'name': user_name,
            'createdAt': now
        })

        # Also notify the accepter's other tabs
        send_ws_notification(user_id, {
            'type': 'friend_accepted',
            'by': requester_id,
            'name': requester_name,
            'createdAt': now
        })

        logger.info(f'Friend request accepted: {user_id} <-> {requester_id}')
        return json_response(200, {'message': 'Friend request accepted'})

    except Exception as e:
        logger.error(f'Error accepting request: {e}')
        return json_response(500, {'error': 'Failed to accept friend request'})


def reject_request(user_id, requester_id):
    """Reject a friend request"""
    try:
        # Delete the request
        friendships_table.delete_item(
            Key={
                'PK': f'USER#{user_id}',
                'SK': f'REQUEST#{requester_id}'
            }
        )

        logger.info(f'Friend request rejected: {requester_id} -> {user_id}')
        return json_response(200, {'message': 'Friend request rejected'})

    except Exception as e:
        logger.error(f'Error rejecting request: {e}')
        return json_response(500, {'error': 'Failed to reject friend request'})


def remove_friend(user_id, friend_id):
    """Remove a friend (unfriend)"""
    try:
        # Delete both directions of friendship
        with friendships_table.batch_writer() as batch:
            batch.delete_item(
                Key={
                    'PK': f'USER#{user_id}',
                    'SK': f'FRIEND#{friend_id}'
                }
            )
            batch.delete_item(
                Key={
                    'PK': f'USER#{friend_id}',
                    'SK': f'FRIEND#{user_id}'
                }
            )

        # Notify the removed friend
        send_ws_notification(friend_id, {
            'type': 'friend_removed',
            'by': user_id
        })

        # Notify user's other tabs
        send_ws_notification(user_id, {
            'type': 'friend_removed',
            'by': friend_id
        })

        logger.info(f'Friendship removed: {user_id} <-> {friend_id}')
        return json_response(200, {'message': 'Friend removed'})

    except Exception as e:
        logger.error(f'Error removing friend: {e}')
        return json_response(500, {'error': 'Failed to remove friend'})


def lambda_handler(event, context):
    """Main handler - routes to appropriate function based on path"""
    logger.info(f'Event: {json.dumps(event)}')

    http_method = event.get('httpMethod', '')
    path = event.get('path', '')

    # Get user ID
    user_id = get_user_from_event(event)

    if not user_id:
        return json_response(400, {'error': 'userId is required'})

    # Route based on path and method
    if path == '/friends' and http_method == 'GET':
        return list_friends(user_id)

    elif path == '/friends/requests' and http_method == 'GET':
        return list_requests(user_id)

    elif path == '/friends/request' and http_method == 'POST':
        body = json.loads(event.get('body', '{}'))
        target_id = body.get('targetId')
        user_name = body.get('userName')
        target_name = body.get('targetName')

        if not target_id:
            return json_response(400, {'error': 'targetId is required'})

        return send_request(user_id, target_id, user_name, target_name)

    elif path == '/friends/accept' and http_method == 'POST':
        body = json.loads(event.get('body', '{}'))
        requester_id = body.get('requesterId')

        if not requester_id:
            return json_response(400, {'error': 'requesterId is required'})

        return accept_request(user_id, requester_id)

    elif path == '/friends/reject' and http_method == 'POST':
        body = json.loads(event.get('body', '{}'))
        requester_id = body.get('requesterId')

        if not requester_id:
            return json_response(400, {'error': 'requesterId is required'})

        return reject_request(user_id, requester_id)

    elif path == '/friends/remove' and http_method == 'POST':
        body = json.loads(event.get('body', '{}'))
        friend_id = body.get('friendId')

        if not friend_id:
            return json_response(400, {'error': 'friendId is required'})

        return remove_friend(user_id, friend_id)

    else:
        return json_response(404, {'error': 'Not found'})
