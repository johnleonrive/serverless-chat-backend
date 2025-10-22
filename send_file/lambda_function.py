import json
import os
import boto3
import logging
import uuid
from urllib.parse import quote

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')
UPLOAD_BUCKET = os.environ['UPLOAD_BUCKET']
ALLOWED_CONTENT_TYPES = [
    'image/jpeg', 'image/png', 'image/gif',
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain'
]

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        chat_id = body.get('chatId')
        file_name = body.get('fileName')
        content_type = body.get('contentType')

        # Validation
        if not all([chat_id, file_name, content_type]):
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'chatId, fileName, and contentType required'})
            }

        if content_type not in ALLOWED_CONTENT_TYPES:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Content type not allowed'})
            }

        # Generate unique file key
        file_extension = file_name.split('.')[-1] if '.' in file_name else 'bin'
        file_key = f"{chat_id}/{uuid.uuid4()}.{file_extension}"

        # Generate pre-signed URL (15 min expiry)
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': UPLOAD_BUCKET,
                'Key': file_key,
                'ContentType': content_type
            },
            ExpiresIn=900,
            HttpMethod='PUT'
        )

        logger.info(json.dumps({
            'event': 'presigned_url_generated',
            'fileKey': file_key,
            'chatId': chat_id
        }))

        return {
            'statusCode': 200,
            'body': json.dumps({
                'uploadUrl': presigned_url,
                'fileKey': file_key,
                'instructions': 'PUT file to uploadUrl, then call sendMessage with fileKey'
            })
        }

    except Exception as e:
        logger.error(json.dumps({
            'event': 'send_file_error',
            'error': str(e)
        }))
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Failed to generate upload URL'})
        }
