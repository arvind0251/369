# config.py

# ==== BOT SETTINGS ====
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"   # BotFather se lo
API_ID = 12345678                       # my.telegram.org se lo
API_HASH = "your_api_hash_here"

# ==== ADMINS ====
ADMINS = [123456789, 987654321]  # Telegram user IDs jo admin panel access kar sakte hain

# ==== PAYMENT SETTINGS ====
# Telegram Payments (Stripe, LiqPay, YooMoney etc.)
PAYMENT_PROVIDER_TOKEN = "YOUR_PAYMENT_PROVIDER_TOKEN"

# Default prices (Paise me; 1 INR = 100 paise)
PRICE_LIST = {
    "IN": 5000,     # ₹50 India ke liye
    "US": 30000,    # $300 US ke liye
    "PK": 4000,     # PKR price
    "DEFAULT": 10000  # Baaki sab countries
}

# ==== FILE STORAGE ====
SESSION_FILES_DIR = "sessions"  # Yaha tum session files save karoge

# ==== OTHER SETTINGS ====
CURRENCY_SYMBOLS = {
    "IN": "₹",
    "US": "$",
    "PK": "₨",
    "DEFAULT": "$"
}

# ==== FEATURES ====
# True/False toggle
ENABLE_COUNTRY_DETECTION = True
ENABLE_AUTO_PRICE = True
