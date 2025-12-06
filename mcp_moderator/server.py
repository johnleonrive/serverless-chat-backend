"""
MCP Chat Moderation Server

Provides tools for analyzing and moderating chat messages using a hybrid approach:
1. Quick rule-based checks (blocklist, patterns)
2. AI analysis via Claude API for edge cases

Tools:
- analyze_message: Check message for violations
- flag_message: Mark message for review
- get_moderation_queue: List flagged messages
- apply_action: Take moderation action
- get_user_violations: Check user's history
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import Optional
from decimal import Decimal

import boto3
import anthropic
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
secrets_client = boto3.client('secretsmanager', region_name='us-east-1')

# Table name (will be set via environment or default)
MODERATION_TABLE = os.environ.get('MODERATION_TABLE', 'ChatModeration')
moderation_table = dynamodb.Table(MODERATION_TABLE)

# Claude client (lazy initialization)
_claude_client = None


def get_claude_client():
    """Get Claude client with API key from Secrets Manager"""
    global _claude_client
    if _claude_client is None:
        try:
            secret = secrets_client.get_secret_value(SecretId='chat-app/claude-api-key')
            api_key = secret['SecretString']
            _claude_client = anthropic.Anthropic(api_key=api_key)
        except Exception as e:
            logger.error(f"Failed to get Claude API key: {e}")
            raise
    return _claude_client


# Rule-based moderation patterns
BLOCKLIST = [
    # Profanity (examples - expand as needed)
    r'\b(fuck|shit|damn|ass|bitch)\b',
    # Slurs and hate speech patterns
    r'\b(n[i1]gg[ae]r?|f[a4]gg?[o0]t|ret[a4]rd)\b',
    # Spam patterns
    r'(buy now|click here|free money|earn \$\d+)',
    r'https?://\S+\.(ru|cn|xyz)/\S*',  # Suspicious domains
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BLOCKLIST]


def rule_based_check(text: str) -> dict:
    """
    Quick rule-based check for obvious violations.
    Returns: {flagged: bool, matches: list, severity: str}
    """
    matches = []

    for i, pattern in enumerate(COMPILED_PATTERNS):
        if pattern.search(text):
            matches.append({
                'pattern_index': i,
                'pattern': BLOCKLIST[i]
            })

    if matches:
        # Determine severity based on pattern type
        severity = 'high' if any(i['pattern_index'] < 2 for i in matches) else 'medium'
        return {
            'flagged': True,
            'matches': matches,
            'severity': severity,
            'method': 'rule_based'
        }

    return {'flagged': False, 'matches': [], 'severity': 'none', 'method': 'rule_based'}


async def ai_analysis(text: str, context: Optional[dict] = None) -> dict:
    """
    Use Claude to analyze message for subtle violations.
    Returns: {flagged: bool, reason: str, severity: str, confidence: float}
    """
    try:
        client = get_claude_client()

        prompt = f"""Analyze this chat message for content moderation. Check for:
1. Harassment or bullying
2. Hate speech or discrimination
3. Threats or violence
4. Sexual content
5. Spam or scams
6. Personal information sharing

Message: "{text}"

Respond in JSON format:
{{
    "flagged": true/false,
    "reason": "brief explanation if flagged, null otherwise",
    "categories": ["list", "of", "violated", "categories"],
    "severity": "none" | "low" | "medium" | "high",
    "confidence": 0.0-1.0
}}

Only flag content that clearly violates guidelines. Be lenient with mild language."""

        response = client.messages.create(
            model="claude-3-haiku-20240307",  # Fast and cheap for moderation
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )

        # Parse JSON response
        result_text = response.content[0].text
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            result['method'] = 'ai_analysis'
            return result

        return {
            'flagged': False,
            'reason': None,
            'severity': 'none',
            'confidence': 0.5,
            'method': 'ai_analysis',
            'error': 'Could not parse AI response'
        }

    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return {
            'flagged': False,
            'reason': None,
            'severity': 'none',
            'confidence': 0.0,
            'method': 'ai_analysis',
            'error': str(e)
        }


# Create MCP Server
app = Server("chat-moderator")


@app.list_tools()
async def list_tools():
    """List available moderation tools"""
    return [
        Tool(
            name="analyze_message",
            description="Analyze a chat message for inappropriate content using hybrid rule-based and AI analysis",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The message text to analyze"
                    },
                    "sender_id": {
                        "type": "string",
                        "description": "ID of the message sender (optional)"
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "ID of the chat (optional)"
                    }
                },
                "required": ["text"]
            }
        ),
        Tool(
            name="flag_message",
            description="Flag a message for moderator review",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "ID of the message to flag"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for flagging"
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Severity level"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Violation categories"
                    }
                },
                "required": ["message_id", "reason", "severity"]
            }
        ),
        Tool(
            name="get_moderation_queue",
            description="Get messages pending moderation review",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "reviewed", "all"],
                        "default": "pending"
                    },
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "Maximum number of items to return"
                    }
                }
            }
        ),
        Tool(
            name="apply_action",
            description="Apply a moderation action to a flagged message",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "ID of the flagged message"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["approve", "delete", "warn_user", "ban_user"],
                        "description": "Action to take"
                    },
                    "moderator_notes": {
                        "type": "string",
                        "description": "Notes from moderator"
                    }
                },
                "required": ["message_id", "action"]
            }
        ),
        Tool(
            name="get_user_violations",
            description="Get moderation history for a specific user",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "ID of the user"
                    },
                    "limit": {
                        "type": "integer",
                        "default": 20
                    }
                },
                "required": ["user_id"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    """Handle tool calls"""

    if name == "analyze_message":
        text = arguments.get("text", "")
        sender_id = arguments.get("sender_id")
        chat_id = arguments.get("chat_id")

        # Step 1: Quick rule-based check
        rule_result = rule_based_check(text)

        if rule_result['flagged']:
            # Obvious violation found
            return [TextContent(
                type="text",
                text=json.dumps({
                    "flagged": True,
                    "severity": rule_result['severity'],
                    "method": "rule_based",
                    "matches": len(rule_result['matches']),
                    "recommendation": "block" if rule_result['severity'] == 'high' else "flag"
                })
            )]

        # Step 2: AI analysis for edge cases
        ai_result = await ai_analysis(text)

        return [TextContent(
            type="text",
            text=json.dumps({
                "flagged": ai_result.get('flagged', False),
                "severity": ai_result.get('severity', 'none'),
                "reason": ai_result.get('reason'),
                "categories": ai_result.get('categories', []),
                "confidence": ai_result.get('confidence', 0.0),
                "method": ai_result.get('method'),
                "recommendation": "flag" if ai_result.get('flagged') else "allow"
            })
        )]

    elif name == "flag_message":
        message_id = arguments["message_id"]
        reason = arguments["reason"]
        severity = arguments["severity"]
        categories = arguments.get("categories", [])

        try:
            moderation_table.put_item(Item={
                'messageId': message_id,
                'status': 'pending',
                'reason': reason,
                'severity': severity,
                'categories': categories,
                'flaggedAt': datetime.utcnow().isoformat(),
                'reviewedAt': None,
                'action': None,
                'moderatorNotes': None
            })

            return [TextContent(
                type="text",
                text=json.dumps({"success": True, "message_id": message_id})
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"success": False, "error": str(e)})
            )]

    elif name == "get_moderation_queue":
        status = arguments.get("status", "pending")
        limit = arguments.get("limit", 50)

        try:
            if status == "all":
                response = moderation_table.scan(Limit=limit)
            else:
                response = moderation_table.scan(
                    FilterExpression='#s = :status',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':status': status},
                    Limit=limit
                )

            items = response.get('Items', [])
            # Convert Decimal to float for JSON serialization
            for item in items:
                for key, value in item.items():
                    if isinstance(value, Decimal):
                        item[key] = float(value)

            return [TextContent(
                type="text",
                text=json.dumps({"items": items, "count": len(items)})
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"error": str(e)})
            )]

    elif name == "apply_action":
        message_id = arguments["message_id"]
        action = arguments["action"]
        notes = arguments.get("moderator_notes", "")

        try:
            moderation_table.update_item(
                Key={'messageId': message_id},
                UpdateExpression='SET #s = :status, #a = :action, reviewedAt = :reviewed, moderatorNotes = :notes',
                ExpressionAttributeNames={
                    '#s': 'status',
                    '#a': 'action'
                },
                ExpressionAttributeValues={
                    ':status': 'reviewed',
                    ':action': action,
                    ':reviewed': datetime.utcnow().isoformat(),
                    ':notes': notes
                }
            )

            return [TextContent(
                type="text",
                text=json.dumps({"success": True, "message_id": message_id, "action": action})
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"success": False, "error": str(e)})
            )]

    elif name == "get_user_violations":
        user_id = arguments["user_id"]
        limit = arguments.get("limit", 20)

        try:
            # Query by GSI on senderId (would need to add this index)
            response = moderation_table.scan(
                FilterExpression='senderId = :uid',
                ExpressionAttributeValues={':uid': user_id},
                Limit=limit
            )

            items = response.get('Items', [])
            return [TextContent(
                type="text",
                text=json.dumps({
                    "user_id": user_id,
                    "violations": items,
                    "count": len(items)
                })
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"error": str(e)})
            )]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    """Run the MCP server"""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
