import logging
import os
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solders.keypair import Keypair   # Sử dụng solders thay cho solana.keypair
from solana.publickey import PublicKey

# Cấu hình log
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Lấy biến môi trường từ Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Lệnh /start
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade khởi động!")

# Lệnh /createkeypair để test private key
def create_keypair(update: Update, context: CallbackContext):
    try:
        # Chuyển private key dạng hex thành keypair
        keypair = Keypair.from_bytes(bytes.fromhex(PRIVATE_KEY))
        pubkey = str(keypair.pubkey())
        update.message.reply_text(f"Keypair tạo thành công!\nPublic key: {pubkey}")
    except Exception as e:
        update.message.reply_text(f"Lỗi tạo keypair: {e}")

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("createkeypair", create_keypair))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
