import os
import sys
import logging
import json
import base64
import base58
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from solana.keypair import Keypair
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.rpc.commitment import Processed
from solders.transaction import VersionedTransaction
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# Thiết lập logger để ghi log thông tin
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Đọc các biến môi trường cần thiết
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
SOLANA_ENDPOINT = os.getenv("SOLANA_ENDPOINT")
CHAT_ID = os.getenv("CHAT_ID")

# Kiểm tra biến môi trường, nếu thiếu thì ghi log lỗi và thoát
required_vars = {"TELEGRAM_TOKEN": TELEGRAM_TOKEN, 
                 "PRIVATE_KEY": PRIVATE_KEY, 
                 "SOLANA_ENDPOINT": SOLANA_ENDPOINT, 
                 "CHAT_ID": CHAT_ID}
missing_vars = [name for name, val in required_vars.items() if not val]
if missing_vars:
    logger.error("Missing environment variables: " + ", ".join(missing_vars))
    sys.exit(1)

# Kết nối RPC Solana và khởi tạo Keypair ví từ PRIVATE_KEY (định dạng base58)
try:
    secret_key_bytes = base58.b58decode(PRIVATE_KEY)
    wallet = Keypair.from_secret_key(secret_key_bytes)
except Exception as e:
    logger.error("Invalid PRIVATE_KEY provided, unable to decode. Error: %s", e)
    sys.exit(1)

wallet_address = str(wallet.public_key)
logger.info(f"Wallet address: {wallet_address}")
solana_client = Client(SOLANA_ENDPOINT)

# Hàm xử lý lệnh /start
def start(update: Update, context: CallbackContext) -> None:
    # Chỉ phản hồi nếu chat ID trùng với CHAT_ID đã cấu hình (tránh người lạ)
    if str(update.effective_chat.id) != str(CHAT_ID):
        return
    update.message.reply_text("Bot giao dịch Solana đã sẵn sàng. Sử dụng /buy <địa_chỉ_token> <số_SOL> để mua, hoặc /sell <địa_chỉ_token> để bán toàn bộ token đó.")

# Hàm xử lý lệnh /buy <token_address> <amount_in_SOL>
def buy(update: Update, context: CallbackContext) -> None:
    if str(update.effective_chat.id) != str(CHAT_ID):
        return  # bỏ qua nếu không phải chat được phép
    args = context.args
    if len(args) < 1:
        update.message.reply_text("Cú pháp: /buy <địa_chỉ_token> [số_SOL_muốn_dùng]")
        return
    token_address = args[0]
    # Nếu không chỉ định số SOL, mặc định dùng 0.01 SOL
    try:
        amount_sol = float(args[1]) if len(args) > 1 else 0.01
    except Exception:
        update.message.reply_text("Số lượng SOL không hợp lệ. Vui lòng nhập một số.")
        return
    if amount_sol <= 0:
        update.message.reply_text("Số lượng SOL phải lớn hơn 0.")
        return

    try:
        # Gọi Jupiter API để lấy route hoán đổi tốt nhất từ SOL -> token
        params = {
            "inputMint": "So11111111111111111111111111111111111111112",  # WSOL (đại diện cho SOL)
            "outputMint": token_address,
            "amount": int(amount_sol * 1_000_000_000)  # đổi SOL sang lamports
        }
        quote_resp = requests.get("https://quote-api.jup.ag/v6/quote", params=params)
        quote_data = quote_resp.json()
        # Kiểm tra kết quả nhận được từ quote
        if not quote_data or quote_data.get("data") == [] or quote_data.get("outAmount") is None:
            update.message.reply_text("Không tìm được route hoán đổi cho token đã chọn.")
            return

        # Gọi API swap của Jupiter để lấy giao dịch đã mã hóa
        swap_payload = {
            "userPublicKey": wallet_address,
            "quoteResponse": quote_data
        }
        swap_resp = requests.post("https://quote-api.jup.ag/v6/swap", json=swap_payload)
        swap_data = swap_resp.json()
        if "swapTransaction" not in swap_data:
            logger.error(f"Swap API response error: {swap_data}")
            update.message.reply_text("Giao dịch mua không thành công (API swap lỗi).")
            return

        # Giải mã transaction và ký giao dịch bằng ví
        swap_txn_base64 = swap_data["swapTransaction"]
        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_txn_base64))
        signature = wallet.sign_message(raw_tx.message.serialize())  # chữ ký giao dịch
        signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
        # Gửi giao dịch đã ký lên mạng Solana
        result = solana_client.send_raw_transaction(bytes(signed_tx), opts=TxOpts(skip_preflight=False, preflight_commitment=Processed))
        # Lấy transaction signature (ID giao dịch) từ kết quả trả về
        try:
            tx_signature = json.loads(result.to_json())["result"]
        except Exception:
            tx_signature = result.get("result") if isinstance(result, dict) else None

        if tx_signature:
            update.message.reply_text(f"Đã mua token (mint: {token_address}). TxID: {tx_signature}")
        else:
            update.message.reply_text("Đã gửi giao dịch mua token, nhưng không nhận được TxID.")
    except Exception as e:
        logger.error(f"Lỗi khi thực hiện mua token: {e}")
        update.message.reply_text("Đã xảy ra lỗi khi mua token.")

# Hàm xử lý lệnh /sell <token_address>
def sell(update: Update, context: CallbackContext) -> None:
    if str(update.effective_chat.id) != str(CHAT_ID):
        return
    args = context.args
    if len(args) < 1:
        update.message.reply_text("Cú pháp: /sell <địa_chỉ_token>")
        return
    token_address = args[0]

    try:
        # Lấy tài khoản token trong ví (nếu có)
        resp = solana_client.get_token_accounts_by_owner(wallet_address, {"mint": token_address})
        accounts = resp.get("result", {}).get("value", [])
        if not accounts:
            update.message.reply_text("Ví không nắm giữ token này.")
            return
        token_account_pubkey = accounts[0]["pubkey"]

        # Lấy số dư token (toàn bộ) để bán
        balance_resp = solana_client.get_token_account_balance(token_account_pubkey)
        if not balance_resp.get("result"):
            update.message.reply_text("Không thể lấy số dư token.")
            return
        balance_info = balance_resp["result"]["value"]
        token_amount_str = balance_info.get("amount")  # lượng token (ở đơn vị nhỏ nhất) dạng chuỗi
        token_decimals = balance_info.get("decimals", 0)
        if token_amount_str is None:
            update.message.reply_text("Không thể lấy số dư token.")
            return
        try:
            input_amount = int(token_amount_str)
        except Exception:
            update.message.reply_text("Số dư token không hợp lệ.")
            return
        if input_amount <= 0:
            update.message.reply_text("Số lượng token trong ví bằng 0, không có gì để bán.")
            return

        # Gọi Jupiter API để lấy route hoán đổi từ token -> SOL
        params = {
            "inputMint": token_address,
            "outputMint": "So11111111111111111111111111111111111111112",  # WSOL (để hoán đổi thành SOL)
            "amount": input_amount
        }
        quote_resp = requests.get("https://quote-api.jup.ag/v6/quote", params=params)
        quote_data = quote_resp.json()
        if not quote_data or quote_data.get("data") == [] or quote_data.get("outAmount") is None:
            update.message.reply_text("Không tìm được route hoán đổi để bán token này.")
            return

        # Gọi API swap để lấy giao dịch bán (từ token -> SOL)
        swap_payload = {
            "userPublicKey": wallet_address,
            "quoteResponse": quote_data
        }
        swap_resp = requests.post("https://quote-api.jup.ag/v6/swap", json=swap_payload)
        swap_data = swap_resp.json()
        if "swapTransaction" not in swap_data:
            logger.error(f"Swap API response error (sell): {swap_data}")
            update.message.reply_text("Giao dịch bán không thành công (API swap lỗi).")
            return

        # Giải mã và ký giao dịch swap
        swap_txn_base64 = swap_data["swapTransaction"]
        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_txn_base64))
        signature = wallet.sign_message(raw_tx.message.serialize())
        signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
        result = solana_client.send_raw_transaction(bytes(signed_tx), opts=TxOpts(skip_preflight=False, preflight_commitment=Processed))
        try:
            tx_signature = json.loads(result.to_json())["result"]
        except Exception:
            tx_signature = result.get("result") if isinstance(result, dict) else None

        if tx_signature:
            update.message.reply_text(f"Đã bán token (mint: {token_address}). TxID: {tx_signature}")
        else:
            update.message.reply_text("Đã gửi giao dịch bán token, nhưng không nhận được TxID.")
    except Exception as e:
        logger.error(f"Lỗi khi thực hiện bán token: {e}")
        update.message.reply_text("Đã xảy ra lỗi khi bán token.")

# Thiết lập bộ hẹn giờ tự động (auto-trade) quét token xu hướng mỗi 60 giây
def scheduled_scan():
    try:
        # Lấy danh sách token xu hướng (trending) từ API Jupiter (Birdeye)
        trending_resp = requests.get("https://tokens.jup.ag/tokens?tags=birdeye-trending")
        trending_tokens = trending_resp.json()
        if not trending_tokens or not isinstance(trending_tokens, list):
            return  # nếu không lấy được danh sách nào
        # Chọn token đứng đầu danh sách xu hướng
        top_token = trending_tokens[0]
        token_mint = top_token.get("address")
        token_symbol = top_token.get("symbol", "<unknown>")
        # Kiểm tra nếu ví đã nắm giữ token này thì bỏ qua (tránh mua trùng)
        resp = solana_client.get_token_accounts_by_owner(wallet_address, {"mint": token_mint})
        already_holding = resp.get("result", {}).get("value", [])
        if already_holding:
            logger.info(f"Đã nắm giữ {token_symbol}, bỏ qua không mua lại.")
            return

        # Thực hiện mua tự động một lượng nhỏ token xu hướng
        trade_amount_sol = 0.01  # dùng 0.01 SOL để mua token trending
        params = {
            "inputMint": "So11111111111111111111111111111111111111112",
            "outputMint": token_mint,
            "amount": int(trade_amount_sol * 1_000_000_000)
        }
        quote_resp = requests.get("https://quote-api.jup.ag/v6/quote", params=params)
        quote_data = quote_resp.json()
        if not quote_data or quote_data.get("data") == [] or quote_data.get("outAmount") is None:
            logger.info(f"Không tìm được route để mua token xu hướng: {token_symbol}.")
            return
        swap_payload = {
            "userPublicKey": wallet_address,
            "quoteResponse": quote_data
        }
        swap_resp = requests.post("https://quote-api.jup.ag/v6/swap", json=swap_payload)
        swap_data = swap_resp.json()
        if "swapTransaction" not in swap_data:
            logger.error(f"Giao dịch mua token xu hướng thất bại: {swap_data}")
            return
        swap_txn_base64 = swap_data["swapTransaction"]
        raw_tx = VersionedTransaction.from_bytes(base64.b64decode(swap_txn_base64))
        signature = wallet.sign_message(raw_tx.message.serialize())
        signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
        result = solana_client.send_raw_transaction(bytes(signed_tx), opts=TxOpts(skip_preflight=False, preflight_commitment=Processed))
        try:
            tx_signature = json.loads(result.to_json())["result"]
        except Exception:
            tx_signature = result.get("result") if isinstance(result, dict) else None

        if tx_signature:
            logger.info(f"Auto-trade: Đã mua {token_symbol} (mint: {token_mint}), TxID: {tx_signature}")
        else:
            logger.error(f"Auto-trade: Gửi giao dịch mua {token_symbol} nhưng không nhận được TxID.")
    except Exception as e:
        logger.error(f"Failed to fetch trending pairs: {e}")

# Khởi tạo bot Telegram và gắn hàm xử lý cho các lệnh
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("buy", buy))
dispatcher.add_handler(CommandHandler("sell", sell))

# Bắt đầu bộ lập lịch tự động và bot polling
scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(scheduled_scan, 'interval', seconds=60)
scheduler.start()
logger.info("Đã khởi động bộ hẹn giờ (auto-trade).")

updater.start_polling()
logger.info("Bot Telegram đang chạy (polling).")
updater.idle()
