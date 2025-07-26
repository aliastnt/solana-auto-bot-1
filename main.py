import os
import logging
import base58
from telegram.ext import Updater, CommandHandler
from solana.keypair import Keypair
from solana.rpc.api import Client
from solana.system_program import TransferParams, transfer
from solana.transaction import Transaction
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# ENV
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")

# Solana Client
solana_client = Client(SOLANA_ENDPOINT)

def load_keypair():
    """Load keypair từ private key dạng base58"""
    secret_key = base58.b58decode(PRIVATE_KEY)
    return Keypair.from_secret_key(secret_key)

keypair = load_keypair()

def send_sol(receiver, amount_sol):
    """Gửi SOL"""
    lamports = int(amount_sol * 1_000_000_000)
    txn = Transaction().add(
        transfer(TransferParams(
            from_pubkey=keypair.public_key,
            to_pubkey=receiver,
            lamports=lamports
        ))
    )
    resp = solana_client.send_transaction(txn, keypair)
    return resp

def start(update, context):
    update.message.reply_text("BOT Solana AutoTrade đã khởi động!")

def open_trade(update, context):
    update.message.reply_text("Đang gửi lệnh thử (0.01 SOL)...")
    receiver = keypair.public_key  # test gửi cho chính mình
    tx = send_sol(receiver, 0.01)
    update.message.reply_text(f"Lệnh thử đã gửi: {tx}")

def check_trailing():
    logger.info("Đang chạy logic trailing stop (demo)...")

def main():
    updater = Updater(token=BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("open", open_trade))

    # Scheduler
    scheduler = BackgroundScheduler(timezone=pytz.utc)
    scheduler.add_job(check_trailing, 'interval', seconds=30)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
