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
rekognition = boto3.client("rekognition")

BUCKET = os.environ['S3_BUCKET_NAME']
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


def rekognition_with_aws(key):
    """Extract visual labels from video using AWS Rekognition"""
    response = rekognition.start_label_detection(
        Video={'S3Object': {'Bucket': BUCKET, 'Name': key}},
        MinConfidence=75
    )
    job_id = response['JobId']
    max_wait = 300
    waited = 0
    
    while waited < max_wait:
        result = rekognition.get_label_detection(JobId=job_id)
        status = result['JobStatus']
        
        if status == 'SUCCEEDED':
            labels = result['Labels']
            
            # Process labels into readable text
            visual_elements = []
            label_counts = {}
            
            for label_data in labels:
                label_name = label_data['Label']['Name']
                confidence = label_data['Label']['Confidence']
                
                # Only include high-confidence labels
                if confidence >= 80:
                    if label_name in label_counts:
                        label_counts[label_name] = max(label_counts[label_name], confidence)
                    else:
                        label_counts[label_name] = confidence
            
            # Convert to descriptive text
            if label_counts:
                sorted_labels = sorted(label_counts.items(), key=lambda x: x[1], reverse=True)
                top_labels = [f"{label}" for label, conf in sorted_labels[:10]]
                visual_text = f"Visual content includes: {', '.join(top_labels)}."
            else:
                visual_text = "No significant visual content detected."
                
            return visual_text
            
        elif status == 'FAILED':
            raise Exception(f"Rekognition label detection failed: {result.get('StatusMessage', 'Unknown error')}")
        
        time.sleep(10)
        waited += 10

    raise Exception("Rekognition job timed out.")
    

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

def create_integrated_video_summary_with_comprehend(transcript, visual_labels, max_sentences=3):
    """Create an integrated summary using AWS Comprehend analysis combined with visual content"""
    
    transcript = transcript.strip()
    if not transcript:
        return "No transcript content available."
    
    # Use Comprehend to analyze the transcript
    print("  🧠 Analyzing transcript with AWS Comprehend...")
    key_phrases, entities = analyze_text_with_comprehend(transcript)
    
    # Get most important key phrases and entities
    key_phrase_counts = Counter(key_phrases)
    entity_counts = Counter(entities)
    
    top_key_phrases = [phrase for phrase, count in key_phrase_counts.most_common(8)]
    top_entities = [entity for entity, count in entity_counts.most_common(8)]
    
    print(f"  🔑 Found {len(top_key_phrases)} key phrases and {len(top_entities)} entities")
    
    # Split transcript into sentences
    sentences = re.split(r'[.!?]+', transcript)
    sentences = [s.strip() for s in sentences if s.strip() and len(s.split()) > 3]
    
    if not sentences:
        return "No meaningful sentences found in transcript."
    
    # Analyze and categorize visual elements
    visual_description = ""
    tech_items = []
    people_items = []
    text_items = []
    other_items = []
    
    if visual_labels:
        for label in visual_labels:
            label_lower = label.lower()
            if label_lower in ['electronics', 'computer', 'phone', 'mobile phone', 'monitor', 'screen', 'hardware', 'laptop', 'device']:
                tech_items.append(label)
            elif label_lower in ['person', 'people', 'human', 'face', 'meeting', 'audience', 'crowd']:
                people_items.append(label)
            elif 'text' in label_lower or 'writing' in label_lower:
                text_items.append(label)
            else:
                other_items.append(label)
    
    # Create rich visual description
    visual_parts = []
    if tech_items:
        visual_parts.append(f"featuring {', '.join(tech_items[:3]).lower()}")
    if people_items:
        visual_parts.append(f"with {', '.join(people_items[:2]).lower()} present")
    if text_items:
        visual_parts.append("displaying textual information")
    if other_items:
        visual_parts.append(f"showing {', '.join(other_items[:2]).lower()}")
    
    if visual_parts:
        visual_description = f"The video scene {' and '.join(visual_parts[:3])}"
    
    # Score sentences using Comprehend analysis + visual context
    sentence_scores = []
    for i, sentence in enumerate(sentences):
        score = 0
        words = sentence.split()
        sentence_lower = sentence.lower()
        
        # Base score for good length
        if 8 <= len(words) <= 25:
            score += 3
        elif 5 <= len(words) <= 30:
            score += 1
        
        # Score based on Comprehend key phrases (weighted higher)
        for phrase in top_key_phrases:
            if phrase.lower() in sentence_lower:
                score += 4  # High weight for key phrases
        
        # Score based on Comprehend entities
        for entity in top_entities:
            if entity.lower() in sentence_lower:
                score += 2
        
        # Penalize negative/dismissive content
        negative_phrases = ['idiotic', 'stupid', 'worst', 'terrible', 'hate', 'awful']
        for phrase in negative_phrases:
            if phrase in sentence_lower:
                score -= 5
        
        sentence_scores.append((i, sentence, score))
    
    # Select best sentences based on Comprehend analysis
    sentence_scores.sort(key=lambda x: x[2], reverse=True)
    selected_sentences = sentence_scores[:max_sentences]
    selected_sentences.sort(key=lambda x: x[0])  # Back to original order
    
    # Create fully integrated narrative with visual elements woven in
    if selected_sentences:
        main_content = []
        
        # Start with visual context
        if visual_description:
            main_content.append(visual_description)
        
        # Add the main content with context
        if len(selected_sentences) > 0:
            first_sentence = selected_sentences[0][1]
            main_content.append(f"presents content focusing on: {first_sentence.lower()}")
            
            # Add remaining sentences with smooth transitions
            for i, (_, sentence, _) in enumerate(selected_sentences[1:], 1):
                if i == 1:
                    main_content.append(f"The discussion continues with: {sentence.lower()}")
                else:
                    main_content.append(sentence)
        
        # Combine everything into flowing narrative
        integrated_summary = " ".join(main_content)
        
        # Add Comprehend insights integrated into the narrative
        if top_key_phrases:
            integrated_summary += f" Throughout this presentation, key topics emerge including {', '.join(top_key_phrases[:4]).lower()}."
        
        if top_entities and visual_labels:
            integrated_summary += f" The combination of visual elements ({', '.join(visual_labels[:3]).lower()}) and spoken content about {', '.join(top_entities[:3]).lower()} creates a comprehensive educational experience."
        
        return integrated_summary
    
    return "Unable to generate meaningful summary from the content."

def summarize_video_content(transcript, visual_summary):
    """Main function to create integrated video summary using AWS Comprehend"""
    
    # Extract visual labels
    visual_labels = re.findall(r"Visual content includes: (.+)\.", visual_summary)
    labels = visual_labels[0].split(", ") if visual_labels else []
    
    # Clean and deduplicate labels
    cleaned_labels = []
    seen = set()
    for label in labels:
        clean_label = label.strip()
        if clean_label and clean_label.lower() not in seen:
            cleaned_labels.append(clean_label)
            seen.add(clean_label.lower())
    
    # Create fully integrated summary with visual elements woven throughout
    summary = create_integrated_video_summary_with_comprehend(transcript, cleaned_labels)
    
    # Format output - no separate visual section since it's integrated
    result = f"📹 Integrated Video Analysis:\n{summary}"
    
    return result


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

        print("Step 3: starting rekognition... ")
        visual_summary=rekognition_with_aws(key)

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
        summary = summarize_video_content(transcript,visual_summary)
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
                send_to_telegram("No video found")
            except:
                pass
        
        return {"status": "error", "message": str(e)}