import boto3
import os
import subprocess
import requests
import uuid
import time
import json
import re
from collections import Counter

s3 = boto3.client("s3")
transcribe = boto3.client("transcribe")
comprehend = boto3.client("comprehend")

BUCKET = "videos-summerizing-bot"
PREFIX = "telegram_videos/"
TMP_DIR = "/tmp"

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']


def get_single_video_key():
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)
    videos = [obj["Key"] for obj in response.get("Contents", []) if obj["Key"].endswith(".mp4")]
    return videos[0] if videos else None


def download_video(key):
    filename = os.path.join(TMP_DIR, key.split("/")[-1])
    s3.download_file(Bucket=BUCKET, Key=key, Filename=filename)
    return filename


def extract_audio(video_path):
    audio_path = video_path.replace(".mp4", ".mp3")
    subprocess.run(["ffmpeg","-y", "-i", video_path, "-q:a", "0", "-map", "a", audio_path], check=True)
    return audio_path


def upload_audio_to_s3(audio_path):
    """Upload audio file to S3 for transcription"""
    audio_key = f"audio_temp/{uuid.uuid4()}.mp3"
    s3.upload_file(audio_path, BUCKET, audio_key)
    return f"s3://{BUCKET}/{audio_key}", audio_key


def transcribe_with_aws(audio_s3_uri):
    """Transcribe audio using AWS Transcribe"""
    job_name = f"transcribe-job-{uuid.uuid4()}"
    
    # Start transcription job
    transcribe.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={'MediaFileUri': audio_s3_uri},
        MediaFormat='mp3',
        LanguageCode='en-US'  # Change this to your preferred language
    )
    
    # Poll for completion
    max_wait = 300  # 5 minutes max wait
    wait_time = 0
    
    while wait_time < max_wait:
        response = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        status = response['TranscriptionJob']['TranscriptionJobStatus']
        
        if status == 'COMPLETED':
            # Get transcript from the result URL
            transcript_uri = response['TranscriptionJob']['Transcript']['TranscriptFileUri']
            transcript_response = requests.get(transcript_uri)
            transcript_data = transcript_response.json()
            
            # Clean up the transcription job
            transcribe.delete_transcription_job(TranscriptionJobName=job_name)
            
            return transcript_data['results']['transcripts'][0]['transcript']
        
        elif status == 'FAILED':
            transcribe.delete_transcription_job(TranscriptionJobName=job_name)
            raise Exception(f"Transcription failed: {response['TranscriptionJob'].get('FailureReason', 'Unknown error')}")
        
        # Wait before checking again
        time.sleep(10)
        wait_time += 10
    
    # Cleanup on timeout
    transcribe.delete_transcription_job(TranscriptionJobName=job_name)
    raise Exception("Transcription timed out")


def analyze_text_with_comprehend(text):
    """Analyze text using AWS Comprehend to extract key information"""
    
    # Split text into chunks if it's too long (Comprehend has limits)
    max_bytes = 5000  # Comprehend limit is 5KB for some operations
    chunks = []
    
    if len(text.encode('utf-8')) > max_bytes:
        # Split text into sentences and group into chunks
        sentences = re.split(r'[.!?]+', text)
        current_chunk = ""
        
        for sentence in sentences:
            if len((current_chunk + sentence).encode('utf-8')) < max_bytes:
                current_chunk += sentence + ". "
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence + ". "
        
        if current_chunk:
            chunks.append(current_chunk.strip())
    else:
        chunks = [text]
    
    # Extract key phrases and entities from all chunks
    all_key_phrases = []
    all_entities = []
    
    for chunk in chunks:
        if not chunk.strip():
            continue
            
        try:
            # Extract key phrases
            key_phrases_response = comprehend.detect_key_phrases(
                Text=chunk,
                LanguageCode='en'
            )
            all_key_phrases.extend([phrase['Text'] for phrase in key_phrases_response['KeyPhrases']])
            
            # Extract entities
            entities_response = comprehend.detect_entities(
                Text=chunk,
                LanguageCode='en'
            )
            all_entities.extend([entity['Text'] for entity in entities_response['Entities']])
            
        except Exception as e:
            print(f"Error analyzing chunk: {str(e)}")
            continue
    
    return all_key_phrases, all_entities


def create_extractive_summary(text, key_phrases, entities, num_sentences=5):
    """Create an extractive summary using key phrases and entities"""
    
    # Split text into sentences
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    # Score sentences based on key phrases and entities
    sentence_scores = {}
    
    for i, sentence in enumerate(sentences):
        score = 0
        sentence_lower = sentence.lower()
        
        # Score based on key phrases
        for phrase in key_phrases:
            if phrase.lower() in sentence_lower:
                score += 2
        
        # Score based on entities
        for entity in entities:
            if entity.lower() in sentence_lower:
                score += 1
        
        # Prefer sentences that are not too short or too long
        word_count = len(sentence.split())
        if 8 <= word_count <= 30:
            score += 1
        
        sentence_scores[i] = score
    
    # Get top scoring sentences
    top_sentences = sorted(sentence_scores.items(), key=lambda x: x[1], reverse=True)
    selected_indices = sorted([idx for idx, score in top_sentences[:num_sentences]])
    
    # Create summary with selected sentences
    summary_sentences = [sentences[i] for i in selected_indices if i < len(sentences)]
    
    return summary_sentences


def summarize_with_comprehend(text):
    """Create a summary using AWS Comprehend analysis"""
    # Analyze text with Comprehend
    key_phrases, entities = analyze_text_with_comprehend(text)
    
    # Get most common key phrases and entities
    key_phrase_counts = Counter(key_phrases)
    entity_counts = Counter(entities)
    
    top_key_phrases = [phrase for phrase, count in key_phrase_counts.most_common(10)]
    top_entities = [entity for entity, count in entity_counts.most_common(10)]
    
    # Create extractive summary
    summary_sentences = create_extractive_summary(text, top_key_phrases, top_entities)
    
    # Format as bullet points
    summary = "📋 Meeting Summary:\n\n"
    for i, sentence in enumerate(summary_sentences, 1):
        summary += f"• {sentence}\n"
    
    # Add key topics if available
    if top_key_phrases:
        summary += f"\n🔑 Key Topics: {', '.join(top_key_phrases[:5])}"
    
    if top_entities:
        summary += f"\n👥 Mentioned: {', '.join(top_entities[:5])}"
    
    return summary


def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, json=payload)


def lambda_handler(event, context):
    audio_s3_key = None
    try:
        print("Step 1: Getting latest video...")
        # 1. Get the latest video
        key = get_single_video_key()
        if not key:
            return {"status": "no videos"}
        print(f"Found video: {key}")

        print("Step 2: Downloading and extracting audio...")
        # 2. Download & extract audio
        video_path = download_video(key)
        audio_path = extract_audio(video_path)
        print(f"Audio extracted to: {audio_path}")

        print("Step 3: Uploading audio to S3...")
        # 3. Upload audio to S3 for transcription
        audio_s3_uri, audio_s3_key = upload_audio_to_s3(audio_path)
        print(f"Audio uploaded to: {audio_s3_uri}")

        print("Step 4: Starting transcription...")
        # 4. Transcribe using AWS Transcribe
        transcript = transcribe_with_aws(audio_s3_uri)
        print(f"Transcription completed. Length: {len(transcript)} characters")

        print("Step 5: Summarizing with AWS Comprehend...")
        # 5. Summarize using AWS Comprehend
        summary = summarize_with_comprehend(transcript)
        print(f"Summary created. Length: {len(summary)} characters")

        print("Step 6: Sending to Telegram...")
        # 6. Send to Telegram
        send_to_telegram(summary)
        print("Message sent to Telegram")

        print("Step 7: Cleaning up...")
        # 7. Delete original video from S3
        s3.delete_object(Bucket=BUCKET, Key=key)

        # 8. Clean up temporary audio file from S3
        if audio_s3_key:
            s3.delete_object(Bucket=BUCKET, Key=audio_s3_key)

        print("Process completed successfully")
        return {"status": "success", "summary": summary}

    except Exception as e:
        print(f"Error occurred: {str(e)}")
        # Clean up temporary audio file in case of error
        if audio_s3_key:
            try:
                s3.delete_object(Bucket=BUCKET, Key=audio_s3_key)
            except:
                pass
        
        return {"status": "error", "message": str(e)}