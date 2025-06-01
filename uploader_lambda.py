import json
import boto3
import urllib.request
import urllib.parse
import os
from datetime import datetime

def lambda_handler(event, context):
    try:
        # Parse the incoming webhook from Telegram
        body = json.loads(event['body'])
        
        # Check if message contains a video
        if 'message' not in body or 'video' not in body['message']:
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No video found'})
            }
        
        message = body['message']
        video = message['video']
        chat_id = message['chat']['id']
        
        # Get bot token from environment variables
        bot_token = os.environ['TELEGRAM_BOT_TOKEN']
        s3_bucket = os.environ['S3_BUCKET_NAME']
        
        # Download video from Telegram
        file_id = video['file_id']
        video_data = download_telegram_video(bot_token, file_id)
        
        if not video_data:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to download video'})
            }
        
        # Upload to S3
        s3_key = upload_to_s3(video_data, s3_bucket, file_id)
        
        if s3_key:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Video uploaded successfully',
                    's3_key': s3_key
                })
            }
        else:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to upload to S3'})
            }
            
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

def download_telegram_video(bot_token, file_id):
    """Download video file from Telegram servers"""
    try:
        # Get file info
        file_info_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
        
        with urllib.request.urlopen(file_info_url) as response:
            file_info = json.loads(response.read().decode())
        
        if not file_info['ok']:
            print(f"Failed to get file info: {file_info}")
            return None
        
        file_path = file_info['result']['file_path']
        
        # Download the actual file
        download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        
        with urllib.request.urlopen(download_url) as response:
            if response.status == 200:
                return response.read()
            else:
                print(f"Failed to download video: {response.status}")
                return None
            
    except Exception as e:
        print(f"Error downloading video: {str(e)}")
        return None

def upload_to_s3(video_data, bucket_name, file_id):
    """Upload video data to S3"""
    try:
        s3_client = boto3.client('s3')
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        s3_key = f"telegram_videos/{timestamp}_{file_id}.mp4"
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=video_data,
            ContentType='video/mp4'
        )
        
        print(f"Video uploaded to S3: {s3_key}")
        return s3_key
        
    except Exception as e:
        print(f"Error uploading to S3: {str(e)}")
        return None



# Required IAM permissions for Lambda role:
# - s3:PutObject on your S3 bucket
# - logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents for CloudWatch

# Environment variables needed:
# - TELEGRAM_BOT_TOKEN: Your bot token from BotFather
# - S3_BUCKET_NAME: Name of your S3 bucket