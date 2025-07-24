import os
import time
import requests
import json
from datetime import datetime, timedelta
from telegram import Bot
from solana.rpc.api import Client
from solana.keypair import Keypair

# === Environment variables ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PRIVATE_KEY = json.loads(os.getenv("PRIVATE_KEY"))
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", None)  # optional nếu muốn hiển thị ví

# === RPC & API ===
RPC_URL = "https://api.mainnet-beta.solana.com"
DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/solana"
RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/"

bot = Bot(token=TELEGRAM_TOKEN)
solana_client = Client(RPC_URL)
keypair = Keypair.from_secret_key(bytes(PRIVATE_KEY))
wallet_pubkey = keypair.public_key

# === Trading config ===
TOTAL_BALANCE = 10.0        # Tổng vốn test
TRADE_RISK = 0.02           # 2% rủi ro/tài khoản
MAX_TRADES = 3              # số lệnh tối đa cùng lúc
TRADE_SIZE = TOTAL_BALANCE * 0.2   # vốn mỗi lệnh = 20% tài khoản (~2 USD)
TIME_LIMIT = 60 * 60        # 1 giờ

# === Quản lý lệnh ===
trades = {}  # {token_address: {"entry":..., "amount":..., "highest":..., "start_time":...}}

# === Kiểm tra rugpull ===
def check_rug(token_address):
    try:
        r = requests.get(RUGCHECK_URL + token_address)
        data = r.json()
        # liquidity
        liquidity = data.get("liquidity", 0)
        lock = data.get("liquidity_locked", False)
        top_holder = data.get("top_holder", 0)
        mint_auth = data.get("mint_revoked", False)

        if liquidity < 50000:  # <50k usd
            return False
        if not lock:
            return False
        if top_holder > 50:
            return False
        if not mint_auth:
            return False
        return True
    except Exception as e:
        print("RugCheck error:", e)
        return False

# === Quét token tăng mạnh ===
def get_top_token():
    try:
        r = requests.get(DEXSCREENER_URL)
        data = r.json()
        token_list = sorted(data["pairs"], key=lambda x: x["priceChange"]["m5"], reverse=True)
        for token in token_list:
            change = token["priceChange"]["m5"]
            address = token["baseToken"]["address"]
            if change > 5 and address not in trades:
                # lọc rug
                if check_rug(address):
                    return token
        return None
    except Exception as e:
        print("DexScreener error:", e)
        return None

# === Vào lệnh ===
def open_trade(token):
    price = float(token["priceUsd"])
    amount = TRADE_SIZE / price
    trades[token["baseToken"]["address"]] = {
        "entry": price,
        "amount": amount,
        "highest": price,
        "start_time": datetime.utcnow()
    }
    bot.send_message(chat_id="@your_channel_or_userid",
                     text=f"Mở lệnh: {token['baseToken']['symbol']} @ {price:.6f}\nVốn: {TRADE_SIZE} USD")

# === Đóng lệnh ===
def close_trade(token_address, price, reason):
    trade = trades.pop(token_address, None)
    if not trade:
        return
    entry = trade["entry"]
    pnl = (price - entry) / entry * 100
    bot.send_message(chat_id="@your_channel_or_userid",
                     text=f"Đóng lệnh: {token_address}\nGiá đóng: {price:.6f}\nP/L: {pnl:.2f}%\nLý do: {reason}")

# === Cập nhật trailing stop ===
def update_trades():
    now = datetime.utcnow()
    to_close = []
    for token_address, trade in trades.items():
        # lấy giá hiện tại
        price = get_token_price(token_address)
        if price is None:
            continue

        entry = trade["entry"]
        highest = trade["highest"]
        if price > highest:
            highest = price
            trade["highest"] = highest

        # Stop loss theo vốn tài khoản
        sl_price = entry - (TRADE_RISK * TOTAL_BALANCE) / trade["amount"]

        # Break-even
        if price >= entry * 1.02:
            sl_price = entry

        # trailing stop động
        gain_pct = (highest - entry) / entry * 100
        if gain_pct < 10:
            trail_pct = 5
        elif gain_pct < 20:
            trail_pct = 8
        else:
            trail_pct = 12

        trailing_stop = highest * (1 - trail_pct / 100)

        # Chọn mức SL cuối
        final_sl = max(sl_price, trailing_stop)

        # Điều kiện đóng
        if price <= final_sl:
            to_close.append((token_address, price, "Stop/Trailing"))
        elif (now - trade["start_time"]).total_seconds() > TIME_LIMIT:
            # time limit
            if price > entry:
                to_close.append((token_address, price, "Hòa vốn do hết giờ"))
            else:
                to_close.append((token_address, price, "Cắt lỗ do hết giờ"))

    # đóng lệnh
    for t in to_close:
        close_trade(*t)

# === Lấy giá token ===
def get_token_price(token_address):
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{token_address}"
        r = requests.get(url)
        data = r.json()
        return float(data["pairs"][0]["priceUsd"])
    except:
        return None

# === Vòng lặp chính ===
def run_bot():
    bot.send_message(chat_id="@your_channel_or_userid", text="BOT Solana AutoTrade khởi động!")
    while True:
        # mở lệnh mới nếu còn slot
        if len(trades) < MAX_TRADES:
            token = get_top_token()
            if token:
                open_trade(token)
        # cập nhật trailing stop và đóng lệnh khi cần
        update_trades()
        time.sleep(30)

if __name__ == "__main__":
    run_bot()
