import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
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

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('BOT Solana AutoTrade khởi động!')

def get_balance(update: Update, context: CallbackContext) -> None:
    pubkey = Keypair.from_base58_string(PRIVATE_KEY).pubkey()
    balance = client.get_balance(pubkey)
    update.message.reply_text(f"Số dư SOL: {balance['result']['value'] / 1e9} SOL")

def main() -> None:
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", get_balance))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
