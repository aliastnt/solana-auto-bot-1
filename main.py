import os
import asyncio
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer

# Load biến môi trường
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Dạng base58
SOLANA_ENDPOINT = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")

logging.basicConfig(level=logging.INFO)

# Load keypair từ base58
def load_keypair():
    try:
        return Keypair.from_base58_string(PRIVATE_KEY)
    except Exception as e:
        logging.error(f"Lỗi load keypair: {e}")
        raise

# Gửi SOL
async def send_sol(destination: str, amount_sol: float):
    try:
        keypair = load_keypair()
        dest_pubkey = Pubkey.from_string(destination)

        async with AsyncClient(SOLANA_ENDPOINT) as client:
            lamports = int(amount_sol * 10**9)
            params = TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=dest_pubkey,
                lamports=lamports,
            )
            txn = Transaction().add(transfer(params))
            resp = await client.send_transaction(txn, keypair)
            return resp
    except Exception as e:
        logging.error(f"Lỗi gửi SOL: {e}")
        return str(e)

# Command /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("BOT Solana AutoTrade (Full Auto) đã khởi động!")

# Command /open
async def open_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Đang gửi lệnh thử (0.01 SOL)...")
    tx_sig = await send_sol("11111111111111111111111111111111", 0.01)
    await update.message.reply_text(f"Lệnh thử hoàn tất. TX: {tx_sig}")

# Main
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("open", open_order))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
