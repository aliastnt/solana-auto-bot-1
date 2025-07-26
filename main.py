import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction

# --- Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"

# --- Solana client ---
client = Client(SOLANA_ENDPOINT)
keypair = Keypair.from_base58_string(PRIVATE_KEY)

# --- Token giả định: SOL/USDC ---
TOKEN_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")

# ================== TRADE ==================

def send_sol(receiver_pubkey: str, amount_sol: float):
    """Gửi SOL thật"""
    try:
        receiver = Pubkey.from_string(receiver_pubkey)
        lamports = int(amount_sol * 1e9)
        tx = Transaction().add(
            client.request_airdrop(receiver, lamports)["result"]
        )
        resp = client.send_transaction(tx, keypair)
        return resp.value
    except Exception as e:
        return str(e)

def open_trade(update: Update, context: CallbackContext):
    """Mở lệnh thật (gồm lệnh test + lệnh chính)"""
    update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
    test_result = send_sol(keypair.pubkey().__str__(), 0.00005)  # ~0.01 USD

    if "error" in str(test_result).lower():
        update.message.reply_text(f"Lệnh thử thất bại: {test_result}")
        return

    update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")
    main_result = send_sol(keypair.pubkey().__str__(), 0.025)  # ~5 USD
    update.message.reply_text(f"Lệnh chính đã gửi thành công.\nTX: {main_result}")

# ================== TELEGRAM COMMAND ==================

def start(update: Update, context: CallbackContext):
    update.message.reply_text("BOT Solana AutoTrade khởi động (Giao dịch thật)!")

def get_balance(update: Update, context: CallbackContext):
    balance = client.get_balance(keypair.pubkey())
    sol_balance = balance.value / 1e9
    update.message.reply_text(f"Số dư SOL: {sol_balance} SOL")

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", get_balance))
    dispatcher.add_handler(CommandHandler("open", open_trade))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
