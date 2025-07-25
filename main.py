import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# --- Log setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

# --- Solana client ---
client = Client(SOLANA_ENDPOINT)

# --- Khởi tạo keypair ---
keypair = Keypair.from_base58_string(PRIVATE_KEY)
public_key = keypair.pubkey()

# --- Scheduler ---
scheduler = BackgroundScheduler(timezone=pytz.utc)

def start(update: Update, context: CallbackContext):
    update.message.reply_text('BOT Solana AutoTrade khởi động!')

def get_balance(update: Update, context: CallbackContext):
    balance = client.get_balance(public_key)
    sol_balance = balance['result']['value'] / 1e9
    update.message.reply_text(f"Số dư SOL: {sol_balance} SOL")

# --- Logic trade cơ bản ---
def trading_logic():
    try:
        balance = client.get_balance(public_key)['result']['value'] / 1e9
        logger.info(f"Balance: {balance} SOL")

        # Dummy strategy: gửi tín hiệu mua khi SOL > 0.1
        if balance > 0.1:
            text = f"AutoTrade signal: Balance {balance} SOL > 0.1 => MUA!"
        else:
            text = f"AutoTrade signal: Balance {balance} SOL <= 0.1 => BÁN!"

        # gửi thông báo tới Telegram
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text}
        )
    except Exception as e:
        logger.error(f"Lỗi logic trade: {e}")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", get_balance))

    # Scheduler job: chạy logic trade mỗi 5 giây
    scheduler.add_job(trading_logic, 'interval', seconds=5)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
