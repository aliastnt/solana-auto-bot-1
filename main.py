import os
import base58
from solana.keypair import Keypair
from solana.rpc.api import Client
from telegram import Bot

# --- Load biến môi trường ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN chưa được thiết lập")
if not CHAT_ID:
    raise ValueError("CHAT_ID chưa được thiết lập")
if not PRIVATE_KEY:
    raise ValueError("PRIVATE_KEY chưa được thiết lập")

# --- Tạo keypair từ private key (base58) ---
keypair = Keypair.from_secret_key(base58.b58decode(PRIVATE_KEY))

# --- Kết nối Solana (không proxy) ---
client = Client("https://api.mainnet-beta.solana.com")

# --- Telegram bot ---
bot = Bot(token=BOT_TOKEN)

def main():
    # Thông báo khởi động
    bot.send_message(chat_id=CHAT_ID, text="BOT Solana AutoTrade khởi động thành công!")

    # Lấy số dư ví
    balance = client.get_balance(keypair.public_key)
    lamports = balance['result']['value']
    sol_balance = lamports / 1_000_000_000
    bot.send_message(chat_id=CHAT_ID, text=f"Số dư ví: {sol_balance} SOL")

if __name__ == "__main__":
    main()
