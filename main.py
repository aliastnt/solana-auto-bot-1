# coding: utf-8
import os
import sys
import logging

# Thư viện Telegram bot (python-telegram-bot 13.15)
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

# Thư viện Solana RPC
from solana.rpc.api import Client

# Thư viện Solders (cho Keypair, Pubkey, và System Program)
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer

# Các thư viện khác
import base58
import requests
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# Thiết lập logging để theo dõi bot
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Đọc token của bot và private key Solana từ biến môi trường (nếu có)
TOKEN = os.environ.get("BOT_TOKEN")
INITIAL_PRIVKEY = os.environ.get("SOL_PRIVATE_KEY")

# Biến toàn cục lưu trữ trạng thái ví và cặp token đang theo dõi
user_keypair = None  # Keypair người dùng (sẽ được gán sau khi /connect)
user_pubkey = None   # Địa chỉ ví (chuỗi base58) tương ứng với user_keypair
tracking_pair = None  # ID cặp token DexScreener đang theo dõi (chuỗi)
alert_price = None    # Ngưỡng giá đặt cảnh báo (float USD)
alert_direction = None  # Hướng biến động giá cần cảnh báo ("up" hoặc "down")
alert_chat_id = None   # ID chat sẽ nhận cảnh báo giá

# Nếu có private key ban đầu trong biến môi trường, tự động khởi tạo Keypair
if INITIAL_PRIVKEY:
    try:
        user_keypair = Keypair.from_base58_string(INITIAL_PRIVKEY)
        user_pubkey = str(user_keypair.pubkey())
        logging.info(f"Loaded wallet from ENV with address: {user_pubkey}")
    except Exception as e:
        logging.error(f"ENV SOL_PRIVATE_KEY invalid: {e}")
        user_keypair = None
        user_pubkey = None

# Kết nối RPC Solana (mặc định dùng mainnet, có thể đổi sang devnet nếu cần)
solana_rpc_url = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
client = Client(solana_rpc_url)
logging.info(f"Connected to Solana RPC: {solana_rpc_url}")

# Hằng số quy đổi
LAMPORTS_PER_SOL = 1000000000

# Handler cho lệnh /start
def start(update, context):
    update.message.reply_text(
        "🤖 Xin chào! Đây là bot giao dịch Solana.\n"
        "Bạn có thể kết nối ví Solana của mình hoặc tạo ví mới bằng lệnh /connect.\n"
        "Các lệnh khả dụng:\n"
        "- /connect <PRIVATE_KEY>: Kết nối ví (private key định dạng base58)\n"
        "- /connect new: Tạo ví Solana mới\n"
        "- /address: Xem địa chỉ ví hiện tại\n"
        "- /balance: Xem số dư SOL của ví\n"
        "- /setpair <ID>: Chọn cặp token (DexScreener pair address) để theo dõi\n"
        "- /price: Xem giá hiện tại của cặp token đã chọn\n"
        "- /alert <GIÁ_USD>: Đặt cảnh báo giá cho cặp token\n"
        "- /send <ĐỊA_CHỈ> <SỐ_SOL>: Chuyển SOL tới địa chỉ khác\n"
        "- /help: Xem hướng dẫn sử dụng"
    )
    # Nếu bot chưa có ví, nhắc người dùng kết nối
    if user_keypair is None:
        update.message.reply_text(
            "💡 Bạn chưa kết nối ví. Hãy dùng /connect <private_key> hoặc /connect new để tiếp tục."
        )

# Handler cho lệnh /help (hiển thị tương tự /start)
def help_command(update, context):
    start(update, context)

# Handler cho lệnh /connect (kết nối hoặc tạo ví)
def connect(update, context):
    global user_keypair, user_pubkey, alert_price, alert_direction, alert_chat_id
    args = context.args
    if len(args) == 0:
        update.message.reply_text("Vui lòng cung cấp khoá bí mật (private key) dạng base58 hoặc nhập 'new' để tạo ví mới.")
        return
    key_str = args[0].strip()
    if key_str.lower() in ("new", "tao", "tạo"):
        # Tạo ví Solana mới
        new_kp = Keypair()
        new_pub = new_kp.pubkey()
        user_keypair = new_kp
        user_pubkey = str(new_pub)
        # Mã hoá private key thành chuỗi base58 để hiển thị cho người dùng
        priv_b58 = base58.b58encode(bytes(new_kp)).decode()
        update.message.reply_text(
            "✅ Đã tạo ví Solana mới!\n"
            f"🔑 Private key (base58): `{priv_b58}`\n"
            f"🔓 Địa chỉ (public key): `{user_pubkey}`\n\n"
            "Hãy **lưu lại** khoá bí mật trên để sử dụng sau này. "
            "Bạn có thể cấu hình biến môi trường `SOL_PRIVATE_KEY` với giá trị trên để bot tự động kết nối ví sau khi khởi động lại."
        )
        # Xoá cảnh báo giá cũ (nếu có) khi đổi ví
        alert_price = None
        alert_direction = None
        alert_chat_id = None
    else:
        # Kết nối ví bằng private key do người dùng cung cấp
        try:
            kp = Keypair.from_base58_string(key_str)
        except Exception as e:
            update.message.reply_text("❌ Khoá bí mật không hợp lệ. Đảm bảo bạn nhập đúng chuỗi base58 của private key.")
            return
        user_keypair = kp
        user_pubkey = str(kp.pubkey())
        update.message.reply_text(f"✅ Đã kết nối ví Solana! Địa chỉ ví: `{user_pubkey}`")
        # Xoá bất kỳ cảnh báo giá cũ nào khi thay đổi ví
        alert_price = None
        alert_direction = None
        alert_chat_id = None

# Handler cho lệnh /address (hoặc /wallet) - hiển thị địa chỉ ví hiện tại
def address(update, context):
    if user_pubkey:
        update.message.reply_text(f"Địa chỉ ví của bạn: `{user_pubkey}`")
    else:
        update.message.reply_text("🔎 Chưa có ví được kết nối. Hãy dùng /connect để kết nối ví Solana của bạn.")

# Handler cho lệnh /balance - lấy số dư SOL của ví
def balance(update, context):
    if user_pubkey is None:
        update.message.reply_text("💰 Bạn chưa kết nối ví. Vui lòng dùng /connect để kết nối ví trước.")
        return
    try:
        # Gọi RPC get_balance
        balance_resp = client.get_balance(Pubkey.from_string(user_pubkey))
        lamports = balance_resp.value  # số lamport (1 SOL = 1e9 lamport)
        sol_amount = lamports / LAMPORTS_PER_SOL
        update.message.reply_text(f"Số dư: {lamports} lamport = {sol_amount:.9f} SOL")
    except Exception as e:
        logging.error(f"Lỗi khi get_balance: {e}")
        update.message.reply_text("❌ Không thể lấy số dư. Vui lòng thử lại sau.")

# Handler cho lệnh /setpair - đặt cặp token để theo dõi
def setpair(update, context):
    global tracking_pair
    if len(context.args) == 0:
        update.message.reply_text("Vui lòng cung cấp ID cặp token (DexScreener pair address) sau lệnh /setpair.")
        return
    pair_id = context.args[0].strip()
    tracking_pair = pair_id
    update.message.reply_text(f"✅ Đã chọn cặp token: `{tracking_pair}`. Bạn có thể dùng /price để xem giá.")

# Handler cho lệnh /price - lấy giá hiện tại của cặp token đang theo dõi
def price(update, context):
    if tracking_pair is None:
        update.message.reply_text("Bạn chưa thiết lập cặp token. Hãy dùng /setpair <pair_id> trước.")
        return
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{tracking_pair}"
        res = requests.get(url, timeout=10)
        data = res.json()
        if "pairs" in data and data["pairs"]:
            info = data["pairs"][0]
            price_usd = float(info.get("priceUsd", 0.0))
            base_token = info.get("baseToken", {}).get("symbol", "")
            quote_token = info.get("quoteToken", {}).get("symbol", "USD")
            update.message.reply_text(
                f"💱 Giá hiện tại của cặp {base_token}/{quote_token}: {price_usd:.6f} USD"
            )
        else:
            update.message.reply_text("❌ Không tìm thấy thông tin giá cho cặp đã chọn.")
    except Exception as e:
        logging.error(f"Lỗi khi lấy giá DexScreener: {e}")
        update.message.reply_text("❌ Lỗi khi kết nối DexScreener để lấy giá.")

# Handler cho lệnh /alert - đặt cảnh báo giá
def alert(update, context):
    global alert_price, alert_direction, alert_chat_id
    if tracking_pair is None:
        update.message.reply_text("Bạn cần /setpair trước khi đặt cảnh báo giá.")
        return
    if len(context.args) == 0:
        update.message.reply_text("Vui lòng cung cấp mức giá USD để cảnh báo, ví dụ: /alert 0.5")
        return
    try:
        threshold = float(context.args[0])
    except:
        update.message.reply_text("❌ Mức giá không hợp lệ. Hãy nhập một số (vd: 0.5)")
        return
    # Lấy giá hiện tại để xác định hướng biến động
    try:
        res = requests.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{tracking_pair}", timeout=5)
        data = res.json()
        current_price = float(data["pairs"][0]["priceUsd"]) if ("pairs" in data and data["pairs"]) else None
    except Exception:
        current_price = None
    if current_price is None:
        update.message.reply_text("⚠️ Không thể lấy giá hiện tại để thiết lập cảnh báo.")
    else:
        if abs(threshold - current_price) < 1e-9:
            update.message.reply_text("⚠️ Mức giá bạn nhập đang bằng với giá hiện tại.")
        elif threshold > current_price:
            alert_direction = "up"
            alert_price = threshold
            alert_chat_id = update.effective_chat.id
            update.message.reply_text(
                f"🔔 Đã đặt cảnh báo khi giá tăng đến {threshold:.6f} USD (giá hiện tại: {current_price:.6f} USD)."
            )
        else:
            alert_direction = "down"
            alert_price = threshold
            alert_chat_id = update.effective_chat.id
            update.message.reply_text(
                f"🔔 Đã đặt cảnh báo khi giá giảm xuống {threshold:.6f} USD (giá hiện tại: {current_price:.6f} USD)."
            )

# Handler cho lệnh /send - gửi SOL tới địa chỉ khác
def send(update, context):
    if user_keypair is None:
        update.message.reply_text("🚫 Bạn chưa kết nối ví để gửi SOL.")
        return
    # Yêu cầu cú pháp: /send <địa_chỉ_nhận> <số_SOL>
    args = context.args
    if len(args) < 2:
        update.message.reply_text("Usage: /send <địa_chỉ ví nhận> <số_SOL>")
        return
    to_address = args[0].strip()
    amount_str = args[1].replace(",", ".")
    try:
        amount_sol = float(amount_str)
    except:
        update.message.reply_text("❌ Số lượng SOL không hợp lệ.")
        return
    if amount_sol <= 0:
        update.message.reply_text("❌ Số lượng SOL phải lớn hơn 0.")
        return
    lamports = int(amount_sol * LAMPORTS_PER_SOL)
    try:
        # Tạo instruction chuyển SOL bằng System Program
        ix = transfer(
            TransferParams(
                from_pubkey=user_keypair.pubkey(),
                to_pubkey=Pubkey.from_string(to_address),
                lamports=lamports
            )
        )
        # Lấy blockhash mới nhất để hợp lệ hoá transaction
        latest_blockhash = client.get_latest_blockhash()
        blockhash_obj = latest_blockhash.value.blockhash
        # Biên dịch Message v0
        message = MessageV0.try_compile(
            payer=user_keypair.pubkey(),
            instructions=[ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash_obj
        )
        # Tạo transaction (đã ký bằng user_keypair)
        tx = VersionedTransaction(message, [user_keypair])
        # Gửi transaction
        send_resp = client.send_transaction(tx)
        # Lấy tx signature (ở dạng chuỗi base58)
        tx_signature = send_resp.value  # SendTransactionResp.value là signature
        update.message.reply_text(
            "✅ Đã gửi thành công!\n"
            f"🔗 Giao dịch: https://solscan.io/tx/{tx_signature}\n"
            f"(Tx signature: {tx_signature})"
        )
    except Exception as e:
        logging.error(f"Lỗi khi gửi transaction: {e}")
        update.message.reply_text(f"❌ Gửi SOL thất bại: {e}")

# Handler cho các tin nhắn/command không xác định
def unknown(update, context):
    update.message.reply_text("❓ Không hiểu yêu cầu. Gõ /help để xem hướng dẫn.")

# Thiết lập scheduler để kiểm tra giá định kỳ (phục vụ /alert)
tz = pytz.timezone("Asia/Ho_Chi_Minh")
scheduler = BackgroundScheduler(timezone=tz)

def price_check_job():
    global alert_price, alert_direction, alert_chat_id, tracking_pair
    if alert_price is None or alert_direction is None or tracking_pair is None:
        return  # không có cảnh báo nào
    try:
        res = requests.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{tracking_pair}", timeout=5)
        data = res.json()
        if "pairs" in data and data["pairs"]:
            current_price = float(data["pairs"][0]["priceUsd"])
        else:
            current_price = None
    except Exception as e:
        logging.error(f"Lỗi cập nhật giá định kỳ: {e}")
        current_price = None
    if current_price is None:
        return
    # Kiểm tra điều kiện kích hoạt cảnh báo
    if alert_direction == "up" and current_price >= alert_price:
        # Giá tăng vượt ngưỡng
        try:
            bot.send_message(alert_chat_id, f"🔔 Giá đã tăng lên {current_price:.6f} USD, vượt ngưỡng {alert_price:.6f} USD!")
        except Exception as e:
            logging.error(f"Không gửi được tin nhắn cảnh báo: {e}")
        # Tắt cảnh báo sau khi gửi
        alert_price = None
        alert_direction = None
    elif alert_direction == "down" and current_price <= alert_price:
        # Giá giảm xuống dưới ngưỡng
        try:
            bot.send_message(alert_chat_id, f"🔔 Giá đã giảm xuống {current_price:.6f} USD, dưới ngưỡng {alert_price:.6f} USD!")
        except Exception as e:
            logging.error(f"Không gửi được tin nhắn cảnh báo: {e}")
        alert_price = None
        alert_direction = None

# Thêm job kiểm tra giá 10 giây một lần
scheduler.add_job(price_check_job, "interval", seconds=10)
scheduler.start()

# Khởi động bot Telegram
if __name__ == "__main__":
    if not TOKEN:
        logging.error("Chưa cấu hình BOT_TOKEN. Thoát chương trình.")
        sys.exit(1)
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # Đăng ký các handler cho bot
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("connect", connect))
    dp.add_handler(CommandHandler("address", address))
    dp.add_handler(CommandHandler("wallet", address))  # alias /wallet cho /address
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("setpair", setpair))
    dp.add_handler(CommandHandler("price", price))
    dp.add_handler(CommandHandler("alert", alert))
    dp.add_handler(CommandHandler("send", send))

    # Handler cho tin nhắn không phải lệnh
    dp.add_handler(MessageHandler(Filters.command, unknown))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, unknown))

    # Lấy đối tượng bot để dùng trong scheduler
    bot = updater.bot

    # Bắt đầu polling để nhận cập nhật từ Telegram
    updater.start_polling()
    logging.info("Bot is polling...")
    updater.idle()
