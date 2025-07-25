import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from solana.keypair import Keypair
import base58

# Lấy token và chat_id từ biến môi trường Railway
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
PRIVATE_KEY = os.environ.get("PRIVATE_KEY")

# Khởi tạo keypair từ private key dạng base58
def load_keypair():
    try:
        return Keypair.from_secret_key(base58.b58decode(PRIVATE_KEY))
    except Exception as e:
        print(f"Lỗi tạo keypair: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Bot đã nhận lệnh /start thành công!")

async def post_init(application):
    # Chỉ gửi thông báo một lần khi bot khởi động
    try:
        await application.bot.send_message(chat_id=CHAT_ID, text="BOT Solana AutoTrade khởi động!")
    except Exception as e:
        print(f"Lỗi gửi tin nhắn khởi động: {e}")

async def main():
    keypair = load_keypair()
    if keypair is None:
        print("Không thể khởi tạo keypair, kiểm tra lại PRIVATE_KEY")
        return

    application = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Lệnh /start
    application.add_handler(CommandHandler("start", start))

    # Chạy bot (blocking)
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
