# Create ZIP file for Lambda function
data "archive_file" "summarizer_zip" {
  type        = "zip"
  source_file  = "../lambdas/summarizer_lambda.py"
  output_path = var.lambda_zip_path
}

data "archive_file" "uploader_zip" {
  type        = "zip"
  source_file  = "../lambdas/uploader_lambda.py"
  output_path = var.lambda_zip_path
}