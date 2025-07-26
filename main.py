import os
import json
import requests
import pytz
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey

# ---------------- Cấu hình ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")   # Private key base58
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# ---------------- Logging ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- Kết nối ----------------
client = Client(RPC_URL)
keypair = Keypair.from_base58_string(PRIVATE_KEY)
public_key = keypair.pubkey()

# ---------------- Dữ liệu lệnh ----------------
orders = []
trailing_active = False

# ---------------- Hàm lấy giá từ DexScreener ----------------
def get_price(token_address):
    url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{token_address}"
    r = requests.get(url)
    if r.status_code == 200:
        data = r.json()
        price = float(data["pairs"][0]["priceUsd"])
        return price
    return None

# ---------------- Hàm mở lệnh ----------------
def open_trade():
    # Lệnh test trước
    logger.info("Đang gửi lệnh thử 0.01 USD...")
    # Giả lập lệnh test thành công
    logger.info("Lệnh thử thành công! Mở lệnh chính 5 USD...")
    # Giả lập lệnh chính
    logger.info("Lệnh chính đã gửi thành công (mô phỏng).")

# ---------------- Trailing stop ----------------
def check_trailing():
    global trailing_active
    if not trailing_active or len(orders) == 0:
        return
    # Logic trailing stop giả lập
    logger.info("Đang kiểm tra trailing stop...")

# ---------------- Các command telegram ----------------
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade (Full Auto) đã khởi động!")

def balance(update: Update, context: CallbackContext):
    balance = client.get_balance(public_key).value / 1e9
    update.message.reply_text(f"Số dư SOL: {balance:.8f} SOL")

def open_command(update: Update, context: CallbackContext):
    open_trade()
    update.message.reply_text("Lệnh thử và lệnh chính đã gửi thành công (mô phỏng).")

# ---------------- Chạy bot ----------------
def main():
    updater = Updater(TELEGRAM_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", balance))
    dispatcher.add_handler(CommandHandler("open", open_command))

    updater.start_polling()

    scheduler = BackgroundScheduler(timezone=pytz.UTC)
    scheduler.add_job(check_trailing, "interval", seconds=30, timezone=pytz.UTC)
    scheduler.start()

    updater.idle()

if __name__ == "__main__":
    main()
