import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import time

# --- Cấu hình log ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Lấy ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

# --- Solana Client ---
client = Client(SOLANA_ENDPOINT)

# --- Danh sách lệnh mô phỏng ---
positions = []

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('BOT Solana AutoTrade khởi động!')

def get_balance(update: Update, context: CallbackContext) -> None:
    try:
        pubkey = Keypair.from_base58_string(PRIVATE_KEY).pubkey()
        balance = client.get_balance(pubkey)
        sol_balance = balance.value / 1e9
        update.message.reply_text(f"Số dư SOL: {sol_balance} SOL")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy số dư: {e}")

def open_trade(update: Update, context: CallbackContext) -> None:
    # Mô phỏng lệnh thử
    context.bot.send_message(chat_id=CHAT_ID, text="Đang gửi lệnh thử (0.01 USD)...")
    time.sleep(1)
    context.bot.send_message(chat_id=CHAT_ID, text="Lệnh thử thành công! Mở lệnh chính 5 USD...")
    time.sleep(1)
    positions.append({
        "token": "SOL/USDC",
        "entry": 180.0,
        "current_price": 182.5,
        "sl": 180.0,
        "trailing_active": True
    })
    context.bot.send_message(chat_id=CHAT_ID, text="Lệnh chính đã gửi thành công (mô phỏng).")

def log_positions():
    if not positions:
        return
    msg = "📊 **Trạng thái lệnh:**\n"
    for idx, pos in enumerate(positions, start=1):
        msg += (f"\n[Lệnh {idx}]\n"
                f"Token: {pos['token']}\n"
                f"Entry: {pos['entry']}\n"
                f"Giá hiện tại: {pos['current_price']}\n"
                f"SL: {pos['sl']}\n"
                f"Trailing: {'Kích hoạt' if pos['trailing_active'] else 'Chưa kích hoạt'}\n")
    updater.bot.send_message(chat_id=CHAT_ID, text=msg)

def main() -> None:
    global updater
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", get_balance))
    dispatcher.add_handler(CommandHandler("open", open_trade))

    # --- Scheduler log định kỳ ---
    scheduler = BackgroundScheduler(timezone=pytz.utc)
    scheduler.add_job(log_positions, 'interval', seconds=5)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
