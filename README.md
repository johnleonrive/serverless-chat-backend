# Serverless Chat Backend

CPSC 465 Project - Real-time 1-on-1 chat application backend built with AWS Lambda, API Gateway WebSocket, DynamoDB, and S3.

## Architecture Overview

This backend implements a serverless 1-on-1 chat system using AWS managed services.

## Tech Stack

- **Runtime**: Python 3.13
- **API**: API Gateway WebSocket
- **Compute**: AWS Lambda (serverless functions)
- **Database**: DynamoDB (connections + messages)
- **Storage**: S3 (file uploads)
- **IaC**: AWS SAM (Serverless Application Model)

## Project Structure

```
serverless-chat-backend/
├── on_connect/
│   ├── lambda_function.py      # Handles WebSocket connections
│   └── requirements.txt
├── on_disconnect/
│   ├── lambda_function.py      # Handles WebSocket disconnections
│   └── requirements.txt
├── send_message/
│   ├── lambda_function.py      # Sends messages between users
│   └── requirements.txt
├── send_file/
│   ├── lambda_function.py      # Generates S3 pre-signed URLs for file uploads
│   └── requirements.txt
├── template-python.yaml         # SAM template for Python deployment (USE THIS)
├── template.yaml                # Original Node.js sample (reference only)
└── README.md                    # This file
```

## Lambda Functions

### 1. on_connect (1_on_connect)

**Trigger**: When a client connects to WebSocket API

**What it does**:
- Extracts `userId` from query string parameter
- Stores connection in DynamoDB `ChatConnections` table
- Sets TTL (24 hours) for automatic cleanup

**Request**: Connect to WebSocket with userId query parameter
```
wss://{api-id}.execute-api.us-east-1.amazonaws.com/prod?userId=user123
```

### 2. on_disconnect (2-on-disconnect)

**Trigger**: When a client disconnects from WebSocket API

**What it does**:
- Removes connection from DynamoDB `ChatConnections` table
- Logs disconnection event

### 3. send_message (3-send-message)

**Trigger**: When client sends a message with `action: "sendmessage"`

**What it does**:
- Validates payload (chatId, text/fileKey)
- Stores message in DynamoDB `ChatMessages` table
- Broadcasts message to recipient's active WebSocket connections
- Handles stale connections (removes if gone)

**Request**:
```json
{
  "action": "sendmessage",
  "chatId": "user123#user456",
  "text": "Hello!",
  "fileKey": "optional-s3-file-key"
}
```

### 4. send_file (4-send-file)

**Trigger**: When client requests file upload URL with `action: "sendfile"`

**What it does**:
- Validates file metadata (chatId, fileName, contentType)
- Generates S3 pre-signed PUT URL (15 min expiry)
- Returns URL for client to upload file directly to S3

**Request**:
```json
{
  "action": "sendfile",
  "chatId": "user123#user456",
  "fileName": "image.jpg",
  "contentType": "image/jpeg"
}
```

## DynamoDB Tables

### ChatConnections

Maps WebSocket connectionIds to userIds. TTL enabled (24 hours).

### ChatMessages

Stores all 1-on-1 chat messages. Keyed by chatId (format: "user1#user2") and timestamp.

## Deployment

### Prerequisites

1. AWS CLI configured with credentials
2. AWS SAM CLI installed
3. Python 3.13

### Deploy to AWS

```bash
# Build the application
sam build -t template-python.yaml

# Deploy (guided first time)
sam deploy --guided --template-file template-python.yaml

# Get WebSocket URL
aws cloudformation describe-stacks \
  --stack-name serverless-chat-backend \
  --query 'Stacks[0].Outputs[?OutputKey==`WebSocketURI`].OutputValue' \
  --output text
```

## How Lambda Deployment Works

**Important**: Lambda functions are **code that lives in your repo** but **runs in AWS**.

### Workflow:

```
1. Edit Lambda code locally (this repo)
   ↓
2. Commit to Git (version control)
   ↓
3. sam build (packages Python code)
   ↓
4. sam deploy (uploads to AWS)
   ↓
5. AWS Lambda runs your code when triggered
```

**Not like a traditional server**:
- No server running 24/7
- Functions execute on-demand
- Auto-scales
- Pay only when used

## Testing

```bash
# Install wscat
npm install -g wscat

# Connect
wscat -c "wss://{api-id}.execute-api.us-east-1.amazonaws.com/prod?userId=testuser123"

# Send a message
{"action":"sendmessage","chatId":"testuser123#testuser456","text":"Hello!"}
```

## Current Deployment

**AWS Account**: 676206934447 (cpsc465-group)
**Region**: us-east-1
**WebSocket URL**: `wss://0o1twopkd9.execute-api.us-east-1.amazonaws.com/prod`

**Deployed Functions**:
- 1_on_connect (Python 3.13)
- 2-on-disconnect (Python 3.13)
- 3-send-message (Python 3.13)
- 4-send-file (Python 3.13)

## License

MIT
