import os
import base58
import logging
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.transaction import Transaction
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Logging setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
PRIVATE_KEY_B58 = os.getenv("PRIVATE_KEY")

# --- Solana setup ---
client = Client("https://api.mainnet-beta.solana.com")

try:
    keypair = Keypair.from_bytes(base58.b58decode(PRIVATE_KEY_B58))
    public_key = keypair.pubkey()
    logger.info(f"Public Key: {public_key}")
except Exception as e:
    logger.error(f"Error creating keypair: {e}")
    raise SystemExit(e)

# --- Telegram Bot ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="BOT Solana AutoTrade khởi động!"
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balance_result = client.get_balance(public_key)
    sol_balance = balance_result['result']['value'] / 10**9
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"SOL Balance: {sol_balance} SOL"
    )

async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ví dụ: chưa thực hiện giao dịch thực, chỉ demo
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Thực hiện lệnh giao dịch giả lập thành công!"
    )

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("trade", trade))
    app.run_polling()

if __name__ == "__main__":
    main()
