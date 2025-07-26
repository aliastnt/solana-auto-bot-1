import os
import time
import logging
import requests
import pytz
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
from solana.rpc.api import Client
from solders.keypair import Keypair

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# --- Solana client ---
client = Client(SOLANA_RPC)
keypair = Keypair.from_base58_string(PRIVATE_KEY)
wallet_pubkey = keypair.pubkey()

# --- Trade config ---
TRADE_SIZE_USD = 5.0
TEST_SIZE_USD = 0.01
STOPLOSS_PCT = 0.20
TRAILING_ACTIVATE = 0.15
TRAILING_DISTANCE = 0.10
MOVE_SL_TO_ENTRY_AT = 0.50
MAX_OPEN_TRADES = 3
MIN_LIQUIDITY = 50000

# --- State ---
open_trades = []

# --- Telegram helper ---
def send_telegram(text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text})
    except Exception as e:
        logger.error(f"Lỗi gửi Telegram: {e}")

# --- Balance ---
def get_balance():
    balance = client.get_balance(wallet_pubkey)
    return balance.value / 1e9

# --- Lấy token từ DexScreener ---
def get_top_tokens():
    try:
        url = "https://api.dexscreener.com/latest/dex/tokens/solana"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            tokens = r.json().get("pairs", [])
            return [t for t in tokens if t.get("liquidity", {}).get("usd", 0) >= MIN_LIQUIDITY]
    except Exception as e:
        logger.error(f"Lỗi lấy token: {e}")
    return []

# --- Mở lệnh ---
def open_trade(symbol, price):
    trade = {
        "symbol": symbol,
        "entry_price": price,
        "sl": price * (1 - STOPLOSS_PCT),
        "trail_high": price,
        "sl_moved": False,
        "price_now": price
    }
    open_trades.append(trade)
    send_telegram(f"Mở lệnh chính {symbol} @ {price}")

# --- Quản lý lệnh đang mở ---
def manage_open_trades():
    global open_trades
    for trade in open_trades[:]:
        price_now = trade["price_now"]
        entry = trade["entry_price"]
        pnl_pct = (price_now - entry) / entry

        # Dời SL về entry khi đạt 50%
        if pnl_pct >= MOVE_SL_TO_ENTRY_AT and not trade["sl_moved"]:
            trade["sl"] = entry
            trade["sl_moved"] = True
            send_telegram(f"Dời SL về entry cho {trade['symbol']}")

        # Trailing Stop
        if pnl_pct >= TRAILING_ACTIVATE:
            if price_now > trade["trail_high"]:
                trade["trail_high"] = price_now
                trade["sl"] = trade["trail_high"] * (1 - TRAILING_DISTANCE)
            elif price_now <= trade["sl"]:
                send_telegram(f"Trailing Stop hit {trade['symbol']} @ {price_now}")
                open_trades.remove(trade)

# --- Auto Trade job ---
def auto_trade():
    global open_trades
    if len(open_trades) < MAX_OPEN_TRADES:
        tokens = get_top_tokens()
        if tokens:
            token = tokens[0]
            price = float(token["priceUsd"])
            symbol = token["baseToken"]["symbol"]

            # Kiểm tra token bằng lệnh nhỏ
            send_telegram(f"Kiểm tra token {symbol} với lệnh nhỏ {TEST_SIZE_USD} USD (mô phỏng)")
            time.sleep(1)  # mô phỏng chờ giao dịch test
            send_telegram(f"Token {symbol} kiểm tra thành công → mở lệnh chính {TRADE_SIZE_USD} USD")
            open_trade(symbol, price)

    manage_open_trades()

# --- Telegram Commands ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade khởi động!")

def balance(update: Update, context: CallbackContext):
    bal = get_balance()
    update.message.reply_text(f"Số dư SOL: {bal} SOL")

# --- Main ---
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", balance))

    scheduler = BackgroundScheduler(timezone=pytz.utc)
    scheduler.add_job(auto_trade, "interval", seconds=10)
    scheduler.start()

    send_telegram("Bot AutoTrade khởi động (chế độ mô phỏng lệnh thử + mở lệnh chính)")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
