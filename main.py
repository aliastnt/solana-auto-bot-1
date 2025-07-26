import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import pytz

# --- Logging ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

client = Client(SOLANA_ENDPOINT)

# --- Variables ---
open_positions = []
MAX_OPEN_POSITIONS = 3

# --- Balance ---
def get_balance(update: Update, context: CallbackContext):
    try:
        pubkey = Keypair.from_base58_string(PRIVATE_KEY).pubkey()
        balance_response = client.get_balance(pubkey)
        balance_sol = balance_response.value / 1e9
        update.message.reply_text(f"Số dư SOL: {balance_sol} SOL")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy số dư: {e}")

# --- Fake Order Execution (simulate success) ---
def send_test_order(amount_usd):
    return True  # Giả lập thành công

# --- Logic mở lệnh ---
def open_trade(update: Update, context: CallbackContext):
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        update.message.reply_text("Đã đủ 3 lệnh mở, không mở thêm.")
        return

    update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
    if send_test_order(0.01):
        update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")

        # Giả lập mở lệnh
        entry_price = 1.0   # giả định giá
        position = {
            "entry_price": entry_price,
            "amount": 5,
            "stop_loss": entry_price * 0.8,
            "take_profit_trigger": entry_price * 1.15,
            "trailing_active": False,
            "trailing_stop": None,
            "peak_price": entry_price
        }
        open_positions.append(position)

        update.message.reply_text("Lệnh chính đã gửi thành công (mô phỏng).")

# --- Logic quản lý lệnh ---
def manage_positions():
    if not open_positions:
        logger.info("Không có lệnh mở để quản lý.")
        return

    for pos in open_positions[:]:
        # giả lập giá thị trường (tăng dần để test)
        current_price = pos["peak_price"] * 1.02
        pos["peak_price"] = max(pos["peak_price"], current_price)

        # dời SL khi đạt 50% lợi nhuận
        if current_price >= pos["entry_price"] * 1.5 and not pos["trailing_active"]:
            pos["stop_loss"] = pos["entry_price"]
            pos["trailing_active"] = True
            logger.info(f"Dời SL về entry {pos['entry_price']} vì đạt 50% lợi nhuận.")

        # kích hoạt trailing stop
        if pos["trailing_active"]:
            if pos["trailing_stop"] is None or current_price > pos["trailing_stop"] / 0.9:
                pos["trailing_stop"] = current_price * 0.9

            # nếu giá giảm hơn 10% từ đỉnh → thoát lệnh
            if current_price < pos["trailing_stop"]:
                open_positions.remove(pos)
                logger.info(f"Thoát lệnh tại {current_price} (trailing stop kích hoạt).")

        # --- log chi tiết mỗi lần quét ---
        logger.info(f"[LỆNH] Entry: {pos['entry_price']} | Giá hiện tại: {current_price} "
                    f"| SL: {pos['stop_loss']} | Trailing: {pos['trailing_stop']} "
                    f"| Trạng thái trailing: {pos['trailing_active']}")

# --- Scheduler ---
scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(manage_positions, "interval", seconds=5)
scheduler.start()

# --- Commands ---
def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade khởi động (mô phỏng lệnh thử + mở lệnh chính)!")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", get_balance))
    dp.add_handler(CommandHandler("open", open_trade))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
