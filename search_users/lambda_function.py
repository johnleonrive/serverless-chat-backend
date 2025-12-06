import json
import os
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cognito = boto3.client('cognito-idp')
user_pool_id = os.environ['COGNITO_USER_POOL_ID']


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


def search_users(query, current_user_id, limit=20):
    """Search for users by username or email"""
    try:
        if not query or len(query) < 2:
            return json_response(400, {'error': 'Query must be at least 2 characters'})

        # Search by preferred_username (display name)
        users = []

        # Try searching by preferred_username
        try:
            response = cognito.list_users(
                UserPoolId=user_pool_id,
                Filter=f'preferred_username ^= "{query}"',
                Limit=limit
            )
            users.extend(response.get('Users', []))
        except Exception as e:
            logger.warning(f'Error searching by preferred_username: {e}')

        # Also search by email if not enough results
        if len(users) < limit:
            try:
                response = cognito.list_users(
                    UserPoolId=user_pool_id,
                    Filter=f'email ^= "{query}"',
                    Limit=limit - len(users)
                )
                # Add users not already in list
                existing_subs = {get_user_attribute(u, 'sub') for u in users}
                for user in response.get('Users', []):
                    if get_user_attribute(user, 'sub') not in existing_subs:
                        users.append(user)
            except Exception as e:
                logger.warning(f'Error searching by email: {e}')

        # Format results
        results = []
        for user in users:
            user_sub = get_user_attribute(user, 'sub')
            username = get_user_attribute(user, 'preferred_username') or user.get('Username', '')
            email = get_user_attribute(user, 'email', '')

            # Don't include the current user in results
            if username == current_user_id or user_sub == current_user_id:
                continue

            results.append({
                'id': username or user_sub,
                'username': username,
                'email': email,
                'sub': user_sub
            })

        logger.info(f'Search for "{query}" returned {len(results)} results')
        return json_response(200, {'users': results})

    except Exception as e:
        logger.error(f'Error searching users: {e}')
        return json_response(500, {'error': 'Failed to search users'})


def get_user_attribute(user, attr_name, default=None):
    """Extract attribute from Cognito user object"""
    for attr in user.get('Attributes', []):
        if attr['Name'] == attr_name:
            return attr['Value']
    return default


def lambda_handler(event, context):
    """Main handler for user search"""
    logger.info(f'Event: {json.dumps(event)}')

    params = event.get('queryStringParameters') or {}
    query = params.get('q', '').strip()
    current_user_id = params.get('userId', '')
    limit = min(int(params.get('limit', 20)), 50)

    if not query:
        return json_response(400, {'error': 'Search query (q) is required'})

    return search_users(query, current_user_id, limit)
