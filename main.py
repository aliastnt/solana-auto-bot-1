import os
import time
import requests
import base58
from solana.rpc.api import Client
from solana.keypair import Keypair
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from telegram import Bot

# --- ENVIRONMENT VARIABLES ---
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # dạng base58
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# --- CONFIG ---
RPC_URL = "https://api.mainnet-beta.solana.com"
TRAILING_STOP_PERCENT = 0.02
SL_PERCENT = 0.02
MAX_TRADES_PER_HOUR = 3
MIN_LIQUIDITY = 50000  # USD
TRADE_AMOUNT_USD = 10

# --- TELEGRAM BOT ---
bot = Bot(token=TELEGRAM_TOKEN)

def send_telegram(msg):
    try:
        bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print("Telegram error:", e)

# --- CONNECT WALLET ---
def load_wallet(private_key_b58):
    pk_bytes = base58.b58decode(private_key_b58)
    return Keypair.from_secret_key(pk_bytes)

# --- CHECK TOKEN LIQUIDITY (dummy) ---
def check_liquidity(token_address):
    # TODO: thay bằng API DexScreener hoặc Jupiter để check thanh khoản thực
    return 100000  # giả định > 50k USD để test

# --- PLACE ORDER (dummy swap) ---
def place_order(client, keypair, token, amount):
    # TODO: tích hợp Serum/Jupiter Swap
    send_telegram(f"Mở lệnh mua {token} với {amount}$")
    return {"entry": 100, "tp": None, "sl": None}  # mock

# --- TRAILING STOP LOGIC ---
def trailing_stop_logic(entry_price, current_price, highest_price):
    highest_price = max(highest_price, current_price)
    if current_price < highest_price * (1 - TRAILING_STOP_PERCENT):
        return True, highest_price
    return False, highest_price

def main():
    send_telegram("BOT Solana AutoTrade khởi động!")
    client = Client(RPC_URL)
    wallet = load_wallet(PRIVATE_KEY)

    trades_this_hour = 0
    highest_price = 0
    entry_price = None

    while True:
        if trades_this_hour < MAX_TRADES_PER_HOUR:
            token = "SOL"  # có thể thay bằng token mới
            liquidity = check_liquidity(token)
            if liquidity >= MIN_LIQUIDITY:
                order = place_order(client, wallet, token, TRADE_AMOUNT_USD)
                entry_price = order["entry"]
                highest_price = entry_price
                trades_this_hour += 1

        # giả lập giá thay đổi
        current_price = entry_price * 1.03  # giả sử tăng 3%
        exit_signal, highest_price = trailing_stop_logic(entry_price, current_price, highest_price)
        if exit_signal:
            send_telegram(f"Đóng lệnh {token} tại {current_price}, entry {entry_price}")
            entry_price = None

        # reset theo giờ
        if time.localtime().tm_min == 0:
            trades_this_hour = 0

        time.sleep(30)

if __name__ == "__main__":
    main()
