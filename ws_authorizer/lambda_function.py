import json
import os
import logging
import urllib.request
import time
from jose import jwt, jwk
from jose.utils import base64url_decode

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Cache for JWKS
jwks_cache = {}
jwks_cache_time = 0
CACHE_DURATION = 3600  # 1 hour

def get_jwks(region, user_pool_id):
    """Fetch and cache JWKS from Cognito"""
    global jwks_cache, jwks_cache_time

    current_time = time.time()
    cache_key = f"{region}:{user_pool_id}"

    if cache_key in jwks_cache and (current_time - jwks_cache_time) < CACHE_DURATION:
        return jwks_cache[cache_key]

    jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"

    with urllib.request.urlopen(jwks_url) as response:
        jwks = json.loads(response.read().decode())

    jwks_cache[cache_key] = jwks
    jwks_cache_time = current_time

    return jwks


def verify_token(token, region, user_pool_id, client_id):
    """Verify Cognito JWT token"""
    try:
        # Get JWKS
        jwks = get_jwks(region, user_pool_id)

        # Get the kid from the token header
        headers = jwt.get_unverified_header(token)
        kid = headers.get('kid')

        if not kid:
            logger.error("No kid in token header")
            return None

        # Find the matching key
        key = None
        for k in jwks.get('keys', []):
            if k.get('kid') == kid:
                key = k
                break

        if not key:
            logger.error(f"Key {kid} not found in JWKS")
            return None

        # Construct the public key
        public_key = jwk.construct(key)

        # Verify the token
        issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

        claims = jwt.decode(
            token,
            public_key,
            algorithms=['RS256'],
            audience=client_id,
            issuer=issuer
        )

        # Verify token_use
        token_use = claims.get('token_use')
        if token_use != 'id':
            logger.error(f"Invalid token_use: {token_use}")
            return None

        return claims

    except jwt.ExpiredSignatureError:
        logger.error("Token has expired")
        return None
    except jwt.JWTClaimsError as e:
        logger.error(f"Invalid claims: {e}")
        return None
    except Exception as e:
        logger.error(f"Token verification failed: {e}")
        return None


def generate_policy(principal_id, effect, resource, context=None):
    """Generate IAM policy for API Gateway"""
    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': effect,
                'Resource': resource
            }]
        }
    }

    if context:
        policy['context'] = context

    return policy


def lambda_handler(event, context):
    """WebSocket API Gateway Lambda Authorizer"""
    logger.info(json.dumps({
        'event': 'authorizer_invoked',
        'methodArn': event.get('methodArn'),
        'type': event.get('type')
    }))

    try:
        # Get configuration from environment
        region = os.environ.get('COGNITO_REGION', 'us-east-1')
        user_pool_id = os.environ['COGNITO_USER_POOL_ID']
        client_id = os.environ['COGNITO_CLIENT_ID']

        # Extract token from query string
        query_params = event.get('queryStringParameters') or {}
        token = query_params.get('token')

        if not token:
            logger.error("No token provided in query string")
            raise Exception('Unauthorized')

        # Verify the token
        claims = verify_token(token, region, user_pool_id, client_id)

        if not claims:
            logger.error("Token verification failed")
            raise Exception('Unauthorized')

        # Extract user information
        user_id = claims.get('preferred_username') or claims.get('email') or claims.get('sub')
        email = claims.get('email')
        sub = claims.get('sub')

        logger.info(json.dumps({
            'event': 'authorization_success',
            'userId': user_id,
            'sub': sub
        }))

        # Generate allow policy with user context
        method_arn = event.get('methodArn', '*')

        return generate_policy(
            principal_id=sub,
            effect='Allow',
            resource=method_arn,
            context={
                'userId': user_id,
                'email': email or '',
                'sub': sub
            }
        )

    except Exception as e:
        logger.error(json.dumps({
            'event': 'authorization_failed',
            'error': str(e)
        }))
        raise Exception('Unauthorized')
