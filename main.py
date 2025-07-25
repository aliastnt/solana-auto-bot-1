import os
import asyncio
import base58
from solana.keypair import Keypair
from solana.rpc.async_api import AsyncClient
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Đọc biến môi trường từ Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_ENDPOINT = "https://api.mainnet-beta.solana.com"

async def create_keypair():
    try:
        # Decode Base58 từ Phantom
        secret_key_bytes = base58.b58decode(PRIVATE_KEY)
        keypair = Keypair.from_secret_key(secret_key_bytes)
        return keypair
    except Exception as e:
        return f"Lỗi tạo keypair: {e}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keypair = await create_keypair()
    if isinstance(keypair, str):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=keypair)
        return

    # Public Key
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"BOT Solana AutoTrade khởi động!\nPublic Key: {keypair.public_key}"
    )

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
