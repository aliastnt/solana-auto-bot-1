import os
import logging
import requests
import pytz
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey

# --- Cấu hình log ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Lấy ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

# --- Solana Client ---
client = Client(SOLANA_ENDPOINT)
keypair = Keypair.from_base58_string(PRIVATE_KEY)

# --- Bot command ---
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('BOT Solana AutoTrade khởi động!')

def get_balance(update: Update, context: CallbackContext) -> None:
    pubkey = keypair.pubkey()
    balance = client.get_balance(pubkey)
    update.message.reply_text(f"Số dư SOL: {balance['result']['value'] / 1e9} SOL")

# --- Auto Trading Logic ---
def trading_logic():
    pubkey = keypair.pubkey()
    balance = client.get_balance(pubkey)
    message = f"[AutoTrade] Số dư SOL: {balance['result']['value'] / 1e9} SOL"
    send_telegram_message(message)

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        logger.error(f"Không gửi được tin nhắn Telegram: {e}")

def main():
    # Telegram Bot
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", get_balance))

    # Scheduler Auto Trade
    scheduler = BackgroundScheduler(timezone=pytz.utc)
    scheduler.add_job(trading_logic, 'interval', seconds=5)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
