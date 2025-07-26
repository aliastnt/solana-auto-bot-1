import os
import logging
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

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
scheduler = BackgroundScheduler(timezone=pytz.utc)

# --- Biến lưu trạng thái lệnh ---
orders = []
TRAILING_PERCENT = 0.10   # trailing stop 10%
TRIGGER_TRAILING = 0.15   # kích hoạt trailing khi giá >= 15% so với entry

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('BOT Solana AutoTrade khởi động!')

def get_balance(update: Update, context: CallbackContext) -> None:
    pubkey = Keypair.from_base58_string(PRIVATE_KEY).pubkey()
    balance = client.get_balance(pubkey)
    sol_balance = balance.value / 1e9
    update.message.reply_text(f"Số dư SOL: {sol_balance} SOL")

def open_order(update: Update, context: CallbackContext) -> None:
    entry = 180.0  # giả định entry
    sl = entry * 0.8  # SL ban đầu -20%
    orders.append({
        "token": "SOL/USDC",
        "entry": entry,
        "current_price": entry,
        "sl": sl,
        "trailing_active": False,
        "peak_price": entry
    })
    update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
    update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")
    update.message.reply_text("Lệnh chính đã gửi thành công (mô phỏng).")

def check_orders():
    """Hàm kiểm tra lệnh và xử lý trailing stop"""
    for order in orders:
        price = order["current_price"] + 2.5  # mô phỏng giá tăng
        order["current_price"] = price

        # Kích hoạt trailing stop
        if not order["trailing_active"] and price >= order["entry"] * (1 + TRIGGER_TRAILING):
            order["trailing_active"] = True
            logger.info(f"Trailing stop được kích hoạt cho lệnh {order['token']}.")

        # Khi đã kích hoạt trailing stop
        if order["trailing_active"]:
            order["peak_price"] = max(order["peak_price"], price)
            # Dời SL về Entry khi lãi >= 50%
            if price >= order["entry"] * 1.5 and order["sl"] < order["entry"]:
                order["sl"] = order["entry"]
            # Cập nhật SL theo trailing (10% từ đỉnh)
            trailing_sl = order["peak_price"] * (1 - TRAILING_PERCENT)
            if trailing_sl > order["sl"]:
                order["sl"] = trailing_sl

        logger.info(f"Trạng thái lệnh {order['token']}: Entry={order['entry']}, "
                    f"Giá hiện tại={price}, SL={order['sl']}, "
                    f"Trailing={'Kích hoạt' if order['trailing_active'] else 'Chưa kích hoạt'}")

def send_order_status(context: CallbackContext):
    """Gửi trạng thái lệnh về Telegram"""
    if not orders:
        context.bot.send_message(chat_id=CHAT_ID, text="Không có lệnh nào đang mở.")
        return
    msg = "📊 **Trạng thái lệnh:**\n"
    for i, order in enumerate(orders, 1):
        msg += (f"\n[Lệnh {i}]\n"
                f"Token: {order['token']}\n"
                f"Entry: {order['entry']}\n"
                f"Giá hiện tại: {order['current_price']}\n"
                f"SL: {order['sl']}\n"
                f"Trailing: {'Kích hoạt' if order['trailing_active'] else 'Chưa kích hoạt'}\n")
    context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", get_balance))
    dispatcher.add_handler(CommandHandler("open", open_order))

    # Chạy scheduler: kiểm tra lệnh mỗi 5 giây
    scheduler.add_job(check_orders, "interval", seconds=5)
    # Gửi log trạng thái lệnh mỗi 1 giờ
    scheduler.add_job(send_order_status, "interval", hours=1, args=[updater.bot])
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
