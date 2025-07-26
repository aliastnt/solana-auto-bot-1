import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import utc

# --- Cấu hình log ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"
client = Client(SOLANA_ENDPOINT)

# --- Lệnh ---
open_orders = []
MAX_OPEN_ORDERS = 3

# --- Scheduler ---
scheduler = BackgroundScheduler(timezone=utc)

# --- Giá từ Dexscreener ---
def get_price_from_dex(pair_address="9wFFyRfZfM8GQYjvn9FU6pL6Xr9hEkGm9iUZx2XxZp6h"):  # SOL/USDC mặc định
    url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}"
    try:
        response = requests.get(url)
        price = float(response.json()['pairs'][0]['priceUsd'])
        return price
    except Exception as e:
        logger.error(f"Lỗi lấy giá Dexscreener: {e}")
        return None

# --- Trailing Stop ---
def update_trailing(order):
    current_price = get_price_from_dex()
    if current_price is None:
        return

    # Nếu giá tăng > 15% => kích hoạt trailing stop
    if current_price >= order['entry'] * 1.15 and not order['trailing']:
        order['trailing'] = True
        order['sl'] = order['entry']
        send_msg("Giá đã vượt 15%, kích hoạt trailing stop, SL = entry.")

    # Nếu trailing đang kích hoạt, cập nhật SL = đỉnh - 10%
    if order['trailing']:
        if current_price > order['peak']:
            order['peak'] = current_price
            order['sl'] = current_price * 0.9
            send_msg(f"Cập nhật trailing stop: Peak = {order['peak']}, SL mới = {order['sl']}")

# --- Gửi tin nhắn Telegram ---
def send_msg(text):
    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                 params={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})

# --- Lệnh Telegram ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade (Dexscreener + Trailing Stop) đã khởi động!")

def get_balance(update: Update, context: CallbackContext):
    keypair = Keypair.from_base58_string(PRIVATE_KEY)
    balance = client.get_balance(keypair.pubkey()).value / 1e9
    update.message.reply_text(f"Số dư SOL: {balance} SOL")

def open_order(update: Update, context: CallbackContext):
    if len(open_orders) >= MAX_OPEN_ORDERS:
        update.message.reply_text("Đã đạt giới hạn 3 lệnh mở đồng thời.")
        return

    update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
    update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")

    entry = get_price_from_dex()
    order = {
        "token": "SOL/USDC",
        "entry": entry,
        "sl": entry * 0.8,  # SL ban đầu 20%
        "peak": entry,
        "trailing": False
    }
    open_orders.append(order)
    update.message.reply_text("Lệnh chính đã gửi thành công (mô phỏng).")

# --- Log lệnh 1h/lần ---
def log_orders():
    if not open_orders:
        return
    msg = "📊 **Trạng thái lệnh:**\n"
    for i, order in enumerate(open_orders, 1):
        price = get_price_from_dex()
        msg += f"\n[Lệnh {i}]\nToken: {order['token']}\nEntry: {order['entry']}\n"
        msg += f"Giá hiện tại: {price}\nSL: {order['sl']}\nTrailing: {'Kích hoạt' if order['trailing'] else 'Chưa'}\n"
        update_trailing(order)
    send_msg(msg)

# --- Main ---
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", get_balance))
    dp.add_handler(CommandHandler("open", open_order))

    scheduler.add_job(log_orders, 'interval', hours=1)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
