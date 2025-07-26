import os
import time
from apscheduler.schedulers.background import BackgroundScheduler
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from solders.keypair import Keypair
from telegram import Bot
from telegram.ext import Updater, CommandHandler

# ========== ENVIRONMENT VARIABLES ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")

# ========== TELEGRAM BOT ==========
bot = Bot(token=BOT_TOKEN)

def send_telegram_message(message):
    try:
        bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        print(f"Telegram send error: {e}")

# ========== SOLANA CLIENT ==========
client = Client(SOLANA_ENDPOINT)
keypair = Keypair.from_base58_string(PRIVATE_KEY)

# ========== TRADE LOGIC ==========
def open_trade():
    try:
        tx = Transaction().add(
            transfer(
                TransferParams(
                    from_pubkey=keypair.pubkey(),
                    to_pubkey=keypair.pubkey(),
                    lamports=100000  # 0.0001 SOL
                )
            )
        )
        response = client.send_transaction(tx, keypair)
        send_telegram_message(f"Lệnh mở thành công. TX: {response}")
    except Exception as e:
        send_telegram_message(f"Lỗi mở lệnh: {e}")
        print(f"Lỗi mở lệnh: {e}")

def check_trailing():
    print("Check trailing stop...")

# ========== TELEGRAM COMMANDS ==========
def start_command(update, context):
    update.message.reply_text("BOT Solana AutoTrade (Full Auto) đã khởi động!")
    send_telegram_message("BOT Solana AutoTrade (Full Auto) đã khởi động!")

def open_command(update, context):
    update.message.reply_text("Đang gửi lệnh...")
    open_trade()

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("open", open_command))
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_trailing, "interval", seconds=30)
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    send_telegram_message("BOT Solana AutoTrade khởi động!")
    main()
