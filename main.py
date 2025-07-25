import os
import time
import logging
import requests
import base58
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair

# --- Cấu hình log ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

# --- Solana Client ---
client = Client(SOLANA_ENDPOINT)

# --- Trade config ---
LIQUIDITY_THRESHOLD = 50000   # USD
STOPLOSS_PCT = 0.02           # 2%
TRAILING_TP_TRIGGER = 0.05    # 5%
TRAILING_DISTANCE = 0.02      # 2%
MAX_TRADES_PER_HOUR = 3

# --- Trạng thái ---
active_trade = None
trade_count = 0
last_trade_time = 0

# --- Load keypair từ Base58 ---
def load_keypair():
    return Keypair.from_bytes(base58.b58decode(PRIVATE_KEY))

# --- Telegram command ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade khởi động!")

def balance(update: Update, context: CallbackContext):
    keypair = load_keypair()
    balance = client.get_balance(keypair.pubkey())
    sol = balance['result']['value'] / 1e9
    update.message.reply_text(f"Số dư SOL: {sol} SOL")

# --- DexScreener fetch ---
def fetch_top_tokens():
    url = "https://api.dexscreener.com/latest/dex/tokens/solana"
    r = requests.get(url, timeout=10)
    if r.status_code == 200:
        data = r.json()
        return [
            t for t in data["pairs"]
            if t.get("liquidity", {}).get("usd", 0) > LIQUIDITY_THRESHOLD
        ]
    return []

# --- Mở lệnh ---
def open_trade(token: dict, context: CallbackContext):
    global active_trade, trade_count, last_trade_time
    price = float(token["priceUsd"])
    active_trade = {
        "symbol": token["baseToken"]["symbol"],
        "entry_price": price,
        "stoploss": price * (1 - STOPLOSS_PCT),
        "trail_trigger": price * (1 + TRAILING_TP_TRIGGER),
        "trail_high": price,
        "trail_stop": price * (1 + TRAILING_TP_TRIGGER - TRAILING_DISTANCE)
    }
    trade_count += 1
    last_trade_time = time.time()
    context.bot.send_message(chat_id=CHAT_ID, text=f"Mở lệnh {active_trade['symbol']} @ {price}")

# --- Quản lý lệnh ---
def manage_trade(context: CallbackContext):
    global active_trade
    if not active_trade:
        return

    symbol = active_trade["symbol"]
    url = f"https://api.dexscreener.com/latest/dex/tokens/{symbol}"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return
    price = float(r.json()["pairs"][0]["priceUsd"])

    # Stoploss check
    if price <= active_trade["stoploss"]:
        context.bot.send_message(chat_id=CHAT_ID, text=f"Đóng lệnh {symbol} hit stoploss @ {price}")
        active_trade = None
        return

    # Trailing TP
    if price >= active_trade["trail_trigger"]:
        if price > active_trade["trail_high"]:
            active_trade["trail_high"] = price
            active_trade["trail_stop"] = price * (1 - TRAILING_DISTANCE)
        elif price <= active_trade["trail_stop"]:
            context.bot.send_message(chat_id=CHAT_ID, text=f"Đóng lệnh {symbol} trailing TP @ {price}")
            active_trade = None

# --- Auto trade vòng lặp ---
def auto_trade(context: CallbackContext):
    global trade_count, last_trade_time, active_trade
    now = time.time()

    # Reset trade count mỗi giờ
    if now - last_trade_time > 3600:
        trade_count = 0

    # Quản lý lệnh đang mở
    manage_trade(context)

    # Mở lệnh mới nếu chưa có
    if not active_trade and trade_count < MAX_TRADES_PER_HOUR:
        tokens = fetch_top_tokens()
        if tokens:
            token = tokens[0]  # lấy token đầu tiên đủ điều kiện
            open_trade(token, context)

# --- Main ---
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Command handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", balance))

    # Auto trade job chạy mỗi 10s
    job_queue = updater.job_queue
    job_queue.run_repeating(auto_trade, interval=10, first=5)

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
