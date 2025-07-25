import os
import logging
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

# --- Lấy ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

# --- Solana Client ---
client = Client(SOLANA_ENDPOINT)

def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade khởi động!")

def get_balance(update: Update, context: CallbackContext):
    try:
        logger.info("Đang tạo keypair từ PRIVATE_KEY")
        kp = Keypair.from_base58_string(PRIVATE_KEY)
        balance = client.get_balance(kp.pubkey())
        sol_balance = balance.value / 1e9
        update.message.reply_text(f"Số dư SOL: {sol_balance} SOL")
    except Exception as e:
        logger.error(f"Lỗi khi lấy số dư: {e}")
        update.message.reply_text(f"Lỗi khi lấy số dư: {e}")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", get_balance))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
