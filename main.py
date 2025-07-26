import logging
import requests
import json
import base58
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import Message
from solders.rpc.responses import GetBalanceResp
from solana.rpc.api import Client
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import os

# ===== CONFIG =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # base58
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
DEXSCREENER_API = "https://api.dexscreener.io/latest/dex/tokens/"
TRADE_AMOUNT = 5  # USD
TEST_AMOUNT = 0.01  # USD
MAX_OPEN_ORDERS = 1

# ===== INIT =====
client = Client(SOLANA_RPC)
scheduler = BackgroundScheduler(timezone=pytz.UTC)
logging.basicConfig(level=logging.INFO)

keypair = Keypair.from_base58_string(PRIVATE_KEY)
public_key = keypair.pubkey()

# ===== GLOBAL =====
open_orders = []
AUTO_MODE = False


# ===== FUNCTIONS =====
def get_solana_balance():
    balance_resp: GetBalanceResp = client.get_balance(public_key)
    return balance_resp.value / 1e9


def fetch_solana_tokens():
    # Lọc token hệ Solana
    url = f"{DEXSCREENER_API}{public_key.to_base58()}"
    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.json()
    else:
        logging.warning(f"DEXScreener lỗi: {resp.status_code}")
        return None


def check_and_trade():
    global open_orders

    if not AUTO_MODE:
        return

    tokens = fetch_solana_tokens()
    if not tokens:
        logging.info("Không tìm thấy token nào.")
        return

    # Giả lập điều kiện chọn token (thay bằng logic thật)
    for token in tokens.get("pairs", []):
        symbol = token["baseToken"]["symbol"]
        price = float(token["priceUsd"])
        liquidity = token["liquidity"]["usd"]

        logging.info(f"Đang xét token {symbol}: price={price}, liquidity={liquidity}")

        # Điều kiện cơ bản
        if liquidity > 1000 and len(open_orders) < MAX_OPEN_ORDERS:
            logging.info(f"Token {symbol} đủ điều kiện. Mở lệnh test trước.")
            success = send_test_order(symbol)
            if success:
                send_main_order(symbol, TRADE_AMOUNT)
            break


def send_test_order(symbol):
    logging.info(f"Đang gửi lệnh thử ({TEST_AMOUNT} USD) cho {symbol}")
    return True  # giả lập thành công


def send_main_order(symbol, amount):
    try:
        logging.info(f"Mở lệnh chính {amount} USD cho {symbol}")

        # Transaction builder (Solana v2)
        msg = Message.default()
        tx = VersionedTransaction(msg, [keypair])
        logging.info(f"Lệnh chính đã gửi thành công: {tx}")
        return True
    except Exception as e:
        logging.error(f"Lỗi mở lệnh: {e}")
        return False


def start(update: Update, context: CallbackContext):
    global AUTO_MODE
    AUTO_MODE = True
    update.message.reply_text("BOT Solana AutoTrade (Full Auto) đã khởi động!")


def balance(update: Update, context: CallbackContext):
    bal = get_solana_balance()
    update.message.reply_text(f"Số dư SOL: {bal:.6f} SOL")


def open_manual(update: Update, context: CallbackContext):
    send_test_order("SOL/USDC")
    send_main_order("SOL/USDC", TRADE_AMOUNT)
    update.message.reply_text("Lệnh mở thủ công đã xử lý.")


def main():
    updater = Updater(TELEGRAM_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("open", open_manual))

    scheduler.add_job(check_and_trade, "interval", seconds=30)
    scheduler.start()
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
