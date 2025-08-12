"""
Safe Marketplace Telegram Bot (Aiogram + SQLite)

IMPORTANT: This bot is intentionally designed for selling NON-SENSITIVE digital goods
(images, course PDFs, license keys you own, software, apks you have right to sell etc.).
DO NOT upload or sell Telegram `.session` files or other items that provide full account access.

Features:
- Admin-only /addfile (upload file + price + country tag)
- /stock (admin) to list available items
- /shop (users) browse by country tag
- /buy flow using Telegram Payments (send_invoice example)
- Pre-checkout and successful payment handlers
- Auto-zip with OTP password delivery after successful payment
- SQLite for metadata & logs
- Admin price management (/setprice, /setpriceprod, /getprices, /delprice)

Single-file runnable example. Edit ENV variables below before running.

Requirements:
- Python 3.9+
- aiogram
- python-dotenv (optional, but recommended)

Install:
    pip install aiogram python-dotenv

Run:
    export BOT_TOKEN="<bot token>"
    export PAYMENT_PROVIDER_TOKEN="<provider token or empty for testing>"
    export ADMIN_IDS="123456789,987654321"
    python safe_marketplace_bot.py

Replace env vars on Windows accordingly.

This code uses aiogram v2-style API (common and simple). Adjust if you use aiogram v3.
"""

import os
import sqlite3
import logging
import secrets
import zipfile
import json
from pathlib import Path
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import LabeledPrice

# load .env if present
load_dotenv()

# Config - set these in env or replace inline (not recommended)
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN', '')  # e.g. from @BotFather
ADMIN_IDS = set(int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip())
DATA_DIR = Path('./data')
FILES_DIR = DATA_DIR / 'files'
DB_PATH = DATA_DIR / 'market.db'
PRICE_FILE = DATA_DIR / 'prices.json'

# Safety reminder
SAFETY_NOTICE = (
    "⚠️ Reminder: Do NOT upload/sell Telegram .session files or other sensitive account access. "
    "Only upload goods you own and have rights to sell."
)

# ensure directories
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILES_DIR.mkdir(parents=True, exist_ok=True)

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot init
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# DB helpers
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            display_name TEXT,
            country TEXT,
            price INTEGER NOT NULL,
            sold INTEGER DEFAULT 0,
            uploaded_by INTEGER,
            otp TEXT
        )
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            buyer_id INTEGER,
            price INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn.commit()

init_db()

# Price list helpers (persisted JSON)
def load_prices():
    if PRICE_FILE.exists():
        try:
            return json.loads(PRICE_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}

def save_prices(prices: dict):
    PRICE_FILE.write_text(json.dumps(prices, indent=2), encoding='utf-8')

# initialize price file with DEFAULT if missing
prices = load_prices()
if 'DEFAULT' not in prices:
    prices['DEFAULT'] = 10000  # default 100.00 INR (in paise)
    save_prices(prices)

# util functions
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def add_product(filename: str, display_name: str, country: str, price: int, uploaded_by: int):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('INSERT INTO products (filename, display_name, country, price, uploaded_by) VALUES (?,?,?,?,?)',
                    (filename, display_name, country, price, uploaded_by))
        conn.commit()
        return cur.lastrowid

def list_products(country: str = None, available_only=True):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if country:
            if available_only:
                cur.execute('SELECT id,display_name,country,price FROM products WHERE country=? AND sold=0', (country,))
            else:
                cur.execute('SELECT id,display_name,country,price,sold FROM products WHERE country=?', (country,))
        else:
            if available_only:
                cur.execute('SELECT id,display_name,country,price FROM products WHERE sold=0')
            else:
                cur.execute('SELECT id,display_name,country,price,sold FROM products')
        return cur.fetchall()

def mark_sold(product_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('UPDATE products SET sold=1 WHERE id=?', (product_id,))
        conn.commit()

def record_sale(product_id: int, buyer_id: int, price: int):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('INSERT INTO sales (product_id,buyer_id,price) VALUES (?,?,?)', (product_id, buyer_id, price))
        conn.commit()

# Admin: /start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    txt = "Welcome to the Safe Marketplace Bot!
" + SAFETY_NOTICE
    if is_admin(message.from_user.id):
        txt += "

You are an admin. Use /addfile to upload a product, /stock to view inventory.
" \
               "Use /setprice <COUNTRY> <PAISE> or /setpriceprod <PRODUCT_ID> <PAISE> to set prices."
    else:
        txt += "

Use /shop to browse available products."
    await message.reply(txt)

# Admin: upload file
# Flow: /addfile -> bot asks for display name, price, country, then file

ADD_CTX = {}  # small in-memory context for uploads: {user_id: {'stage':..., ...}}

@dp.message_handler(commands=['addfile'])
async def cmd_addfile(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply("Only admins can add products.")
    ADD_CTX[message.from_user.id] = {'stage': 'ask_name'}
    await message.reply("Send me a display name/title for the product (example: 'React Course PDF - Level 1')")

@dp.message_handler(lambda m: m.from_user.id in ADD_CTX and ADD_CTX[m.from_user.id]['stage']=='ask_name')
async def add_name(message: types.Message):
    ADD_CTX[message.from_user.id]['display_name'] = message.text[:200]
    ADD_CTX[message.from_user.id]['stage'] = 'ask_price'
    await message.reply('Price in paise (example: 49900 for ₹499). Send numeric price.
' \
                        'If you want to use country-based pricing, send 0 here and set prices via /setprice.')

@dp.message_handler(lambda m: m.from_user.id in ADD_CTX and ADD_CTX[m.from_user.id]['stage']=='ask_price')
async def add_price(message: types.Message):
    txt = message.text.strip()
    if not txt.isdigit():
        return await message.reply('Please send price as integer paise only (e.g., 49900) or 0 to use country pricing.')
    ADD_CTX[message.from_user.id]['price'] = int(txt)
    ADD_CTX[message.from_user.id]['stage'] = 'ask_country'
    await message.reply('Country tag (e.g., India, USA, Other). This is used for filtering in /shop.')

@dp.message_handler(lambda m: m.from_user.id in ADD_CTX and ADD_CTX[m.from_user.id]['stage']=='ask_country')
async def add_country(message: types.Message):
    ADD_CTX[message.from_user.id]['country'] = message.text.strip()[:50]
    ADD_CTX[message.from_user.id]['stage'] = 'ask_file'
    await message.reply('Now upload the file (document). Max Telegram size applies.')

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def receive_document(message: types.Message):
    uid = message.from_user.id
    # allow only when in addfile flow OR ignore otherwise
    if uid not in ADD_CTX or ADD_CTX[uid]['stage'] != 'ask_file':
        return

    doc = message.document
    file_name = doc.file_name
    saved_path = FILES_DIR / f"{secrets.token_hex(8)}_{file_name}"
    await message.document.download(destination_file=str(saved_path))

    # finalize
    info = ADD_CTX.pop(uid)
    product_id = add_product(filename=str(saved_path.name), display_name=info['display_name'],
                             country=info['country'], price=info['price'], uploaded_by=uid)
    await message.reply(f"Product saved with id {product_id}. It is now available in /shop.
{SAFETY_NOTICE}")

# Admin: view stock
@dp.message_handler(commands=['stock'])
async def cmd_stock(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply('Only admins can view stock.')
    rows = list_products(available_only=False)
    if not rows:
        return await message.reply('Stock is empty.')
    txt_lines = ['ID | Name | Country | Price (paise) | Sold']
    for r in rows:
        # r may have 4 or 5 columns depending on call
        if len(r) == 4:
            pid, name, country, price = r
            sold = 0
        else:
            pid, name, country, price, sold = r
        txt_lines.append(f"{pid} | {name} | {country} | {price} | {sold}")
    await message.reply('
'.join(txt_lines))

# Price commands
@dp.message_handler(commands=['setprice'])
async def cmd_setprice(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply('Only admins can set prices.')
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply('Usage: /setprice <COUNTRY_KEY> <PRICE_IN_PAISE>
Example: /setprice IN 5000')
    key = parts[1].upper()
    if not parts[2].isdigit():
        return await message.reply('Price must be integer (paise). Example: 5000 for ₹50')
    price_val = int(parts[2])
    prices = load_prices()
    prices[key] = price_val
    save_prices(prices)
    await message.reply(f'Price for {key} set to {price_val} paise (₹{price_val/100:.2f})')

@dp.message_handler(commands=['setpriceprod'])
async def cmd_setpriceprod(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply('Only admins can set product prices.')
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return await message.reply('Usage: /setpriceprod <PRODUCT_ID> <PRICE_IN_PAISE>')
    pid = int(parts[1])
    price_val = int(parts[2])
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('UPDATE products SET price=? WHERE id=?', (price_val, pid))
        conn.commit()
    await message.reply(f'Product {pid} price updated to {price_val} paise (₹{price_val/100:.2f})')

@dp.message_handler(commands=['getprices'])
async def cmd_getprices(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply('Only admins can view prices.')
    prices = load_prices()
    lines = [f"{k}: {v} paise (₹{v/100:.2f})" for k, v in prices.items()]
    await message.reply('
'.join(lines))

@dp.message_handler(commands=['delprice'])
async def cmd_delprice(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply('Only admins can delete prices.')
    parts = message.text.split()
    if len(parts) != 2:
        return await message.reply('Usage: /delprice <COUNTRY_KEY>')
    key = parts[1].upper()
    prices = load_prices()
    if key in prices:
        prices.pop(key)
        save_prices(prices)
        await message.reply(f'Price for {key} removed.')
    else:
        await message.reply(f'No price set for {key}.')

# User: /shop -> choose country
@dp.message_handler(commands=['shop'])
async def cmd_shop(message: types.Message):
    rows = list_products(available_only=True)
    if not rows:
        return await message.reply('No products available right now.')
    # gather countries
    countries = sorted({r[2] for r in rows})
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in countries:
        kb.add(c)
    kb.add('All')
    await message.reply('Choose a country filter or All:', reply_markup=kb)

@dp.message_handler(lambda m: True)
async def shop_country_selected(message: types.Message):
    # if text matches one of country tags or All, show products
    text = message.text.strip()
    rows = None
    if text.lower() == 'all':
        rows = list_products(available_only=True)
    else:
        rows = list_products(country=text, available_only=True)
    if not rows:
        return await message.reply('No products for that selection. Try /shop again.', reply_markup=types.ReplyKeyboardRemove())
    # present options inline
    for r in rows:
        pid, name, country, price = r
        # if product price is 0 use country pricing
        display_price = price
        if display_price == 0:
            prices = load_prices()
            display_price = prices.get(country.upper(), prices.get('DEFAULT', 10000))
        price_display = f"₹{display_price/100:.2f}"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(text=f"Buy {price_display}", callback_data=f"buy:{pid}:{display_price}"))
        await message.reply(f"ID:{pid} | {name} | {country} | {price_display}", reply_markup=kb)
    await message.reply('To cancel keyboard press /start', reply_markup=types.ReplyKeyboardRemove())

# Callback to start purchase
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('buy:'))
async def process_buy_callback(callback_query: types.CallbackQuery):
    parts = callback_query.data.split(':')
    pid = int(parts[1])
    requested_price = int(parts[2]) if len(parts) > 2 else None
    # look up product
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('SELECT id,display_name,price FROM products WHERE id=? AND sold=0', (pid,))
        row = cur.fetchone()
    if not row:
        return await callback_query.answer('Product not available anymore.', show_alert=True)
    _, display_name, stored_price = row
    # determine effective price: product price if non-zero else requested_price
    if stored_price and stored_price > 0:
        effective_price = stored_price
    elif requested_price:
        effective_price = requested_price
    else:
        # fallback to DEFAULT
        prices = load_prices()
        effective_price = prices.get('DEFAULT', 10000)

    # send invoice
    prices_lp = [LabeledPrice(label=display_name, amount=effective_price)]
    await bot.send_invoice(callback_query.from_user.id,
                           title=display_name,
                           description=f'Purchase: {display_name}',
                           payload=f'purchase:{pid}:{effective_price}',
                           provider_token=PAYMENT_PROVIDER_TOKEN or 'TEST',
                           currency='INR',
                           prices=prices_lp,
                           start_parameter=f'pay-{pid}-{secrets.token_hex(4)}')
    await callback_query.answer()

# Pre-checkout (required)
@dp.pre_checkout_query_handler(lambda q: True)
async def pre_checkout(pre_checkout_q: types.PreCheckoutQuery):
    # Always confirm the checkout
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

# Successful payment
@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT)
async def got_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    # payload format: purchase:{pid}:{price}
    if not payload.startswith('purchase:'):
        return await message.reply('Unknown payment payload.')
    parts = payload.split(':')
    if len(parts) < 3:
        return await message.reply('Invalid payload format.')
    pid = int(parts[1])
    paid_amount = int(parts[2])

    # mark product sold and record sale
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('SELECT filename,display_name,price FROM products WHERE id=? AND sold=0', (pid,))
        row = cur.fetchone()
        if not row:
            return await message.reply('Product not available or already sold.')
        filename, display_name, stored_price = row

        # prepare zip + otp
        otp = secrets.token_hex(4)  # short password
        file_path = FILES_DIR / filename
        zip_name = FILES_DIR / f"{filename}.zip"
        with zipfile.ZipFile(zip_name, 'w') as zf:
            zf.write(file_path, arcname=Path(filename).name)

        # mark sold and record
        cur.execute('UPDATE products SET sold=1, otp=? WHERE id=?', (otp, pid))
        cur.execute('INSERT INTO sales (product_id,buyer_id,price) VALUES (?,?,?)', (pid, message.from_user.id, paid_amount))
        conn.commit()

    # send the zip file (telegram file size limits still apply)
    await message.reply('
'.join([
        f'✅ Payment received for {display_name}.',
        'File attached below. Use the OTP to unlock the delivered product.'
    ]))
    await message.reply_document(open(str(zip_name), 'rb'))
    await message.reply(f'🔐 OTP / password: `{otp}`', parse_mode='Markdown')

# Small admin helper: remove product by id
@dp.message_handler(commands=['remove'])
async def cmd_remove(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply('Only admins can remove items.')
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.reply('Usage: /remove <product_id>')
    pid = int(parts[1])
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM products WHERE id=?', (pid,))
        conn.commit()
    await message.reply(f'Product {pid} removed (if it existed).')

# Admin: quick stats
@dp.message_handler(commands=['stats'])
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.reply('Only admins can view stats.')
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM products')
        total = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM products WHERE sold=1')
        sold = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM sales')
        sales = cur.fetchone()[0]
    await message.reply(f'Total products: {total}
Sold items: {sold}
Sales records: {sales}')

# Run
if __name__ == '__main__':
    print('Bot starting...')
    executor.start_polling(dp, skip_updates=True)
