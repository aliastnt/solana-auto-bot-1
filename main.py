import os
import logging
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey

# --- Logging ---
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
keypair = Keypair.from_base58_string(PRIVATE_KEY)
pubkey = keypair.pubkey()

# --- Commands ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade khởi động!")

def balance(update: Update, context: CallbackContext):
    try:
        balance = client.get_balance(pubkey)
        lamports = balance.value  # dùng thuộc tính value thay vì []
        sol = lamports / 1e9
        update.message.reply_text(f"Số dư SOL: {sol} SOL")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy số dư: {str(e)}")

def open_trade(update: Update, context: CallbackContext):
    try:
        update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
        logger.info("Thực hiện lệnh thử: 0.01 USD")

        # Giả lập lệnh thử thành công
        test_success = True

        if test_success:
            update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")
            logger.info("Thực hiện lệnh chính: 5 USD, SL=20%, trailing stop 10%")
            # TODO: Code mở lệnh chính ở đây (Solana DEX API hoặc serum)
            update.message.reply_text("Lệnh chính đã gửi thành công (mô phỏng).")
        else:
            update.message.reply_text("Lệnh thử thất bại, hủy lệnh chính.")
            logger.warning("Lệnh thử thất bại!")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi mở lệnh: {str(e)}")
        logger.error(f"Lỗi khi mở lệnh: {e}")

# --- Main ---
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", balance))
    dispatcher.add_handler(CommandHandler("open", open_trade))

    logger.info("BOT Solana AutoTrade đã khởi động (log chi tiết).")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
