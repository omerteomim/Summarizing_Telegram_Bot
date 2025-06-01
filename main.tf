terraform {
  backend "s3" {
    bucket         = "omer-state-tf"
    key            = "telegram_bot_summary/terraform.tfstate"
    region         = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

#s3 bucket for storing videos
resource "aws_s3_bucket" "videos_bucket" {
  bucket = "videos-summarizing-telegram-bot"
}

#uploader_lambda
resource "aws_lambda_function" "uploader_lambda" {
  function_name    = "uploader_lambda"
  filename         = var.uploader_lambda_zip_path
  source_code_hash = filebase64sha256(var.uploader_lambda_zip_path)
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512
  
  environment {
    variables = {
      TELEGRAM_BOT_TOKEN = var.telegram_token
      S3_BUCKET_NAME = var.s3_bucket
    }
  }
  
  role = aws_iam_role.uploader_lambda_role.arn
}

# Function URL for uploader_lambda
resource "aws_lambda_function_url" "uploader_lambda_url" {
  function_name      = aws_lambda_function.uploader_lambda.function_name
  authorization_type = "NONE"
}


#summarizer_bot
resource "aws_lambda_function" "summarizer_lambda" {
  function_name    = "summarizer_lambda"
  filename         = var.summarizer_lambda_zip_path
  source_code_hash = filebase64sha256(var.summarizer_lambda_zip_path)
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  timeout          = 600
  memory_size      = 512
  
  environment {
    variables = {
      BOT_TOKEN = var.telegram_token
      CHAT_ID = var.telegram_chat_id
    }
  }
  
  role = aws_iam_role.summarizer_lambda_role.arn
}

#s3 trigger to the summarizer lambda
resource "aws_s3_bucket_notification" "video_upload_trigger" {
  bucket = aws_s3_bucket.videos_bucket.id
  lambda_function {
    lambda_function_arn = aws_lambda_function.uploader_lambda.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "telegram_videos/"
    filter_suffix       = ".mp4"
  }
  depends_on = [aws_lambda_permission.allow_s3_invoke]
}
resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.uploader_lambda.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.videos_bucket.arn
}

# Data source for current AWS account ID
data "aws_caller_identity" "current" {}

# Lambda assume role policy (shared by both functions)
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"
    
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    
    actions = ["sts:AssumeRole"]
  }
}

# IAM Role for Uploader Lambda
resource "aws_iam_role" "uploader_lambda_role" {
  name               = "uploader_lambda_role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "Uploader Lambda Role"
  }
}

# IAM Policy for Uploader Lambda
data "aws_iam_policy_document" "uploader_lambda_policy" {
  # CloudWatch Logs - Create Log Group
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup"
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
    ]
  }
  # CloudWatch Logs - Create Log Stream and Put Log Events
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/uploader_lambda:*"
    ]
  }
  # S3 - Put Objects
  statement {
    effect = "Allow"
    actions = [
      "s3:PutObject"
    ]
    resources = [
      "${aws_s3_bucket.videos_bucket.arn}/*"
    ]
  }
}

# Attach policy to Uploader Lambda role
resource "aws_iam_role_policy" "uploader_lambda_policy" {
  name   = "uploader_lambda_policy"
  role   = aws_iam_role.uploader_lambda_role.id
  policy = data.aws_iam_policy_document.uploader_lambda_policy.json
}



# IAM Role for Summarizer Lambda
resource "aws_iam_role" "summarizer_lambda_role" {
  name               = "summarizer_lambda_role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "Summarizer Lambda Role"
  }
}

# IAM Policy for Summarizer Lambda
data "aws_iam_policy_document" "summarizer_lambda_policy" {
  # CloudWatch Logs - Create Log Group
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup"
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
    ]
  }
  # CloudWatch Logs - Create Log Stream and Put Log Events
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/summarizer_lambda:*"
    ]
  }
  # S3 - Delete and Get Objects
  statement {
    sid    = "S3DeleteAndGetAccess"
    effect = "Allow"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject"
    ]
    resources = [
      "${aws_s3_bucket.videos_bucket.arn}/*"
    ]
  }
  # S3 - Put Objects in audio_temp folder
  statement {
    effect = "Allow"
    actions = [
      "s3:PutObject"
    ]
    resources = [
      "$${aws_s3_bucket.videos_bucket.arn}/audio_temp/*"
    ]
  }
  # S3 - List Bucket
  statement {
    sid    = "S3ListAccess"
    effect = "Allow"
    actions = [
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.videos_bucket.arn
    ]
  }
  # Amazon Transcribe Access
  statement {
    sid    = "TranscribeAccess"
    effect = "Allow"
    actions = [
      "transcribe:StartTranscriptionJob",
      "transcribe:GetTranscriptionJob",
      "transcribe:DeleteTranscriptionJob"
    ]
    resources = ["*"]
  }
  # Amazon Comprehend Access
  statement {
    effect = "Allow"
    actions = [
      "comprehend:DetectKeyPhrases",
      "comprehend:DetectEntities"
    ]
    resources = ["*"]
  }
}

# Attach policy to Summarizer Lambda role
resource "aws_iam_role_policy" "summarizer_lambda_policy" {
  name   = "summarizer_lambda_policy"
  role   = aws_iam_role.summarizer_lambda_role.id
  policy = data.aws_iam_policy_document.summarizer_lambda_policy.json
}

# Output the function URL
output "function_url" {
  value = aws_lambda_function_url.uploader_lambda_url.function_url
}