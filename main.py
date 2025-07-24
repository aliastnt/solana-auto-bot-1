import os
import base58
from solana.keypair import Keypair
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.publickey import PublicKey
from telegram import Bot

# Lấy biến môi trường và kiểm tra
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("PRIVATE_KEY chưa được thiết lập trong biến môi trường")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN chưa được thiết lập trong biến môi trường")

CHAT_ID = os.getenv("CHAT_ID")
if CHAT_ID is None:
    raise ValueError("CHAT_ID chưa được thiết lập trong biến môi trường")
CHAT_ID = int(CHAT_ID)

# Khởi tạo bot Telegram
bot = Bot(token=TELEGRAM_TOKEN)

# Tạo keypair từ private key base58
try:
    secret_key = base58.b58decode(PRIVATE_KEY)
    keypair = Keypair.from_secret_key(secret_key)
except Exception as e:
    raise ValueError(f"Lỗi khi giải mã PRIVATE_KEY: {e}")

# Kết nối Solana RPC
client = Client("https://api.mainnet-beta.solana.com")

def notify_telegram(message):
    bot.send_message(chat_id=CHAT_ID, text=message)

def main():
    # Ví dụ gửi thông báo
    notify_telegram("BOT Solana AutoTrade khởi động!")

    # Ví dụ kiểm tra số dư
    balance = client.get_balance(keypair.public_key)
    notify_telegram(f"Số dư tài khoản: {balance['result']['value']/1e9} SOL")

if __name__ == "__main__":
    main()
