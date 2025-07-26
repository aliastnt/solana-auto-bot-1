import os
import logging
import requests
import json
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
from solders.keypair import Keypair
from solders.transaction import Transaction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.rpc.requests import SendTransaction
from solders.rpc import RpcClient

# =====================
# CONFIGURATION
# =====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
PRIVATE_KEY = os.environ.get("PRIVATE_KEY")  # Base58 encoded
SOLANA_ENDPOINT = os.environ.get("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")
DEX_API = "https://api.dexscreener.com/latest/dex/tokens/"
TRADE_AMOUNT_MAIN = 5.0    # lệnh chính
TRADE_AMOUNT_TEST = 0.01   # lệnh test

bot = Bot(token=BOT_TOKEN)
client = RpcClient(SOLANA_ENDPOINT)

# =====================
# LOGGING
# =====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================
# FUNCTIONS
# =====================
def send_telegram_message(message):
    try:
        bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Lỗi gửi Telegram: {e}")

def get_price(token_address):
    try:
        r = requests.get(f"{DEX_API}{token_address}", timeout=5)
        data = r.json()
        price = float(data['pairs'][0]['priceUsd'])
        return price
    except Exception as e:
        logger.error(f"Lỗi lấy giá token: {e}")
        return None

def logic_check_token(token_address):
    price = get_price(token_address)
    if price and price > 0.5:  # ví dụ điều kiện lọc
        return True
    return False

def send_transaction(amount, token_address):
    try:
        kp = Keypair.from_base58_string(PRIVATE_KEY)
        receiver = Pubkey.from_string(token_address)  
        # Lưu ý: đây là ví dụ chuyển SOL, cần thay đổi nếu token là SPL khác
        tx = Transaction.new_signed_with_payer(
            Message.new_with_blockhash(
                [client.request_airdrop(kp.pubkey(), int(amount*1e9))], 
                client.get_latest_blockhash().value.blockhash,
                kp.pubkey()
            ),
            [kp],
            client.get_latest_blockhash().value.blockhash
        )
        res = client.send_transaction(tx)
        logger.info(f"Giao dịch hash: {res}")
        return True
    except Exception as e:
        logger.error(f"Lỗi gửi lệnh thực: {e}")
        return False

def open_order(token_address):
    send_telegram_message(f"Đang gửi lệnh thử ({TRADE_AMOUNT_TEST} USD) token {token_address}...")
    if send_transaction(TRADE_AMOUNT_TEST, token_address):
        send_telegram_message("Lệnh thử thành công! Mở lệnh chính 5 USD...")
        if send_transaction(TRADE_AMOUNT_MAIN, token_address):
            send_telegram_message("Lệnh chính đã gửi thành công.")
        else:
            send_telegram_message("Lỗi mở lệnh chính.")
    else:
        send_telegram_message("Lỗi mở lệnh thử.")

def trailing_stop_check():
    logger.info("Kiểm tra trailing stop...")

def auto_scan_tokens():
    token_list = ["So11111111111111111111111111111111111111112"]  # ví dụ SOL
    for token in token_list:
        if logic_check_token(token):
            open_order(token)

# =====================
# SCHEDULER
# =====================
scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(auto_scan_tokens, "interval", minutes=1)
scheduler.add_job(trailing_stop_check, "interval", seconds=30)
scheduler.start()

send_telegram_message("BOT Solana AutoTrade (Full Auto) đã khởi động!")

# Giữ bot chạy liên tục
import time
while True:
    time.sleep(1)
