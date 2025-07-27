import os
import requests
import logging
import time
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from solana.rpc.types import TxOpts

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Phantom private key dạng base58
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

client = Client(RPC_URL)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

connected_keypair = None
tracked_pairs = {}
open_orders = {}
alert_prices = {}

# --- WALLET CONNECT ---
def connect_wallet(private_key: str):
    global connected_keypair
    connected_keypair = Keypair.from_base58_string(private_key)
    return str(connected_keypair.pubkey())

# --- TELEGRAM COMMANDS ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Xin chào! Đây là bot giao dịch Solana.\n"
        "Các lệnh khả dụng:\n"
        "/connect <PRIVATE_KEY> - Kết nối ví\n"
        "/connect new - Tạo ví mới\n"
        "/address - Xem địa chỉ ví\n"
        "/balance - Xem số dư SOL\n"
        "/setpair <PAIR> - Chọn token pair DexScreener\n"
        "/price - Giá hiện tại\n"
        "/alert <GIÁ_USD> - Đặt cảnh báo giá\n"
        "/send <ĐỊA_CHỈ> <SỐ_SOL> - Chuyển SOL\n"
        "/close <PAIR> - Đóng lệnh thủ công"
    )

def connect_cmd(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        update.message.reply_text("Vui lòng cung cấp private key hoặc nhập 'new'")
        return
    if context.args[0] == "new":
        kp = Keypair()
        update.message.reply_text(f"Tạo ví mới thành công:\nĐịa chỉ: {kp.pubkey()}\nPrivateKey: {kp.to_base58_string()}")
    else:
        pubkey = connect_wallet(context.args[0])
        update.message.reply_text(f"Kết nối ví thành công!\nĐịa chỉ: {pubkey}")

def address(update: Update, context: CallbackContext):
    if not connected_keypair:
        update.message.reply_text("Chưa kết nối ví.")
        return
    update.message.reply_text(f"Địa chỉ ví: {connected_keypair.pubkey()}")

def balance(update: Update, context: CallbackContext):
    if not connected_keypair:
        update.message.reply_text("Chưa kết nối ví.")
        return
    bal = client.get_balance(connected_keypair.pubkey()).value / 1e9
    update.message.reply_text(f"Số dư: {bal:.4f} SOL")

def close_order(update: Update, context: CallbackContext):
    if len(context.args) == 0:
        update.message.reply_text("Vui lòng nhập cặp token cần đóng.")
        return
    pair = context.args[0]
    if pair in open_orders:
        del open_orders[pair]
        update.message.reply_text(f"Đã đóng lệnh {pair}.")
    else:
        update.message.reply_text(f"Không có lệnh mở với {pair}")

# --- DEXSCREENER SCAN ---
def scan_tokens():
    url = "https://api.dexscreener.io/latest/dex/pairs/solana"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        for pair in data.get("pairs", []):
            price_change = pair.get("priceChange", {}).get("m5", 0)
            volume = pair.get("volume", {}).get("h24", 0)
            if price_change and volume and float(volume) > 50000 and abs(float(price_change)) >= 2:
                pair_address = pair.get("pairAddress")
                if pair_address not in open_orders:
                    place_test_trade(pair_address)
    except Exception as e:
        logger.error(f"Lỗi scan token: {e}")

# --- ORDER FLOW ---
def place_test_trade(pair_address):
    logger.info(f"Thực hiện lệnh test {pair_address}")
    time.sleep(2)
    # giả định thành công, sau đó vào lệnh chính
    place_main_trade(pair_address)

def place_main_trade(pair_address):
    balance = client.get_balance(connected_keypair.pubkey()).value / 1e9
    main_order_size = balance * 0.3  # 30% vốn
    open_orders[pair_address] = {
        "entry": 1.0,  # giá placeholder
        "amount": main_order_size,
        "stop_loss": 1.0 * 0.9,
        "trailing_high": 1.0
    }
    logger.info(f"Mở lệnh chính {pair_address} với vốn {main_order_size} SOL")

def manage_orders():
    to_remove = []
    for pair, order in open_orders.items():
        current_price = order["entry"] * 1.1  # placeholder tăng giá
        if current_price > order["trailing_high"]:
            order["trailing_high"] = current_price
            order["stop_loss"] = current_price * 0.9
        if current_price < order["stop_loss"]:
            logger.info(f"Hit stop loss {pair}, đóng lệnh")
            to_remove.append(pair)
    for pair in to_remove:
        del open_orders[pair]

# --- SCHEDULER ---
scheduler = BackgroundScheduler()
scheduler.add_job(scan_tokens, "interval", seconds=10)
scheduler.add_job(manage_orders, "interval", seconds=10)
scheduler.start()

# --- MAIN ---
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("connect", connect_cmd))
    dp.add_handler(CommandHandler("address", address))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("close", close_order))
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
