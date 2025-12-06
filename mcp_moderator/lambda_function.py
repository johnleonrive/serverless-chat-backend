"""
Lambda function for message moderation.

This is a simplified version of the MCP server that can be invoked directly
by the send_message Lambda to analyze messages in real-time.

Uses hybrid approach:
1. Quick rule-based check (blocklist, patterns)
2. AI analysis via Claude API for edge cases
"""

import os
import re
import json
import logging
from datetime import datetime
from decimal import Decimal

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
secrets_client = boto3.client('secretsmanager')

MODERATION_TABLE = os.environ.get('MODERATION_TABLE', 'ChatModeration')
MESSAGES_TABLE = os.environ.get('MESSAGES_TABLE', 'ChatMessages')

# Claude client (lazy initialization)
_claude_client = None
_anthropic_module = None


def get_claude_client():
    """Get Claude client with API key from Secrets Manager"""
    global _claude_client, _anthropic_module

    if _claude_client is None:
        try:
            # Import anthropic here to handle cases where it might not be installed
            import anthropic
            _anthropic_module = anthropic

            secret = secrets_client.get_secret_value(SecretId='chat-app/claude-api-key')
            api_key = secret['SecretString']
            _claude_client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic module not available, AI analysis disabled")
            return None
        except Exception as e:
            logger.error(f"Failed to get Claude API key: {e}")
            return None

    return _claude_client


# Rule-based moderation patterns
BLOCKLIST_PATTERNS = [
    # Profanity (common examples)
    r'\b(fuck|shit|damn|bitch|bastard|crap)\b',
    # Slurs and hate speech
    r'\b(n[i1]gg[ae]r?|f[a4]gg?[o0]t|ret[a4]rd|tr[a4]nny)\b',
    # Threats
    r'\b(kill\s+you|gonna\s+die|murder|threat)\b',
    # Spam patterns
    r'(buy\s+now|click\s+here|free\s+money|earn\s+\$\d+|act\s+now)',
    r'(bit\.ly|tinyurl|goo\.gl)/\S+',  # URL shorteners often used in spam
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BLOCKLIST_PATTERNS]


def rule_based_check(text: str) -> dict:
    """
    Quick rule-based check for obvious violations.
    """
    violations = []

    for i, pattern in enumerate(COMPILED_PATTERNS):
        match = pattern.search(text)
        if match:
            violations.append({
                'type': 'pattern_match',
                'matched': match.group(),
                'pattern_index': i
            })

    if violations:
        # High severity for slurs/threats (indices 1-2), medium for others
        has_severe = any(v['pattern_index'] in [1, 2] for v in violations)
        severity = 'high' if has_severe else 'medium'

        return {
            'flagged': True,
            'severity': severity,
            'reason': f"Matched {len(violations)} blocked pattern(s)",
            'violations': violations,
            'method': 'rule_based',
            'recommendation': 'block' if severity == 'high' else 'flag'
        }

    return {
        'flagged': False,
        'severity': 'none',
        'method': 'rule_based',
        'recommendation': 'allow'
    }


def ai_analysis(text: str) -> dict:
    """
    Use Claude to analyze message for subtle violations.
    """
    client = get_claude_client()

    if client is None:
        return {
            'flagged': False,
            'severity': 'none',
            'method': 'ai_analysis',
            'recommendation': 'allow',
            'note': 'AI analysis unavailable'
        }

    try:
        prompt = f"""Analyze this chat message for content moderation. Check for:
1. Harassment, bullying, or personal attacks
2. Hate speech or discrimination
3. Threats of violence
4. Sexual or explicit content
5. Spam, scams, or phishing
6. Sharing of personal/private information

Message: "{text}"

Respond ONLY with a JSON object (no other text):
{{
    "flagged": true or false,
    "reason": "brief explanation if flagged, null if not",
    "categories": ["list of violated categories if any"],
    "severity": "none" | "low" | "medium" | "high",
    "confidence": 0.0 to 1.0
}}

Be lenient - only flag content that clearly violates guidelines.
Normal conversation, mild frustration, and casual language are OK."""

        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )

        result_text = response.content[0].text.strip()

        # Parse JSON response
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            result['method'] = 'ai_analysis'

            # Determine recommendation based on result
            if result.get('flagged'):
                severity = result.get('severity', 'low')
                if severity == 'high':
                    result['recommendation'] = 'block'
                elif severity in ['medium', 'low']:
                    result['recommendation'] = 'flag'
                else:
                    result['recommendation'] = 'allow'
            else:
                result['recommendation'] = 'allow'

            return result

        return {
            'flagged': False,
            'severity': 'none',
            'method': 'ai_analysis',
            'recommendation': 'allow',
            'parse_error': True
        }

    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return {
            'flagged': False,
            'severity': 'none',
            'method': 'ai_analysis',
            'recommendation': 'allow',
            'error': str(e)
        }


def analyze_message(text: str, message_id: str = None, sender_id: str = None, chat_id: str = None) -> dict:
    """
    Main analysis function - hybrid approach.
    """
    # Step 1: Quick rule-based check
    rule_result = rule_based_check(text)

    if rule_result['flagged']:
        # Store moderation record if we have message details
        if message_id:
            store_moderation_record(
                message_id=message_id,
                sender_id=sender_id,
                chat_id=chat_id,
                text=text,
                result=rule_result
            )
        return rule_result

    # Step 2: AI analysis for edge cases (only for longer messages)
    if len(text) > 10:  # Skip very short messages
        ai_result = ai_analysis(text)

        if ai_result.get('flagged'):
            if message_id:
                store_moderation_record(
                    message_id=message_id,
                    sender_id=sender_id,
                    chat_id=chat_id,
                    text=text,
                    result=ai_result
                )
            return ai_result

        return ai_result

    return {
        'flagged': False,
        'severity': 'none',
        'method': 'skipped',
        'recommendation': 'allow',
        'note': 'Message too short for AI analysis'
    }


def store_moderation_record(message_id: str, sender_id: str, chat_id: str, text: str, result: dict):
    """Store moderation record in DynamoDB"""
    try:
        table = dynamodb.Table(MODERATION_TABLE)
        table.put_item(Item={
            'messageId': message_id,
            'senderId': sender_id,
            'chatId': chat_id,
            'text': text[:500],  # Truncate for storage
            'status': 'pending',
            'severity': result.get('severity', 'unknown'),
            'reason': result.get('reason', ''),
            'categories': result.get('categories', []),
            'method': result.get('method', 'unknown'),
            'recommendation': result.get('recommendation', 'flag'),
            'flaggedAt': datetime.utcnow().isoformat(),
            'reviewedAt': None,
            'action': None
        })
        logger.info(f"Stored moderation record for message {message_id}")
    except Exception as e:
        logger.error(f"Failed to store moderation record: {e}")


def update_message_flag(message_id: str, chat_id: str, timestamp, is_flagged: bool, severity: str):
    """Update the original message with flagged status"""
    try:
        table = dynamodb.Table(MESSAGES_TABLE)
        table.update_item(
            Key={
                'chatId': chat_id,
                'timestamp': timestamp
            },
            UpdateExpression='SET flagged = :f, flagSeverity = :s',
            ExpressionAttributeValues={
                ':f': is_flagged,
                ':s': severity
            }
        )
        logger.info(f"Updated message {message_id} with flagged={is_flagged}")
    except Exception as e:
        logger.error(f"Failed to update message flag: {e}")


def lambda_handler(event, context):
    """
    Lambda handler for message moderation.

    Can be invoked directly or via API Gateway.
    """
    logger.info(f"Event: {json.dumps(event)}")

    # Handle different invocation patterns
    if 'body' in event:
        # API Gateway invocation
        body = json.loads(event.get('body', '{}'))
    else:
        # Direct Lambda invocation
        body = event

    text = body.get('text', '')
    message_id = body.get('messageId')
    sender_id = body.get('senderId')
    chat_id = body.get('chatId')
    timestamp = body.get('timestamp')

    if not text:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'text is required'})
        }

    # Analyze the message
    result = analyze_message(
        text=text,
        message_id=message_id,
        sender_id=sender_id,
        chat_id=chat_id
    )

    # If flagged and we have message details, update the message
    if result.get('flagged') and message_id and chat_id and timestamp:
        update_message_flag(
            message_id=message_id,
            chat_id=chat_id,
            timestamp=Decimal(str(timestamp)),
            is_flagged=True,
            severity=result.get('severity', 'unknown')
        )

    response = {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(result, default=str)
    }

    return response
