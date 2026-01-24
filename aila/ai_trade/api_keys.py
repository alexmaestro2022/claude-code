"""
API keys configuration for AI Trade external services.
Keys are loaded from environment variables for security.
"""

import os

API_KEYS = {
    # Whale Alert — whale transaction tracking
    # https://whale-alert.io/ (free tier: 10 req/min)
    "whale_alert": os.getenv("WHALE_ALERT_API_KEY", ""),

    # Glassnode — on-chain analytics (paid)
    # https://glassnode.com/
    "glassnode": os.getenv("GLASSNODE_API_KEY", ""),

    # CryptoPanic — crypto news aggregator
    # https://cryptopanic.com/developers/api/ (free tier available)
    "cryptopanic": os.getenv("CRYPTOPANIC_API_KEY", ""),

    # Twitter/X API v2 (paid for real-time)
    # https://developer.twitter.com/
    "twitter": os.getenv("TWITTER_API_KEY", ""),

    # NewsAPI — general news
    # https://newsapi.org/ (free tier: 100 req/day)
    "newsapi": os.getenv("NEWSAPI_KEY", ""),
}
