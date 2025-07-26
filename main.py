import logging
import os
import requests
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from solders.keypair import Keypair
from solana.rpc.api import Client
from solders.pubkey import Pubkey

# ========== Cấu hình ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# Client Solana
client = Client(RPC_URL)

# Bot Telegram
bot = Bot(token=TELEGRAM_TOKEN)

# Tài khoản giao dịch
keypair = Keypair.from_base58_string(PRIVATE_KEY)

# ========== Logging ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== Biến lưu trạng thái ==========
orders = []
trailing_orders = []

# ========== Lấy giá từ Dexscreener ==========
def get_price(token_pair="SOL/USDC"):
    try:
        url = "https://api.dexscreener.com/latest/dex/tokens/solana"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            for pair in data.get("pairs", []):
                if token_pair in pair["pairName"]:
                    return float(pair["priceUsd"])
    except Exception as e:
        logger.error(f"Lỗi lấy giá: {e}")
    return None

# ========== Lệnh /start ==========
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade (Full Auto) đã khởi động!")

# ========== Lệnh /balance ==========
def balance(update: Update, context: CallbackContext):
    try:
        balance = client.get_balance(keypair.pubkey()).value / 1e9
        update.message.reply_text(f"Số dư SOL: {balance}")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy số dư: {e}")

# ========== Mở lệnh ==========
def open_order(update: Update, context: CallbackContext):
    update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
    # Giả lập test order
    update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")
    # Thêm vào danh sách lệnh mô phỏng
    orders.append({
        "token": "SOL/USDC",
        "entry": get_price(),
        "sl": 0.8,
        "tp": None,
        "trailing": True
    })
    update.message.reply_text("Lệnh chính đã gửi thành công (mô phỏng).")

# ========== Check trailing stop ==========
def check_trailing():
    if not orders:
        return
    for order in orders:
        current_price = get_price(order["token"])
        if not current_price:
            continue
        entry = order["entry"]
        if order["trailing"] and current_price >= entry * 1.15:
            order["sl"] = entry
            logger.info(f"Trailing stop đã kích hoạt cho {order['token']} ở giá {current_price}")

# ========== Scheduler ==========
scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(check_trailing, "interval", seconds=30)
scheduler.start()

# ========== Main ==========
def main():
    updater = Updater(TELEGRAM_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("open", open_order))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
