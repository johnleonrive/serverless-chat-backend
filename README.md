# Serverless Chat Backend

Real-time serverless chat application backend built with AWS Lambda, API Gateway, DynamoDB, and Cognito for CPSC 465.

**Team:** Muhammad Shahwar Shamim, Daniel Wright, Ansh Tomar, John-Leon Rivera

**Live WebSocket API:** wss://rnf7vtl93i.execute-api.us-east-1.amazonaws.com/prod
**Live REST API:** https://gbctzghf5d.execute-api.us-east-1.amazonaws.com/prod

## Features

- **WebSocket Messaging** - Real-time bidirectional communication via API Gateway WebSocket
- **JWT Authentication** - Custom Lambda authorizer validates Cognito tokens
- **Friend System** - User search, friend requests, accept/reject via REST API
- **AI Content Moderation** - Hybrid rule-based + Claude API message analysis
- **Message History** - Persistent storage with retrieval endpoint
- **Infrastructure as Code** - Entire backend defined in SAM template

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   WebSocket     │     │    REST API     │     │    Cognito      │
│   API Gateway   │     │   API Gateway   │     │   User Pool     │
└────────┬────────┘     └────────┬────────┘     └─────────────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Lambda Functions                            │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐       │
│  │ on_connect│ │send_message│ │get_messages│ │friendships│       │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘       │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐       │
│  │disconnect │ │ send_file │ │search_users│ │ moderation│       │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘       │
│  ┌───────────┐                                                  │
│  │authorizer │                                                  │
│  └───────────┘                                                  │
└─────────────────────────────────────────────────────────────────┘
         │                       │                    │
         ▼                       ▼                    ▼
┌─────────────────┐     ┌─────────────────┐  ┌─────────────────┐
│    DynamoDB     │     │  Claude API     │  │ Secrets Manager │
│   (4 tables)    │     │  (moderation)   │  │  (API keys)     │
└─────────────────┘     └─────────────────┘  └─────────────────┘
```

## Tech Stack

| Category | Technology |
|----------|------------|
| Runtime | Python 3.13 |
| API | API Gateway (WebSocket + REST) |
| Compute | AWS Lambda |
| Database | DynamoDB |
| Auth | Cognito + Custom Authorizer |
| AI | Claude 3 Haiku (Anthropic API) |
| IaC | AWS SAM / CloudFormation |
| CI/CD | GitHub Actions |

## Lambda Functions

| Function | Trigger | Purpose |
|----------|---------|---------|
| 1-on-connect | WebSocket $connect | Store connection, extract user from JWT |
| 2-on-disconnect | WebSocket $disconnect | Remove connection from database |
| 3-send-message | WebSocket sendmessage | Process, moderate, store, broadcast messages |
| 4-send-file | WebSocket sendfile | Generate pre-signed S3 upload URLs |
| 5-get-messages | REST GET /messages | Fetch message history for a chat |
| 6-ws-authorizer | WebSocket $connect | Validate Cognito JWT token |
| 7-friendships | REST /friends/* | Friend CRUD operations |
| 8-search-users | REST GET /users/search | Search Cognito users by username |
| 9-moderation | Lambda invoke | AI content moderation (rule-based + Claude) |

## DynamoDB Tables

| Table | Partition Key | Sort Key | Purpose |
|-------|---------------|----------|---------|
| ChatConnections | connectionId | - | Active WebSocket connections |
| ChatMessages | chatId | timestamp | Message storage |
| ChatFriendships | PK | SK | Friend relationships and requests |
| ChatModeration | messageId | - | Flagged message records |

## Project Structure

```
serverless-chat-backend/
├── on_connect/
│   └── lambda_function.py      # WebSocket connect handler
├── on_disconnect/
│   └── lambda_function.py      # WebSocket disconnect handler
├── send_message/
│   └── lambda_function.py      # Message processing + broadcast
├── send_file/
│   └── lambda_function.py      # Pre-signed URL generation
├── get_messages/
│   └── lambda_function.py      # Message history retrieval
├── ws_authorizer/
│   └── lambda_function.py      # JWT validation
├── friendships/
│   └── lambda_function.py      # Friend system CRUD
├── search_users/
│   └── lambda_function.py      # Cognito user search
├── mcp_moderator/
│   ├── lambda_function.py      # AI moderation Lambda
│   └── server.py               # MCP server implementation
├── template-python.yaml        # SAM template
└── .github/workflows/
    ├── ci.yml                  # CI: lint, validate
    └── deploy.yml              # CD: SAM build, deploy
```

## Deployment

### Prerequisites

- AWS CLI configured
- AWS SAM CLI installed
- Python 3.13

### Deploy

```bash
# Build
sam build -t template-python.yaml

# Deploy (first time - guided)
sam deploy --guided --template-file template-python.yaml

# Deploy (subsequent)
sam deploy --template-file template-python.yaml
```

### Get API URLs

```bash
aws cloudformation describe-stacks \
  --stack-name serverless-chat-backend \
  --query 'Stacks[0].Outputs' \
  --output table
```

## Testing

### WebSocket Testing with wscat

```bash
# Install wscat
npm install -g wscat

# Connect with JWT token
wscat -c "wss://rnf7vtl93i.execute-api.us-east-1.amazonaws.com/prod?token=YOUR_JWT_TOKEN"

# Send a message
{"action":"sendmessage","chatId":"user1#user2","text":"Hello!"}
```

### REST API Testing

```bash
# Get messages
curl "https://gbctzghf5d.execute-api.us-east-1.amazonaws.com/prod/messages?chatId=user1%23user2"

# Search users
curl "https://gbctzghf5d.execute-api.us-east-1.amazonaws.com/prod/users/search?q=test&userId=myuser"
```

## Content Moderation

The moderation system uses a two-layer hybrid approach:

**Layer 1 - Rule-Based (Fast):**
- Regex patterns for profanity, slurs, threats, spam
- Instant response, no API call needed

**Layer 2 - AI Analysis (Claude 3 Haiku):**
- Invoked if rule-based check passes
- Analyzes context for subtle violations
- Returns severity: none, low, medium, high

## AWS Resources

| Resource | Value |
|----------|-------|
| WebSocket API | rnf7vtl93i |
| REST API | gbctzghf5d |
| Region | us-east-1 |
| Cognito User Pool | us-east-1_OncfX435Y |
| CloudFormation Stack | serverless-chat-backend |

## Related Repository

- **Frontend:** https://github.com/johnleonrive/serverless-chat-frontend

## License

MIT
