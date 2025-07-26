import os
import asyncio
import logging
import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solana.rpc.async_api import AsyncClient
from solana.transaction import Transaction
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --------------------
# ENV
# --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # dạng base58
SOLANA_ENDPOINT = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")

# --------------------
# LOGGING
# --------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------
# WALLET LOADER
# --------------------
def load_keypair():
    try:
        secret = base58.b58decode(PRIVATE_KEY)
        return Keypair.from_bytes(secret)
    except Exception as e:
        logger.error(f"Lỗi load keypair: {e}")
        raise

keypair = load_keypair()
pubkey = keypair.pubkey()

# --------------------
# SOLANA CLIENT
# --------------------
async def send_sol(to_pubkey: str, lamports: int):
    async with AsyncClient(SOLANA_ENDPOINT) as client:
        tx = Transaction().add(
            transfer(
                TransferParams(
                    from_pubkey=pubkey,
                    to_pubkey=Pubkey.from_string(to_pubkey),
                    lamports=lamports
                )
            )
        )
        resp = await client.send_transaction(tx, keypair)
        return resp

# --------------------
# TELEGRAM BOT HANDLERS
# --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("BOT Solana AutoTrade đã khởi động!")

async def open_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Đang gửi lệnh thử (0.01 SOL)...")
    result = await send_sol(str(pubkey), 10000000)  # gửi về ví chính mình 0.01 SOL
    await update.message.reply_text(f"Lệnh thử đã gửi: {result}")

# --------------------
# JOB ĐỊNH KỲ
# --------------------
async def check_trailing():
    # Logic trade cũ của bạn (không thay đổi)
    logger.info("Đang chạy check_trailing...")

# --------------------
# MAIN APP
# --------------------
async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("open", open_trade))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_trailing, "interval", seconds=30)
    scheduler.start()

    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
