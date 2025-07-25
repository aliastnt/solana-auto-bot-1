import os
import logging
import requests
import time
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

# --- Client ---
client = Client(SOLANA_ENDPOINT)
scheduler = BackgroundScheduler()

# --- Keypair ---
keypair = Keypair.from_base58_string(PRIVATE_KEY)

# --- Trade config ---
PAIR = "SOL/USDC"
ENTRY_PRICE = 150.0      # Giá mua vào
STOP_LOSS = 145.0        # SL
TAKE_PROFIT = 160.0      # TP
TRADE_SIZE = 0.1         # Số lượng SOL

def send_telegram_message(message: str):
    """Gửi tin nhắn tới Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, json=payload)

def get_balance(update: Update, context: CallbackContext) -> None:
    """Lấy số dư ví"""
    balance = client.get_balance(keypair.pubkey())
    update.message.reply_text(f"Số dư SOL: {balance['result']['value'] / 1e9} SOL")

def get_price() -> float:
    """Lấy giá SOL từ API (ví dụ Coingecko)"""
    url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
    resp = requests.get(url).json()
    return resp["solana"]["usd"]

def execute_trade(action: str, size: float):
    """Thực hiện lệnh (demo: chỉ log, chưa kết nối DEX)"""
    send_telegram_message(f"Thực hiện lệnh {action} {size} {PAIR}")
    logger.info(f"Thực hiện lệnh {action} {size} {PAIR}")

def trading_logic():
    """Logic auto trade"""
    try:
        price = get_price()
        logger.info(f"Giá hiện tại SOL: {price}")
        # Điều kiện vào lệnh
        if price <= ENTRY_PRICE:
            execute_trade("MUA", TRADE_SIZE)
            send_telegram_message(f"MUA {TRADE_SIZE} SOL @ {price}")
        # SL
        elif price <= STOP_LOSS:
            execute_trade("BÁN (SL)", TRADE_SIZE)
            send_telegram_message(f"BÁN (SL) {TRADE_SIZE} SOL @ {price}")
        # TP
        elif price >= TAKE_PROFIT:
            execute_trade("BÁN (TP)", TRADE_SIZE)
            send_telegram_message(f"BÁN (TP) {TRADE_SIZE} SOL @ {price}")
    except Exception as e:
        logger.error(f"Lỗi trading_logic: {e}")
        send_telegram_message(f"Lỗi trading_logic: {e}")

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('BOT Solana AutoTrade khởi động!')

def main() -> None:
    # Telegram Bot
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", get_balance))

    # Scheduler AutoTrade (5 giây check 1 lần)
    scheduler.add_job(trading_logic, 'interval', seconds=5)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    send_telegram_message("BOT AutoTrade đã khởi động!")
    main()
