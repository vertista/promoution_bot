🤖 Promotion Bot: A Telegram Assistant for Promotional Campaigns
This repository contains the source code for a multifunctional Telegram bot designed to automate the submission and review process for promotional campaigns and user-generated content (UGC) contests.

The bot serves as an admin assistant, simplifying routine tasks and ensuring smooth interaction with participants.

✨ Key Features
Submission Handling: The bot accepts video links from YouTube Shorts and TikTok, automatically filtering out invalid messages.

Automatic Stat Collection: Immediately after a link is submitted, the bot connects to the YouTube API or scrapes the TikTok page to fetch up-to-date statistics (views, likes, comments) and attaches them to the submission for the administrator.

Interactive Admin Workflow: All submissions are forwarded to the administrator's private messages with complete user info, the link, stats, and inline buttons "✅ Approve" and "❌ Decline" for one-click decision-making.

Secure Payment Data Collection: The bot guides users through a conversation to collect and validate payment details (e.g., card number, crypto wallet), which are stored securely in a dedicated database.

Enhanced User Experience (UX): While a link is being processed, the user sees an animated loading message, providing clear feedback that their request is in progress.

Database Management: A protected /clear_db command is available exclusively for the administrator to completely wipe the user database of test or outdated entries.

24/7 Stability: The code includes a minimalist Flask web server to handle health checks from hosting platforms, preventing the bot from "sleeping" on free-tier plans.

🛠️ Tech Stack
Language: Python 3

Main Library: python-telegram-bot

Database: PostgreSQL

Web Server (for health checks): Flask

API & Scraping:

google-api-python-client for the YouTube Data API v3

requests and BeautifulSoup4 for fetching data from TikTok

Hosting: Render

Secrets Management: python-dotenv for local development and environment variables on the server.

🚀 Installation and Setup
Prerequisites
Python 3.8+

Git

1. Clone the Repository
git clone [https://github.com/vertista/promoution_bot.git](https://github.com/vertista/promoution_bot.git)
cd promoution_bot

2. Install Dependencies
It is highly recommended to use a virtual environment.

python -m venv .venv
source .venv/bin/activate  # For Windows: .venv\Scripts\activate
pip install -r requirements.txt

3. Configure Environment Variables
Create a .env file in the root directory of the project. This file is ignored by Git and will not be committed to the public repository.

# .env file for local development

TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
ADMIN_CHAT_ID="YOUR_TELEGRAM_ID_AS_ADMIN"
DATABASE_URL="YOUR_POSTGRESQL_DATABASE_URL"
YOUTUBE_API_KEY="YOUR_YOUTUBE_DATA_API_V3_KEY"

TOKEN: Obtain from @BotFather in Telegram.

ADMIN_CHAT_ID: Can be found using @userinfobot.

DATABASE_URL: Copied from your database settings on Render.

YOUTUBE_API_KEY: Created in the Google Cloud Console.

4. Run the Bot Locally
python bot.py
