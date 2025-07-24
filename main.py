import os
import base58
from solders.keypair import Keypair
from solana.rpc.api import Client
from telegram import Bot

# Lấy token và chat_id từ biến môi trường Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Khởi tạo Telegram bot
bot = Bot(token=BOT_TOKEN)
bot.send_message(chat_id=CHAT_ID, text="BOT Solana AutoTrade khởi động!")

# Khởi tạo Solana client
client = Client("https://api.mainnet-beta.solana.com")

# Tạo keypair từ private key
keypair = Keypair.from_bytes(base58.b58decode(PRIVATE_KEY))

# Test gửi thông tin public key
bot.send_message(chat_id=CHAT_ID, text=f"Public key: {keypair.pubkey()}")

# Vòng lặp chạy bot
if __name__ == "__main__":
    while True:
        # Có thể thêm logic giao dịch ở đây
        pass
