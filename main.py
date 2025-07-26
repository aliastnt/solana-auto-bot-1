import os
import logging
import requests
import time
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.message import Message
from solders.rpc.responses import SendTransactionResp
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# --- Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

client = Client(SOLANA_ENDPOINT)
keypair = Keypair.from_base58_string(PRIVATE_KEY)

# --- Danh sách lệnh đang mở ---
open_orders = []
MAX_ORDERS = 3

# ================== DEXSCREENER API ==================
def get_sol_tokens():
    """Lấy tất cả token hệ Solana từ Dexscreener"""
    url = "https://api.dexscreener.com/latest/dex/tokens/solana"
    try:
        r = requests.get(url, timeout=10)
        data = r.json().get("pairs", [])
        return data
    except Exception as e:
        logger.error(f"Lỗi lấy dữ liệu Dexscreener: {e}")
        return []

def filter_tokens(tokens):
    """Lọc token đủ điều kiện"""
    selected = []
    for t in tokens:
        try:
            liquidity = float(t.get("liquidity", {}).get("usd", 0))
            price_change_5m = float(t.get("priceChange", {}).get("m5", 0))
            symbol = t["baseToken"]["symbol"]
            if liquidity >= 50000 and price_change_5m >= 5:
                selected.append((symbol, float(t["priceUsd"]), liquidity))
        except:
            continue
    return selected

# ================== GỬI LỆNH SOL (TEST) ==================
def send_sol(receiver: str, amount_sol: float):
    try:
        recent_blockhash = client.get_latest_blockhash().value.blockhash
        message = Message.new_with_blockhash(instructions=[], payer=keypair.pubkey(), blockhash=recent_blockhash)
        tx = Transaction.new_unsigned(message)
        tx.sign([keypair])
        resp: SendTransactionResp = client.send_transaction(tx)
        return f"TX Hash: {resp.value}"
    except Exception as e:
        return f"Lỗi TX: {e}"

# ================== MỞ LỆNH ==================
def open_order(token, entry_price):
    if len(open_orders) >= MAX_ORDERS:
        return
    # Chặn mở thêm nếu token đã có lệnh
    if any(o["token"] == token for o in open_orders):
        return

    # Test trước
    send_sol(str(keypair.pubkey()), 0.00005)
    time.sleep(1)  # tránh spam mạng
    send_sol(str(keypair.pubkey()), 0.02)

    # Lưu lệnh
    order = {
        "token": token,
        "entry": entry_price,
        "sl": entry_price * 0.8,
        "peak": entry_price,
        "trailing_active": False
    }
    open_orders.append(order)
    logger.info(f"Đã mở lệnh {token} tại {entry_price}$")

# ================== TRAILING STOP ==================
def check_trailing():
    global open_orders
    for order in open_orders[:]:
        # Lấy lại giá từ Dexscreener search
        try:
            url = f"https://api.dexscreener.com/latest/dex/search?q={order['token']}"
            r = requests.get(url, timeout=10)
            data = r.json().get("pairs", [])
            price = float(data[0]["priceUsd"]) if data else None
        except:
            price = None

        if not price:
            continue

        # Cập nhật đỉnh
        if price > order["peak"]:
            order["peak"] = price

        # Kích hoạt trailing khi giá >= entry * 1.15
        if not order["trailing_active"] and price >= order["entry"] * 1.15:
            order["trailing_active"] = True

        # Dời SL về entry khi lãi >= 50%
        if order["sl"] < order["entry"] and price >= order["entry"] * 1.5:
            order["sl"] = order["entry"]

        # Nếu trailing active => SL = peak - 10%
        if order["trailing_active"]:
            order["sl"] = max(order["sl"], order["peak"] * 0.9)

        # Đóng lệnh khi giá <= SL
        if price <= order["sl"]:
            logger.info(f"Đóng lệnh {order['token']} tại {price}, SL = {order['sl']}")
            open_orders.remove(order)

# ================== QUÉT TỰ ĐỘNG ==================
def auto_scan_and_trade():
    tokens = get_sol_tokens()
    filtered = filter_tokens(tokens)
    for symbol, price, liquidity in filtered:
        open_order(symbol, price)

# ================== LOG LỆNH ==================
def log_orders():
    if not open_orders:
        logger.info("Không có lệnh mở.")
    for i, order in enumerate(open_orders, 1):
        logger.info(f"Lệnh {i}: {order}")

# ================== TELEGRAM COMMANDS ==================
def start(update: Update, context: CallbackContext):
    update.message.reply_text('BOT Solana AutoTrade (Full Auto) đã khởi động!')

def get_balance(update: Update, context: CallbackContext):
    balance = client.get_balance(keypair.pubkey()).value / 1e9
    update.message.reply_text(f"Số dư SOL: {balance} SOL")

# ================== MAIN ==================
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", get_balance))

    scheduler = BackgroundScheduler(timezone=pytz.utc)
    scheduler.add_job(auto_scan_and_trade, 'interval', minutes=5)
    scheduler.add_job(check_trailing, 'interval', seconds=30)
    scheduler.add_job(log_orders, 'interval', hours=1)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
