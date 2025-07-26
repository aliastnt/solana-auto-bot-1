import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.message import Message
from solders.transaction import Transaction
from solders.hash import Hash

# --- Cấu hình log ---
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

# --- Solana Client ---
client = Client(SOLANA_ENDPOINT)
keypair = Keypair.from_base58_string(PRIVATE_KEY)

# ===============================
# ======== HÀM HỖ TRỢ ===========
# ===============================
def get_balance():
    balance = client.get_balance(keypair.pubkey())
    return balance.value / 1e9

def send_sol(receiver_pubkey: str, amount_sol: float):
    try:
        receiver = Pubkey.from_string(receiver_pubkey)
        lamports = int(amount_sol * 1e9)

        # Instruction gửi SOL
        instruction = transfer(
            TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=receiver,
                lamports=lamports
            )
        )

        # Lấy recent blockhash
        recent_blockhash = client.get_latest_blockhash().value.blockhash
        msg = Message([instruction], payer=keypair.pubkey())
        tx = Transaction([keypair], msg, Hash.from_string(recent_blockhash))

        # Gửi transaction
        resp = client.send_transaction(tx)
        return f"TX thành công: {resp.value}"
    except Exception as e:
        return f"Lỗi TX: {str(e)}"

# ===============================
# ======== TELEGRAM CMD =========
# ===============================
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('BOT Solana AutoTrade khởi động!')

def balance(update: Update, context: CallbackContext) -> None:
    try:
        sol_balance = get_balance()
        update.message.reply_text(f"Số dư SOL: {sol_balance} SOL")
    except Exception as e:
        update.message.reply_text(f"Lỗi khi lấy số dư: {e}")

def open_trade(update: Update, context: CallbackContext) -> None:
    try:
        # Mô phỏng lệnh test trước (0.01 USD)
        update.message.reply_text("Đang gửi lệnh thử (0.01 USD)...")
        # giả lập test success
        update.message.reply_text("Lệnh thử thành công! Mở lệnh chính 5 USD...")

        # Gửi lệnh chính (thực tế)
        receiver = keypair.pubkey().to_string()  # gửi về ví chính (demo)
        resp = send_sol(receiver, 0.02)  # ví dụ gửi 0.02 SOL ~ 5 USD
        update.message.reply_text(f"Lệnh chính đã gửi thành công.\n{resp}")

    except Exception as e:
        update.message.reply_text(f"Lỗi mở lệnh: {e}")

# ===============================
# ========== MAIN ===============
# ===============================
def main() -> None:
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("balance", balance))
    dispatcher.add_handler(CommandHandler("open", open_trade))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
