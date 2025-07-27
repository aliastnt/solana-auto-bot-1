import os
import logging
import base58
from solana.rpc.api import Client
from solana.keypair import Keypair
from solana.transaction import Transaction
from solders.system_program import transfer, TransferParams
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.rpc.responses import SendTransactionResp
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV Variables ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # base58 encoded
SOLANA_ENDPOINT = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")

# --- Solana Client ---
client = Client(SOLANA_ENDPOINT)

# --- Load Keypair ---
def load_keypair():
    secret = base58.b58decode(PRIVATE_KEY)
    return Keypair.from_secret_key(secret)

keypair = load_keypair()

# --- Check Balance ---
def check_balance():
    balance = client.get_balance(keypair.pubkey())
    lamports = balance['result']['value']
    sol = lamports / 1e9
    return sol

# --- Transfer SOL ---
def send_sol(destination, amount_sol):
    lamports = int(amount_sol * 1e9)
    dest_pubkey = Pubkey.from_string(destination)

    params = TransferParams(
        from_pubkey=keypair.pubkey(),
        to_pubkey=dest_pubkey,
        lamports=lamports
    )
    ix = transfer(params)
    tx = Transaction().add(ix)
    tx.sign(keypair)
    response = client.send_transaction(tx)
    return response

# --- Telegram Commands ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade (Full Auto) đã khởi động!")

def balance(update: Update, context: CallbackContext):
    sol = check_balance()
    update.message.reply_text(f"Số dư: {sol:.4f} SOL")

def send(update: Update, context: CallbackContext):
    try:
        destination = context.args[0]
        amount = float(context.args[1])
        response = send_sol(destination, amount)
        update.message.reply_text(f"Gửi thành công: {response}")
    except Exception as e:
        update.message.reply_text(f"Lỗi: {str(e)}")

# --- Scheduler ---
scheduler = BackgroundScheduler()

def scheduled_task():
    logger.info(f"[Scheduler] Balance: {check_balance()} SOL")

scheduler.add_job(scheduled_task, 'interval', seconds=30)
scheduler.start()

# --- Main ---
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("send", send))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
