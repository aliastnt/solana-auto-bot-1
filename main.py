import os
import time
import base58
from telegram import Bot
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solana.rpc.api import Client

# ====== Load ENV ======
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CHAT_ID = int(os.getenv("CHAT_ID"))
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ====== Init Telegram Bot ======
bot = Bot(token=BOT_TOKEN)

# ====== Init Solana Client ======
client = Client("https://api.mainnet-beta.solana.com")

# ====== Load Keypair ======
if not PRIVATE_KEY:
    raise Exception("PRIVATE_KEY is not set")
keypair = Keypair.from_bytes(base58.b58decode(PRIVATE_KEY))

# ====== Send Telegram Notify ======
def send_message(msg):
    bot.send_message(chat_id=CHAT_ID, text=msg)

send_message("BOT Solana AutoTrade khởi động!")

# ====== Simple Trade Loop (demo) ======
while True:
    try:
        # Lấy balance
        balance = client.get_balance(keypair.pubkey())
        send_message(f"Số dư hiện tại: {balance['result']['value']} lamports")
    except Exception as e:
        send_message(f"Lỗi: {str(e)}")
    time.sleep(60)
