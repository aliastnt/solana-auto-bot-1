import logging
import os
import requests
import time
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram import Update
from apscheduler.schedulers.background import BackgroundScheduler

# Solana
from solana.rpc.api import Client
from solana.keypair import Keypair
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from solders.pubkey import Pubkey
from solders.keypair import Keypair as SoldersKeypair
from solana.rpc.commitment import Confirmed

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= CONFIG =========
DEX_API = "https://api.dexscreener.io/latest/dex/tokens/solana"
LAMPORTS_PER_SOL = 1_000_000_000
SOLANA_RPC = "https://api.mainnet-beta.solana.com"  # đổi sang devnet nếu cần
client = Client(SOLANA_RPC)

# đọc private key từ biến môi trường (định dạng base58)
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # nhập base58 key của ví Phantom
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ========== WALLET ==========
def load_wallet(base58_key: str):
    kp = SoldersKeypair.from_base58_string(base58_key)
    return kp

wallet = load_wallet(PRIVATE_KEY)
public_key = wallet.pubkey()

# ========= TELEGRAM BOT =========
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

open_positions = {}
scheduler = BackgroundScheduler()
scheduler.start()

# ========= DEXSCREENER SCAN =========
def scan_tokens():
    try:
        resp = requests.get(DEX_API)
        data = resp.json()
        hot_tokens = []
        for pair in data.get('pairs', []):
            vol = pair.get('volume', {}).get('h24', 0)
            change5m = float(pair.get('priceChange', {}).get('m5', 0))
            if vol >= 50000 and abs(change5m) >= 2:
                hot_tokens.append({
                    "pairAddress": pair["pairAddress"],
                    "baseToken": pair["baseToken"]["symbol"],
                    "priceUsd": pair["priceUsd"]
                })
        return hot_tokens
    except Exception as e:
        logger.error(f"Lỗi quét DexScreener: {e}")
        return []

# ========= SOLANA TRANSFER =========
def send_sol(destination: str, amount_sol: float):
    try:
        to_pubkey = Pubkey.from_string(destination)
        lamports = int(amount_sol * LAMPORTS_PER_SOL)
        tx = Transaction().add(
            transfer(TransferParams(from_pubkey=public_key, to_pubkey=to_pubkey, lamports=lamports))
        )
        result = client.send_transaction(tx, wallet, opts={"skip_preflight": False}, recent_blockhash=None)
        return result
    except Exception as e:
        logger.error(f"Lỗi gửi SOL: {e}")
        return None

# ========= LOGIC MUA =========
def test_and_buy(destination: str):
    logger.info("Thực hiện test mua 0.01 SOL...")
    test_result = send_sol(destination, 0.01)
    if test_result:
        logger.info("Test mua thành công, tiến hành lệnh chính...")
        balance_resp = client.get_balance(public_key, commitment=Confirmed)
        balance = balance_resp["result"]["value"] / LAMPORTS_PER_SOL
        amount_main = balance * 0.30
        main_result = send_sol(destination, amount_main)
        return main_result
    else:
        logger.warning("Test mua thất bại, hủy lệnh chính.")
        return None

# ========= TELEGRAM COMMAND =========
def start(update: Update, context: CallbackContext):
    update.message.reply_text("Bot Solana Trade đã sẵn sàng.\nLệnh: /scan để quét token hot.")

def scan(update: Update, context: CallbackContext):
    tokens = scan_tokens()
    if not tokens:
        update.message.reply_text("Không tìm thấy token đủ điều kiện.")
    else:
        msg = "Token tiềm năng:\n"
        for t in tokens:
            msg += f"{t['baseToken']} - {t['priceUsd']} USD\n"
        update.message.reply_text(msg)

def send(update: Update, context: CallbackContext):
    try:
        dest = context.args[0]
        res = test_and_buy(dest)
        update.message.reply_text(f"Gửi SOL kết quả: {res}")
    except Exception as e:
        update.message.reply_text(f"Lỗi: {e}")

# ========= ĐĂNG KÝ LỆNH =========
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("scan", scan))
dispatcher.add_handler(CommandHandler("send", send))

# ========= TỰ ĐỘNG SCAN =========
def auto_trade_job():
    tokens = scan_tokens()
    if tokens:
        # giả định chỉ chọn token đầu tiên
        token = tokens[0]
        logger.info(f"Phát hiện token hot: {token}")
        # có thể thêm logic kiểm tra đã trade chưa
        # test_and_buy("ĐỊA_CHỈ_NHẬN_CỦA_TOKEN")
        
scheduler.add_job(auto_trade_job, "interval", minutes=5)

# ========= CHẠY BOT =========
if __name__ == "__main__":
    updater.start_polling()
    updater.idle()
