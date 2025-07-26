import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import utc  # Sử dụng pytz thay cho zoneinfo

# --- Cấu hình log ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Lấy ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

# --- Solana Client ---
client = Client(SOLANA_ENDPOINT)

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('BOT Solana AutoTrade khởi động!')

def get_balance(update: Update, context: CallbackContext) -> None:
    try:
        pubkey = Keypair.from_base58_string(PRIVATE_KEY).pubkey()
        balance_result = client.get_balance(pubkey)
        sol_balance = balance_result.value / 1e9
        update.message.reply_text(f"Số dư SOL: {sol_balance} SOL")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy số dư: {e}")

def trading_logic():
    # Logic trade mẫu - có thể thay bằng logic thực tế sau này
    logger.info("Đang chạy logic quét token và mở lệnh thử (mô phỏng)...")

def main() -> None:
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", get_balance))

    # Scheduler với timezone dùng pytz
    scheduler = BackgroundScheduler(timezone=utc)
    scheduler.add_job(trading_logic, 'interval', seconds=10)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
