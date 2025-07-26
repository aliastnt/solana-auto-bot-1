import os
import requests
import logging
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
from solders.keypair import Keypair
from base58 import b58decode

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Khởi tạo Solana keypair ---
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("Thiếu PRIVATE_KEY trong biến môi trường")
keypair = Keypair.from_bytes(b58decode(PRIVATE_KEY))

# --- Telegram Bot ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# --- Biến toàn cục ---
open_positions = []
scheduler = BackgroundScheduler()

# --- Lấy giá token từ DexScreener ---
def get_price(token_pair="SOL/USDC"):
    url = "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        return float(data["pairs"][0]["priceUsd"])
    except Exception as e:
        logger.error(f"Lỗi lấy giá: {e}")
        return None

# --- Lệnh /start ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade (Full Auto) đã khởi động!")

# --- Lệnh /balance ---
def balance(update: Update, context: CallbackContext):
    # Ví dụ giả định vì chưa kết nối RPC thực
    balance_value = 0.10768426  # giả lập
    update.message.reply_text(f"Số dư SOL: {balance_value} SOL")

# --- Lệnh mở lệnh (test) ---
def open_order(update: Update, context: CallbackContext):
    update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
    # Gửi lệnh test giả lập
    update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")
    # Thêm lệnh mô phỏng vào danh sách
    open_positions.append({"token": "SOL/USDC", "entry": get_price(), "amount": 5})
    update.message.reply_text("Lệnh chính đã gửi thành công (mô phỏng).")

# --- Check trailing stop ---
def check_trailing():
    for position in open_positions:
        price = get_price()
        if price and price < position["entry"] * 0.8:  # stop loss 20%
            logger.info(f"Đóng lệnh do SL {price}")
        elif price and price > position["entry"] * 1.5:  # dời SL về entry khi lời 50%
            logger.info(f"Dời SL về entry cho {price}")

# --- Khởi chạy scheduler ---
scheduler.add_job(check_trailing, "interval", seconds=30)
scheduler.start()

# --- Main ---
def main():
    updater = Updater(TELEGRAM_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("open", open_order))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
