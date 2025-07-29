import os
import sys
import ast
import json
import logging
import asyncio
import base64
import requests
import pytz
import websockets
from apscheduler.schedulers.background import BackgroundScheduler
# Thư viện Telegram Bot API (pyTelegramBotAPI)
import telebot

# Cấu hình logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Đọc các biến môi trường cần thiết
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("SOLANA_ENDPOINT", "https://api.mainnet-beta.solana.com")

# Kiểm tra biến môi trường
if not TELEGRAM_TOKEN or not CHAT_ID or not PRIVATE_KEY:
    logger.error("Missing required environment variables. Please set TELEGRAM_TOKEN, CHAT_ID, PRIVATE_KEY.")
    sys.exit(1)

# Chuyển CHAT_ID sang kiểu số nguyên
try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    logger.error("CHAT_ID must be an integer.")
    sys.exit(1)

# Thiết lập kết nối Solana RPC
from solana.rpc.api import Client
from solana.keypair import Keypair
from solana.transaction import Transaction
from solders.transaction import VersionedTransaction

solana_client = Client(RPC_URL)
# Tải khóa bí mật ví Solana từ chuỗi JSON
try:
    secret_key = json.loads(PRIVATE_KEY)
except json.JSONDecodeError:
    # Nếu chuỗi không phải JSON, thử giải mã như literal Python list
    try:
        secret_key = ast.literal_eval(PRIVATE_KEY)
    except Exception as e:
        logger.error(f"PRIVATE_KEY is not valid JSON or list format: {e}")
        sys.exit(1)
# Tạo đối tượng Keypair từ mảng byte khóa bí mật
try:
    wallet_keypair = Keypair.from_secret_key(bytes(secret_key))
except Exception as e:
    logger.error(f"Failed to load Solana keypair from PRIVATE_KEY: {e}")
    sys.exit(1)
wallet_public_key = wallet_keypair.public_key
wallet_address = str(wallet_public_key)
logger.info(f"Wallet address: {wallet_address}")

# Định nghĩa hằng số địa chỉ token USDC (USD Coin) trên Solana (6 chữ số thập phân)
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
# Định lượng mua cố định: 1 USDC (đơn vị micros, 1_000_000 = 1 USDC với 6 decimal)
BUY_AMOUNT = 1_000_000  # 1 USDC

# Biến toàn cục lưu danh sách các cặp token trending và token hiện tại đang giữ
trending_list = []
current_token_address = None
current_token_symbol = None

# Hàm bất đồng bộ kết nối WebSocket DexScreener để lấy danh sách trending
async def fetch_trending():
    uri = "wss://io.dexscreener.com/dex/screener/pairs/h24/1?rankBy[key]=trendingScoreH6&rankBy[order]=desc"
    # Header WebSocket (DexScreener yêu cầu một số header đặc thù)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://dexscreener.com",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode(),
        "Sec-WebSocket-Version": "13",
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Host": "io.dexscreener.com"
    }
    try:
        async with websockets.connect(uri, extra_headers=headers) as ws:
            # Chờ nhận gói tin đầu tiên (danh sách trending)
            message = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(message)
            if data.get("type") == "pairs":
                pairs = data.get("pairs", [])
                if pairs:
                    # Cập nhật danh sách trending toàn cục
                    global trending_list
                    trending_list = pairs
                    # Lấy token top 1 trending để log thông tin
                    top = pairs[0]
                    name = top["baseToken"]["symbol"]
                    price = float(top["priceUsd"]) if top.get("priceUsd") else None
                    if price is not None:
                        logger.info(f"Top trending: {name} - Price ${price:.6f}")
                    else:
                        logger.info(f"Top trending token: {name}")
    except Exception as e:
        logger.error(f"Failed to fetch trending pairs: {e}")

# Hàm thực thi định kỳ (gọi từ APScheduler)
def scheduled_scan():
    # Tạo event loop mới cho luồng này để chạy nhiệm vụ async
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(fetch_trending())
    finally:
        loop.close()
        # Đặt lại event loop mặc định
        asyncio.set_event_loop(None)

# Khởi tạo bot Telegram
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Xử lý lệnh /start
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    bot.reply_to(message,
                 "Chào bạn! Bot đã sẵn sàng. Các lệnh:\n"
                 "/buy - Mua token (theo cấu hình)\n"
                 "/sell - Bán token đã mua\n"
                 "/help - Hướng dẫn sử dụng")

# Xử lý lệnh /buy (mua token thủ công)
@bot.message_handler(commands=['buy'])
def handle_buy(message):
    global current_token_address, current_token_symbol
    # Kiểm tra quyền truy cập
    if message.chat.id != CHAT_ID:
        bot.reply_to(message, "❌ Bạn không có quyền sử dụng bot này.")
        return
    if not trending_list:
        bot.reply_to(message, "⚠️ Chưa có dữ liệu token trending. Thử lại sau.")
        return
    # Lấy token top 1 từ danh sách trending
    token_info = trending_list[0]
    token_address = token_info["baseToken"]["address"]
    token_symbol = token_info["baseToken"]["symbol"]
    try:
        # Gọi API Jupiter để swap USDC -> token
        quote_url = "https://quote-api.jup.ag/v6/quote"
        params = {
            "inputMint": USDC_MINT,
            "outputMint": token_address,
            "amount": BUY_AMOUNT,
            "slippageBps": 50  # chấp nhận trượt giá 0.5%
        }
        res = requests.get(quote_url, params=params)
        quote_data = res.json()
        if not quote_data.get("data"):
            bot.reply_to(message, f"❌ Không tìm được route swap cho token {token_symbol}.")
            return
        route = quote_data["data"][0]  # chọn route tốt nhất
        # Yêu cầu giao dịch swap từ Jupiter
        swap_url = "https://quote-api.jup.ag/v6/swap"
        swap_req = {
            "quoteResponse": route,
            "userPublicKey": wallet_address,
            "wrapAndUnwrapSol": False
        }
        res2 = requests.post(swap_url, json=swap_req)
        swap_data = res2.json()
        if swap_data.get("error"):
            bot.reply_to(message, f"❌ Lỗi từ Jupiter API: {swap_data['error']}")
            return
        swap_tx = swap_data.get("swapTransaction")
        if not swap_tx:
            bot.reply_to(message, "❌ Không nhận được giao dịch hoán đổi từ API.")
            return
        # Ký giao dịch nhận được bằng khóa bí mật
        swap_tx_bytes = base64.b64decode(swap_tx)
        try:
            tx = Transaction.deserialize(swap_tx_bytes)
            tx.sign(wallet_keypair)
            signed_tx = tx.serialize()
        except Exception:
            vtx = VersionedTransaction.from_bytes(swap_tx_bytes)
            vtx.sign([wallet_keypair])
            signed_tx = bytes(vtx)
        # Gửi giao dịch lên mạng Solana
        send_resp = solana_client.send_raw_transaction(signed_tx)
        # Trích xuất mã giao dịch (signature)
        if isinstance(send_resp, dict):
            # Trường hợp trả về dạng dict
            if "result" in send_resp:
                sig = send_resp["result"]
            elif "error" in send_resp:
                err = send_resp["error"]
                raise Exception(f"Giao dịch thất bại: {err}")
            else:
                sig = str(send_resp)
        else:
            # send_raw_transaction có thể trả về đối tượng có thuộc tính value
            try:
                sig = send_resp.value
            except Exception:
                sig = str(send_resp)
        logger.info(f"Mua token {token_symbol} - Giao dịch: {sig}")
        # Lưu lại token hiện tại đã mua để có thể bán sau này
        current_token_address = token_address
        current_token_symbol = token_symbol
        bot.reply_to(message, f"✅ Đã mua {token_symbol} thành công!\nTx: {sig}")
    except Exception as e:
        logger.error(f"Error in /buy: {e}")
        bot.reply_to(message, f"❌ Lỗi khi mua token: {e}")

# Xử lý lệnh /sell (bán token thủ công)
@bot.message_handler(commands=['sell'])
def handle_sell(message):
    global current_token_address, current_token_symbol
    if message.chat.id != CHAT_ID:
        bot.reply_to(message, "❌ Bạn không có quyền sử dụng bot này.")
        return
    if not current_token_address:
        bot.reply_to(message, "⚠️ Không có token nào để bán. Hãy sử dụng /buy trước.")
        return
    try:
        # Lấy số dư token trong ví để bán
        token_to_sell = current_token_address
        balance_resp = solana_client.get_token_accounts_by_owner(wallet_public_key, {"mint": token_to_sell})
        token_accounts = balance_resp.get("result", {}).get("value", [])
        if not token_accounts:
            raise Exception("Ví không nắm giữ token cần bán.")
        token_account_pubkey = token_accounts[0]["pubkey"]
        bal_info = solana_client.get_token_account_balance(token_account_pubkey)
        amount_str = bal_info.get("result", {}).get("value", {}).get("amount")
        if not amount_str or int(amount_str) == 0:
            raise Exception("Số dư token bằng 0.")
        sell_amount = int(amount_str)
        # Chuẩn bị yêu cầu swap token -> USDC qua Jupiter
        quote_url = "https://quote-api.jup.ag/v6/quote"
        params = {
            "inputMint": token_to_sell,
            "outputMint": USDC_MINT,
            "amount": sell_amount,
            "slippageBps": 50
        }
        res = requests.get(quote_url, params=params)
        quote_data = res.json()
        if not quote_data.get("data"):
            raise Exception("Không tìm được route swap để bán token.")
        route = quote_data["data"][0]
        swap_url = "https://quote-api.jup.ag/v6/swap"
        swap_req = {
            "quoteResponse": route,
            "userPublicKey": wallet_address,
            "wrapAndUnwrapSol": False
        }
        res2 = requests.post(swap_url, json=swap_req)
        swap_data = res2.json()
        if swap_data.get("error"):
            raise Exception(f"Lỗi Jupiter API: {swap_data['error']}")
        swap_tx = swap_data.get("swapTransaction")
        if not swap_tx:
            raise Exception("Không nhận được giao dịch swap để bán token.")
        # Ký và gửi giao dịch bán token
        swap_tx_bytes = base64.b64decode(swap_tx)
        try:
            tx = Transaction.deserialize(swap_tx_bytes)
            tx.sign(wallet_keypair)
            signed_tx = tx.serialize()
        except Exception:
            vtx = VersionedTransaction.from_bytes(swap_tx_bytes)
            vtx.sign([wallet_keypair])
            signed_tx = bytes(vtx)
        send_resp = solana_client.send_raw_transaction(signed_tx)
        if isinstance(send_resp, dict):
            if "result" in send_resp:
                sig = send_resp["result"]
            elif "error" in send_resp:
                err = send_resp["error"]
                raise Exception(f"Giao dịch thất bại: {err}")
            else:
                sig = str(send_resp)
        else:
            try:
                sig = send_resp.value
            except Exception:
                sig = str(send_resp)
        logger.info(f"Bán token {current_token_symbol} - Giao dịch: {sig}")
        # Xóa trạng thái token hiện tại sau khi bán xong
        sold_symbol = current_token_symbol
        current_token_address = None
        current_token_symbol = None
        bot.reply_to(message, f"✅ Đã bán {sold_symbol} thành công!\nTx: {sig}")
    except Exception as e:
        logger.error(f"Error in /sell: {e}")
        bot.reply_to(message, f"❌ Lỗi khi bán token: {e}")

# Khởi tạo lịch trình chạy hàm quét trending mỗi phút
scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(scheduled_scan, 'interval', seconds=60)
scheduler.start()
logger.info("Scheduler started for trending scan (interval 60s).")

# Bắt đầu polling bot Telegram
logger.info("Bot is polling for messages...")
bot.polling(none_stop=True)
