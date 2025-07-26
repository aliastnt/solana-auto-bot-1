import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# --- LOGGING ---
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
pubkey = keypair.pubkey()

# --- LỆNH ĐANG MỞ ---
open_orders = []   # Lưu thông tin lệnh đang mở

# --- TRAILING STOP CẤU HÌNH ---
MAX_OPEN_ORDERS = 3
STOP_LOSS_PCT = 0.2   # 20%
TRAIL_ACTIVATE_PCT = 0.15  # kích hoạt trailing khi giá +15%
TRAIL_OFFSET_PCT = 0.1     # trailing stop cách đỉnh 10%
ENTRY_TP_MOVE_PCT = 0.5    # khi lợi nhuận 50% thì dời SL về entry

def get_balance(update: Update, context: CallbackContext) -> None:
    balance_resp = client.get_balance(pubkey)
    balance = balance_resp.value / 1e9
    update.message.reply_text(f"Số dư SOL: {balance:.8f} SOL")

def open_trade(update: Update, context: CallbackContext) -> None:
    if len(open_orders) >= MAX_OPEN_ORDERS:
        update.message.reply_text("Đã đủ 3 lệnh mở, không thể mở thêm!")
        return

    update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
    # Mô phỏng gửi lệnh thử
    test_success = True

    if test_success:
        update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")
        entry_price = 1.0  # giả định entry = 1.0 USD (mô phỏng)
        order = {
            "entry": entry_price,
            "stop_loss": entry_price * (1 - STOP_LOSS_PCT),
            "take_profit_trailing": None,
            "activated_trail": False
        }
        open_orders.append(order)
        update.message.reply_text("Lệnh chính đã gửi thành công (mô phỏng).")
    else:
        update.message.reply_text("Lỗi khi mở lệnh thử, không thực hiện lệnh chính!")

def check_orders():
    # Mô phỏng giá thị trường
    market_price = 1.2  # giả định tăng giá

    for order in open_orders:
        # Kích hoạt trailing khi giá tăng 15%
        if not order["activated_trail"] and market_price >= order["entry"] * (1 + TRAIL_ACTIVATE_PCT):
            order["activated_trail"] = True
            order["take_profit_trailing"] = market_price * (1 - TRAIL_OFFSET_PCT)

        # Dời SL về entry khi lời 50%
        if market_price >= order["entry"] * (1 + ENTRY_TP_MOVE_PCT):
            order["stop_loss"] = order["entry"]

        # Cập nhật trailing stop
        if order["activated_trail"]:
            new_trail = market_price * (1 - TRAIL_OFFSET_PCT)
            if new_trail > order["take_profit_trailing"]:
                order["take_profit_trailing"] = new_trail

        # Kiểm tra chạm SL
        if market_price <= order["stop_loss"]:
            logger.info(f"Đóng lệnh tại giá {market_price} (chạm SL)")
            open_orders.remove(order)

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text("BOT Solana AutoTrade khởi động!")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", get_balance))
    dp.add_handler(CommandHandler("open", open_trade))

    # --- Scheduler ---
    scheduler = BackgroundScheduler(timezone=pytz.UTC)
    scheduler.add_job(check_orders, "interval", seconds=5)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
