variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-1"
}

variable "telegram_token" {
  description = "Telegram bot token"
  type        = string
  sensitive   = true
}

variable "telegram_chat_id" {
  description = "Your personal Telegram chat ID"
  type        = string
  sensitive   = true
}

variable "s3_bucket" {
  description = "S3 bucket fro storing videos"
  type        = string
  sensitive   = true
}

variable "uploader_lambda_zip_path" {
  description = "Path to the Lambda deployment package"
  type        = string
  default     = "uploader_lambda.zip"
}

variable "summarizer_lambda_zip_path" {
  description = "Path to the Lambda deployment package"
  type        = string
  default     = "summarizer_lambda.zip"
}