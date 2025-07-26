import os
import logging
import base58
from solana.keypair import Keypair
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV ---
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# --- Load Keypair ---
def load_keypair():
    try:
        if "," in PRIVATE_KEY:
            # dạng list int: "1,2,3,..."
            secret = [int(x) for x in PRIVATE_KEY.split(",")]
            return Keypair.from_bytes(bytes(secret))
        else:
            # dạng base58: "3tbhYA..."
            secret = base58.b58decode(PRIVATE_KEY)
            return Keypair.from_bytes(secret)
    except Exception as e:
        logger.error(f"Lỗi load keypair: {e}")
        raise

keypair = load_keypair()
client = Client(RPC_URL)

# --- Auto Trade Logic ---
def check_trailing():
    # ví dụ mô phỏng giao dịch
    logger.info("Đang chạy auto-check trailing stop...")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="BOT Solana AutoTrade (Full Auto) đã khởi động!")

async def open_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Mô phỏng chuyển SOL (thử với 0.01 lamports)
        to_pubkey = keypair.public_key
        params = TransferParams(from_pubkey=keypair.public_key,
                                to_pubkey=to_pubkey,
                                lamports=10000)  # 0.00001 SOL
        transaction = Transaction().add(transfer(params))
        resp = client.send_transaction(transaction, keypair)
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text=f"Lệnh chính đã gửi thành công: {resp}")
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text=f"Lỗi mở lệnh: {e}")

def main():
    # Scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_trailing, "interval", seconds=30)
    scheduler.start()

    # Telegram
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("open", open_order))

    logger.info("BOT đang chạy...")
    app.run_polling()

if __name__ == "__main__":
    main()
