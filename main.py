import os
import logging
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.system_program import TransferParams, transfer
import base58

# -------------------- Logging --------------------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- Environment --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")

# -------------------- Solana --------------------
client = Client(SOLANA_ENDPOINT)

def load_keypair():
    try:
        secret = base58.b58decode(PRIVATE_KEY)
        return Keypair.from_bytes(secret)
    except Exception as e:
        logger.error(f"Error loading keypair: {e}")
        raise

def send_sol_transaction(destination: str, amount_sol: float):
    try:
        sender = load_keypair()
        recipient = Pubkey.from_string(destination)
        lamports = int(amount_sol * 1_000_000_000)
        tx = Transaction().add(
            transfer(TransferParams(from_pubkey=sender.pubkey(), to_pubkey=recipient, lamports=lamports))
        )
        response = client.send_transaction(tx, sender, opts=TxOpts(skip_preflight=True))
        return response
    except Exception as e:
        logger.error(f"Transaction failed: {e}")
        return {"error": str(e)}

# -------------------- Telegram --------------------
def start(update: Update, context: CallbackContext):
    update.message.reply_text('BOT Solana AutoTrade đã khởi động!')

def open_trade(update: Update, context: CallbackContext):
    update.message.reply_text('Đang gửi lệnh thử (0.01 SOL)...')
    resp = send_sol_transaction("ENTER_DESTINATION_ADDRESS", 0.01)
    if "error" in resp:
        update.message.reply_text(f"Lỗi giao dịch: {resp['error']}")
    else:
        update.message.reply_text(f"Giao dịch thành công! TxSig: {resp['result']}")

# -------------------- Scheduler --------------------
def check_trailing():
    logger.info("Running trailing check job...")

scheduler = BackgroundScheduler()
scheduler.add_job(check_trailing, 'interval', seconds=30)
scheduler.start()

# -------------------- Main --------------------
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("open", open_trade))
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
