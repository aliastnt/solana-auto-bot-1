import os
import time
import requests
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.keypair import Keypair
from solana.rpc.types import TxOpts
from solana.system_program import TransferParams, transfer
from base58 import b58decode
from telegram import Bot
from telegram.ext import Updater, CommandHandler

# ==== ENVIRONMENT VARIABLES ====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# ==== INITIALIZE ====
bot = Bot(token=TELEGRAM_TOKEN)
client = Client(RPC_URL)
keypair = Keypair.from_secret_key(b58decode(PRIVATE_KEY))

# ==== CONFIG ====
TEST_AMOUNT = 0.0001  # ~0.01 USD
TRADE_AMOUNT = 0.05   # ~5 USD
STOP_LOSS_PCT = 0.20  # 20%
TRAILING_ACTIVATION = 0.15
TRAILING_STOP = 0.10

open_positions = []

# ==== TELEGRAM HANDLERS ====
def start(update, context):
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="BOT Solana AutoTrade (Full Auto) đã khởi động!")

def balance(update, context):
    balance = client.get_balance(keypair.public_key)["result"]["value"] / 10**9
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"Số dư SOL: {balance} SOL")

def send_message(msg):
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

# ==== DEX PRICING ====
def get_sol_price():
    try:
        resp = requests.get("https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112")
        data = resp.json()
        return float(data['pairs'][0]['priceUsd'])
    except:
        return None

# ==== SEND SOL TRANSACTION ====
def send_sol_transaction(to_pubkey, amount_sol):
    try:
        tx = Transaction().add(
            transfer(TransferParams(
                from_pubkey=keypair.public_key,
                to_pubkey=to_pubkey,
                lamports=int(amount_sol * 10**9)
            ))
        )
        resp = client.send_transaction(tx, keypair, opts=TxOpts(skip_preflight=True))
        return resp
    except Exception as e:
        send_message(f"Lỗi mở lệnh: {e}")
        return None

# ==== OPEN ORDER LOGIC ====
def open_trade():
    # 1. Gửi lệnh test
    send_message(f"Đang gửi lệnh thử ({TEST_AMOUNT} SOL)...")
    test_tx = send_sol_transaction(keypair.public_key, TEST_AMOUNT)
    if test_tx is None:
        send_message("Lệnh thử thất bại.")
        return

    send_message(f"Lệnh thử thành công! Mở lệnh chính {TRADE_AMOUNT} SOL...")
    main_tx = send_sol_transaction(keypair.public_key, TRADE_AMOUNT)
    if main_tx:
        open_positions.append({
            "entry_price": get_sol_price(),
            "stop_loss": None,
            "trailing_active": False
        })
        send_message("Lệnh chính đã gửi thành công.")
    else:
        send_message("Lỗi mở lệnh chính.")

# ==== TRAILING STOP ====
def check_trailing():
    for pos in open_positions:
        current_price = get_sol_price()
        if current_price is None: 
            continue

        if not pos["trailing_active"] and current_price >= pos["entry_price"] * (1 + TRAILING_ACTIVATION):
            pos["stop_loss"] = pos["entry_price"]
            pos["trailing_active"] = True
            send_message(f"Trailing Stop kích hoạt @ {current_price}")

        if pos["trailing_active"]:
            new_sl = current_price * (1 - TRAILING_STOP)
            if new_sl > pos["stop_loss"]:
                pos["stop_loss"] = new_sl
                send_message(f"Stop Loss nâng lên: {pos['stop_loss']}")

# ==== MAIN ====
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("open", lambda u, c: open_trade()))

    updater.start_polling()
    send_message("Bot AutoTrade chạy chế độ FULL AUTO!")

    while True:
        check_trailing()
        time.sleep(60)

if __name__ == "__main__":
    main()
