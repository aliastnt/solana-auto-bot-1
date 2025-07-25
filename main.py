import os
from solana.keypair import Keypair
from solana.rpc.api import Client
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram import Update

BOT_TOKEN = os.getenv("BOT_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade khởi động!")

def main():
    # Tạo kết nối tới Solana
    client = Client("https://api.mainnet-beta.solana.com")
    
    # Tạo Keypair từ private key dạng base58
    try:
        secret_key = bytes.fromhex(PRIVATE_KEY)
        keypair = Keypair.from_secret_key(secret_key)
        print(f"Public key: {keypair.public_key}")
    except Exception as e:
        print(f"Lỗi tạo keypair: {e}")

    # Khởi tạo bot Telegram
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))

    # Chạy bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
