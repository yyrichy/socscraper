# UMD Testudo Course Scraper (AWS Lambda)

This project contains a Python script designed to monitor specific course sections listed on the University of Maryland's Testudo Schedule of Classes website and send notifications about changes via Discord.

It is deployed as an AWS Lambda function triggered by an Amazon EventBridge schedule.

## Features

* Monitors specified UMD Computer Science courses (CMSC320, CMSC335, and relevant CMSC4xx) for the configured term.
* Excludes specified courses (e.g., CMSC498A, CMSC499A).
* Fetches course section details including total seats, open seats, waitlist count, and instructor.
* Compares the current state against the previously saved state (stored in AWS S3).
* Sends notifications to a Discord channel via webhook for:
    * Newly added courses (especially CMSC4xx) or sections.
    * Sections where open seats change from 0 to positive.
    * Any other change in open seats.
    * Changes in total seats.
    * Changes in waitlist count.
    * Instructor changes.
    * Sections that are removed.
* Optionally pings a specific Discord user ID on change notifications.
* Indicates section fullness status (full or partially full).
* Sends a status message if no significant changes are detected.
* Handles transient fetch errors gracefully by reusing old data for affected courses and reporting the issue.

## System Design Overview

This scraper uses a serverless architecture on AWS:

1.  **Amazon EventBridge Scheduler:** Triggers the Lambda function every 10 minutes (configurable).
2.  **AWS Lambda:** Hosts and executes the Python script (`lambda_function.py`).
3.  **AWS S3:** Stores the state file (`course_state.json`) between Lambda invocations.
4.  **Testudo Website:** The script makes HTTP requests to fetch course lists and section details.
5.  **Discord Webhook:** Receives formatted messages about course changes or status updates.

## Setup

### Prerequisites

* Python 3.9+
* An AWS account
* A Discord server + Webhook URL
* Git

### Local Setup

1.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd <your-repository-name>
    ```
2.  **Create Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```
3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Create `.env` File:** Create a file named `.env` in the project root with your configuration secrets:
    ```dotenv
    DISCORD_WEBHOOK_URL="YOUR_DISCORD_WEBHOOK_URL_HERE"
    S3_BUCKET_NAME="YOUR_S3_BUCKET_NAME_HERE"
    DISCORD_USER_ID_TO_PING="YOUR_DISCORD_USER_ID_HERE" # Optional: User ID to ping on updates
    # STATE_FILE_KEY="course_state.json" # Optional: Defaults to course_state.json if omitted
    ```
    *(Note: The `.env` file is included in `.gitignore` and should **not** be committed.)*
5.  **Configure AWS Credentials locally** if you intend to test S3 interaction directly from your local machine.

## Deployment to AWS Lambda

1.  **Create S3 Bucket:** For storing `course_state.json`.
2.  **Create IAM Role:** For the Lambda function. Attach `AWSLambdaBasicExecutionRole` and a custom policy allowing `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on `arn:aws:s3:::YOUR_BUCKET_NAME/course_state.json` and `s3:ListBucket` on `arn:aws:s3:::YOUR_BUCKET_NAME`.
3.  **Package Dependencies:**
    * Run `pip install -r requirements.txt -t ./package`.
    * Create `deployment_package.zip` containing the **contents** of the `package/` folder plus `lambda_function.py` at the zip root.
4.  **Create Lambda Function:**
    * Runtime: Python 3.9+
    * Role: Select the role created above.
    * Upload the `.zip` file.
    * Handler: `lambda_function.lambda_handler`
    * Timeout: Increase to ~1 minute 30 seconds.
    * Environment Variables:
        * `DISCORD_WEBHOOK_URL`: Your webhook URL.
        * `S3_BUCKET_NAME`: Your bucket name.
        * `STATE_FILE_KEY`: `course_state.json` (or custom).
        * `DISCORD_USER_ID_TO_PING`: (Optional) Your Discord user ID.
5.  **Create EventBridge Schedule:**
    * Recurring schedule, Rate-based, every 10 minutes.
    * Target: Lambda Invoke, select your function.
    * Allow creation of a new role for the schedule to invoke Lambda.
    * Enable the schedule.

## Configuration

Environment variables control the deployed Lambda function's behavior:

* `DISCORD_WEBHOOK_URL` (Required): Discord webhook destination.
* `S3_BUCKET_NAME` (Required): S3 bucket for state persistence.
* `STATE_FILE_KEY` (Optional): Name of the state file in S3 (default: `course_state.json`).
* `DISCORD_USER_ID_TO_PING` (Optional): Discord User ID mentioned when changes are detected.

Other configurations (courses to monitor/exclude, term ID, starred courses) are set as constants within `lambda_function.py`.

## Running Locally

Ensure `.env` is configured (or AWS credentials for S3). Run:
```bash
python lambda_function.py
```