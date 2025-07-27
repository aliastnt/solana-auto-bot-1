import os
import time
import json
import threading
from decimal import Decimal, getcontext
import base58
import requests
from solana.rpc.api import Client
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair as SoldersKeypair
from telegram.ext import Updater, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler

# Thiết lập độ chính xác cho tính toán phần trăm
getcontext().prec = 6

# Đọc cấu hình từ biến môi trường
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Khóa bí mật ví Phantom (base58)

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not PRIVATE_KEY:
    raise Exception("Chưa cấu hình TELEGRAM_TOKEN, TELEGRAM_CHAT_ID hoặc PRIVATE_KEY trong biến môi trường")

TELEGRAM_CHAT_ID = int(TELEGRAM_CHAT_ID)

# Kết nối tới ví Phantom bằng private key base58
try:
    secret_key_bytes = base58.b58decode(PRIVATE_KEY.strip())
    # Tạo đối tượng Keypair từ khóa bí mật
    wallet = Keypair.from_secret_key(secret_key_bytes)
except Exception as e:
    raise Exception("Không thể load private key Phantom: " + str(e))

wallet_public_key = wallet.public_key
wallet_address = str(wallet_public_key)
print(f"Đã kết nối ví Phantom: {wallet_address}")

# RPC endpoint để gửi giao dịch (sử dụng mainnet-beta)
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
client = Client(RPC_URL)

# Tạo bot Telegram
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
bot = updater.bot

# Khóa luồng để quản lý truy cập vào danh sách vị thế
positions_lock = threading.Lock()

# Danh sách vị thế đang mở: khóa là địa chỉ token, giá trị là dict thông tin vị thế
positions = {}

# Cấu hình các hằng số giao dịch
TEST_BUY_SOL_AMOUNT = 0.0005  # Lượng SOL dùng để mua thử (~0.01 USD nếu SOL ~ $20)
SLIPPAGE_BPS = 100  # Slippage 1% cho giao dịch

# Hàm tiện ích gửi tin nhắn Telegram
def notify(message: str):
    """Gửi thông báo đến Telegram (chat id cấu hình sẵn)."""
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as e:
        print(f"Lỗi gửi tin nhắn Telegram: {e}")

# Hàm thực hiện swap thông qua Jupiter API, trả về transaction base64 hoặc None nếu lỗi
def jupiter_swap(input_mint: str, output_mint: str, amount: str):
    """
    Gọi API Jupiter để lấy transaction swap từ input_mint sang output_mint với lượng amount (string, tính theo đơn vị nhỏ nhất).
    Trả về chuỗi transaction (base64) nếu thành công, hoặc None nếu thất bại.
    """
    swap_url = "https://quote-api.jup.ag/v4/swap"
    payload = {
        "userPublicKey": wallet_address,
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount),
        "slippageBps": SLIPPAGE_BPS
        # Mặc định Jupiter sẽ tự động wrap/unwrap SOL nếu cần
    }
    try:
        resp = requests.post(swap_url, json=payload, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"Lỗi gọi API Jupiter swap: {e}")
        return None
    if 'error' in data:
        print(f"Jupiter API trả về lỗi: {data.get('error')}")
        return None
    tx_base64 = data.get('swapTransaction')
    if not tx_base64:
        print("Không nhận được swapTransaction từ Jupiter")
        return None
    return tx_base64

# Hàm gửi transaction lên Solana blockchain
def send_transaction(tx_base64: str):
    """
    Ký và gửi transaction (base64) lên Solana.
    Trả về True nếu gửi thành công (có chữ ký giao dịch), False nếu lỗi.
    """
    try:
        # Giải mã transaction từ base64
        raw_tx = VersionedTransaction.from_bytes(base58.b64decode(tx_base64))  # use base58 from solders?
    except Exception as e:
        # Nếu decode base58 thất bại, thử decode base64
        try:
            import base64
            raw_tx = VersionedTransaction.from_bytes(base64.b64decode(tx_base64))
        except Exception as e2:
            print(f"Lỗi giải mã transaction: {e2}")
            return False
    # Ký giao dịch bằng keypair
    try:
        solders_kp = SoldersKeypair.from_bytes(wallet.secret_key[:32])  # Lấy 32 byte secret
        signature = solders_kp.sign_message(bytes(raw_tx.message))
        signed_tx = VersionedTransaction.populate(raw_tx.message, [signature])
        encoded_tx = base64.b64encode(bytes(signed_tx)).decode('utf-8')
    except Exception as e:
        print(f"Lỗi ký transaction: {e}")
        return False
    # Gửi transaction qua RPC
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [
            encoded_tx,
            {"skipPreflight": True, "preflightCommitment": "confirmed"}
        ]
    }
    try:
        rpc_response = requests.post(RPC_URL, json=rpc_payload, timeout=10).json()
    except Exception as e:
        print(f"Lỗi gửi giao dịch RPC: {e}")
        return False
    error = rpc_response.get('error')
    if error:
        print(f"Gửi giao dịch thất bại: {error}")
        return False
    result = rpc_response.get('result')
    if result:
        print(f"Gửi giao dịch thành công, signature: {result}")
        return True
    # Nếu không có result cũng không có error rõ ràng
    print(f"Kết quả gửi giao dịch không xác định: {rpc_response}")
    return False

# Hàm xử lý phát hiện token mới và thực hiện mua thử, mua chính
def handle_new_pair(pair):
    """
    Xử lý khi phát hiện một cặp token mới đủ điều kiện.
    Thực hiện mua thử và nếu thành công thì mua chính, sau đó thêm vào danh sách vị thế.
    """
    symbol = pair["baseToken"]["symbol"]
    token_name = pair["baseToken"]["name"]
    token_address = pair["baseToken"]["address"]
    pair_address = pair["pairAddress"]
    volume5m = pair["volume"]["m5"] if "volume" in pair and "m5" in pair["volume"] else 0
    price_change_5m = pair["priceChange"]["m5"] if "priceChange" in pair and "m5" in pair["priceChange"] else 0
    price_usd = float(pair.get("priceUsd", 0))
    if price_usd == 0:
        # Bỏ qua nếu không có thông tin giá
        return

    # Lấy số vị thế hiện tại (cần lock positions khi truy cập)
    with positions_lock:
        open_count = len(positions)
        if open_count >= 3 or token_address in positions:
            # Nếu đã đạt tối đa 3 vị thế hoặc token này đã có lệnh mở, bỏ qua
            return

    # Thông tin log phát hiện (chỉ log ra console, khi gửi lệnh mua sẽ thông báo sau)
    print(f"Phát hiện token mới: {token_name} ({symbol}), volume5m={volume5m}, biến động5m={price_change_5m}%")

    # Giao dịch mua thử 0.0005 SOL (~0.01 USD) token
    # Gọi Jupiter API để tạo giao dịch swap SOL -> token (dùng WSOL mint cho SOL)
    input_mint = "So11111111111111111111111111111111111111112"  # Địa chỉ WSOL
    output_mint = token_address
    # Quy đổi lượng SOL thử ra lamports (1 SOL = 1e9 lamports)
    test_lamports = int(TEST_BUY_SOL_AMOUNT * 1_000_000_000)
    tx_swap_buy = jupiter_swap(input_mint, output_mint, str(test_lamports))
    if not tx_swap_buy:
        return  # Không tạo được giao dịch mua thử, bỏ qua
    # Ký và gửi giao dịch mua thử
    sent = send_transaction(tx_swap_buy)
    if not sent:
        notify(f"⚠️ Mua thử {symbol} thất bại, bỏ qua token này.")
        return

    # Đợi giao dịch mua thử hoàn tất (kiểm tra token về ví)
    success_test = False
    token_account_pubkey = None
    for _ in range(6):
        # Tìm tài khoản token của ví cho token này
        try:
            resp = client.get_token_accounts_by_owner(wallet_public_key, mint=PublicKey(token_address))
            value = resp.get("result", {}).get("value", [])
            if value:
                token_account_pubkey = PublicKey(value[0]["pubkey"])
                # Lấy số dư token
                bal_resp = client.get_token_account_balance(token_account_pubkey)
                balance_info = bal_resp.get("result", {}).get("value", {})
                token_amount_str = balance_info.get("amount")
                if token_amount_str and int(token_amount_str) > 0:
                    success_test = True
                    break
        except Exception as e:
            print(f"Lỗi khi kiểm tra số dư token thử: {e}")
        time.sleep(2)
    if not success_test:
        notify(f"⚠️ Mua thử {symbol} không nhận được token, bỏ qua.")
        return

    # Sau khi mua thử, thực hiện bán thử (swap token -> SOL) để kiểm tra thanh khoản và khả năng bán
    if not token_account_pubkey:
        # Nếu không tìm thấy tài khoản token (trường hợp hiếm), bỏ qua
        notify(f"⚠️ Không tìm thấy tài khoản token {symbol} sau khi mua thử, bỏ qua.")
        return
    # Lấy số lượng token vừa mua thử (để bán lại)
    bal_resp = client.get_token_account_balance(token_account_pubkey)
    balance_info = bal_resp.get("result", {}).get("value", {})
    token_amount_str = balance_info.get("amount", "0")
    if token_amount_str == "0":
        notify(f"⚠️ Không có token {symbol} để bán thử, bỏ qua.")
        return
    # Gọi Jupiter API để swap token -> SOL (WSOL) toàn bộ lượng token thử
    tx_swap_sell = jupiter_swap(token_address, input_mint, token_amount_str)
    if not tx_swap_sell:
        notify(f"⚠️ Bán thử {symbol} thất bại (không tạo được giao dịch), bỏ qua.")
        return
    sent_sell = send_transaction(tx_swap_sell)
    if not sent_sell:
        notify(f"⚠️ Bán thử {symbol} thất bại (gửi giao dịch lỗi), bỏ qua.")
        return
    # Đợi một chút để giao dịch bán hoàn tất
    time.sleep(3)
    # Kiểm tra số dư token sau khi bán thử, nếu vẫn còn nghĩa là không bán hết -> thất bại
    bal_resp2 = client.get_token_account_balance(token_account_pubkey)
    balance_info2 = bal_resp2.get("result", {}).get("value", {})
    remaining = balance_info2.get("amount", "0")
    if remaining and int(remaining) > 0:
        notify(f"⚠️ Token {symbol} không bán được hết khi thử, bỏ qua.")
        return

    # Nếu đến đây, mua/bán thử thành công -> tiến hành mua chính với 30% vốn (SOL còn lại)
    # Lấy số dư SOL hiện tại trong ví
    sol_balance_lamports = client.get_balance(wallet_public_key)["result"]["value"]
    # Dành 30% số SOL còn lại để mua
    spend_lamports = int(sol_balance_lamports * 0.3)
    if spend_lamports < 100_000:  # nếu <0.0001 SOL thì không đủ, bỏ qua
        notify(f"⚠️ Số dư SOL không đủ để mua {symbol}, bỏ qua.")
        return

    tx_swap_main = jupiter_swap(input_mint, output_mint, str(spend_lamports))
    if not tx_swap_main:
        notify(f"⚠️ Giao dịch mua {symbol} (lệnh chính) không tạo được, hủy bỏ.")
        return
    sent_main = send_transaction(tx_swap_main)
    if not sent_main:
        notify(f"⚠️ Gửi giao dịch mua {symbol} (lệnh chính) thất bại, hủy bỏ.")
        return

    # Thông báo đã mua thành công lệnh chính
    # Tính toán số lượng token mua được và giá entry
    # Đợi một chút để token về ví
    time.sleep(5)
    # Lấy số dư token sau khi mua chính
    resp_main = client.get_token_accounts_by_owner(wallet_public_key, mint=PublicKey(token_address))
    token_accounts = resp_main.get("result", {}).get("value", [])
    main_token_account = None
    if token_accounts:
        main_token_account = PublicKey(token_accounts[0]["pubkey"])
    if not main_token_account:
        notify(f"⚠️ Không tìm thấy tài khoản token {symbol} sau khi mua chính.")
        return
    bal_resp_main = client.get_token_account_balance(main_token_account)
    main_balance_info = bal_resp_main.get("result", {}).get("value", {})
    token_amount_str_main = main_balance_info.get("amount", "0")
    token_decimals = main_balance_info.get("decimals", 0)
    if token_amount_str_main == "0":
        notify(f"⚠️ Không mua được {symbol} (số dư token = 0), hủy bỏ.")
        return
    token_amount_main = Decimal(token_amount_str_main) / (Decimal(10) ** int(token_decimals))
    # Giá mua entry (USD) ước tính từ price_usd hiện tại
    entry_price_usd = price_usd  # dùng giá ngay thời điểm phát hiện làm gần đúng giá mua
    # Tính stop-loss price (entry * 0.9)
    stop_loss_price = entry_price_usd * 0.9

    # Cập nhật danh sách vị thế (thêm vị thế mới)
    with positions_lock:
        positions[token_address] = {
            "symbol": symbol,
            "pair_address": pair_address,
            "entry_price": entry_price_usd,
            "stop_loss_price": stop_loss_price,
            "sl_percent_level": -10,    # đang đặt SL ở -10% so với entry
            "next_tp_trigger": 15,      # ngưỡng tiếp theo để dời SL (15% lợi nhuận)
            "quantity": float(token_amount_main)
        }
    # Gửi thông báo lệnh mua
    notify(f"✅ Đã mua {symbol} - số lượng {token_amount_main:.4f} ~ giá {entry_price_usd:.6f} USD. Stoploss đặt ở {stop_loss_price:.6f} USD (-10%).")

# Luồng WebSocket lắng nghe dữ liệu từ DexScreener (cặp mới trên Solana)
import websockets  # thư viện websockets để kết nối
def trending_pairs_listener():
    """
    Kết nối WebSocket tới DexScreener để nhận danh sách các cặp token mới (trong 24h) đang trending trên Solana.
    Mỗi khi nhận dữ liệu, lọc các cặp đủ điều kiện và gọi xử lý.
    """
    uri = "wss://io.dexscreener.com/dex/screener/pairs/h24/1?rankBy[key]=trendingScoreH6&rankBy[order]=desc"
    while True:
        try:
            # Kết nối websocket
            ws = websockets.connect(uri, extra_headers={"Origin": "https://dexscreener.com"})
            # Sử dụng asyncio để nhận dữ liệu liên tục
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            async def listen():
                async with ws as websocket:
                    while True:
                        msg = await websocket.recv()
                        data = json.loads(msg)
                        if data.get("type") == "pairs":
                            pairs = data.get("pairs", [])
                            # Lặp qua các cặp token
                            for pair in pairs:
                                # Điều kiện: volume 5 phút > 50000 và biến động giá 5 phút > 2%
                                vol5m = pair.get("volume", {}).get("m5", 0)
                                pc5m = pair.get("priceChange", {}).get("m5", 0)
                                if vol5m is None or pc5m is None:
                                    continue
                                try:
                                    vol5m_val = float(vol5m)
                                except:
                                    vol5m_val = vol5m if isinstance(vol5m, (int,float)) else 0
                                try:
                                    pc5m_val = float(pc5m)
                                except:
                                    pc5m_val = pc5m if isinstance(pc5m, (int,float)) else 0
                                if vol5m_val > 50000 and pc5m_val > 2:
                                    # Gọi xử lý cặp token mới (mua thử/mua chính)
                                    handle_new_pair(pair)
                                    # Nếu đã đủ 3 lệnh sau khi thêm thì không xét thêm cặp khác
                                    with positions_lock:
                                        if len(positions) >= 3:
                                            break
                            # Nếu đã đủ 3 lệnh thì tạm ngừng duyệt các cặp còn lại
                            with positions_lock:
                                if len(positions) >= 3:
                                    continue
            loop.run_until_complete(listen())
        except Exception as e:
            print(f"Lỗi kết nối WS DexScreener: {e}. Thử kết nối lại sau 5s...")
            time.sleep(5)
            continue

# Hàm cập nhật vị thế: kiểm tra giá hiện tại và áp dụng trailing SL / đóng lệnh nếu cần
def update_positions():
    """
    Kiểm tra từng vị thế đang mở, cập nhật giá hiện tại và điều chỉnh stop-loss theo trailing.
    Đóng lệnh nếu chạm stop-loss.
    """
    with positions_lock:
        # Tạo bản sao danh sách token để tránh lặp trong khi chỉnh sửa
        tokens = list(positions.keys())
    for token_address in tokens:
        with positions_lock:
            pos = positions.get(token_address)
        if not pos:
            continue
        symbol = pos["symbol"]
        pair_address = pos["pair_address"]
        entry_price = pos["entry_price"]
        stop_loss_price = pos["stop_loss_price"]
        sl_percent_level = pos["sl_percent_level"]
        next_tp_trigger = pos["next_tp_trigger"]

        # Gọi DexScreener API lấy thông tin cặp hiện tại (giá mới nhất)
        url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}"
        try:
            resp = requests.get(url, timeout=5)
            data = resp.json()
        except Exception as e:
            print(f"Lỗi lấy dữ liệu giá từ DexScreener cho {symbol}: {e}")
            continue
        pairs_data = data.get("pairs")
        if not pairs_data:
            continue
        pair_data = pairs_data[0] if isinstance(pairs_data, list) else data.get("pair") or data
        price_usd = pair_data.get("priceUsd")
        if price_usd is None:
            continue
        try:
            current_price = float(price_usd)
        except:
            continue

        # Kiểm tra stop-loss: nếu giá hiện tại <= stop_loss_price => đóng lệnh
        if current_price <= stop_loss_price:
            # Thực hiện bán toàn bộ token để đóng lệnh
            # Lấy tài khoản token
            try:
                resp = client.get_token_accounts_by_owner(wallet_public_key, mint=PublicKey(token_address))
                vals = resp.get("result", {}).get("value", [])
                token_account_pubkey = PublicKey(vals[0]["pubkey"]) if vals else None
            except Exception as e:
                token_account_pubkey = None
            if not token_account_pubkey:
                notify(f"❌ Không tìm thấy tài khoản {symbol} để đóng lệnh!")
            else:
                bal = client.get_token_account_balance(token_account_pubkey)
                balance_info = bal.get("result", {}).get("value", {})
                amount_str = balance_info.get("amount", "0")
                if amount_str and int(amount_str) > 0:
                    # Swap token -> SOL (đóng lệnh)
                    tx_swap_close = jupiter_swap(token_address, "So11111111111111111111111111111111111111112", amount_str)
                    if tx_swap_close:
                        send_transaction(tx_swap_close)
            # Tính PNL %
            pnl_percent = (current_price/entry_price - 1) * 100
            pnl_percent_str = f"{pnl_percent:.2f}%"
            # Tính PNL USD xấp xỉ
            pnl_usd = (current_price - entry_price) * pos["quantity"]
            pnl_usd_str = f"{pnl_usd:.2f} USD"
            if pnl_percent >= 0:
                notify(f"✅ Chốt lời {symbol} tại {current_price:.6f} USD, PNL = +{pnl_percent_str} (~{pnl_usd_str}).")
            else:
                notify(f"❌ Cắt lỗ {symbol} tại {current_price:.6f} USD, PNL = {pnl_percent_str} (~{pnl_usd_str}).")
            # Xóa vị thế khỏi danh sách
            with positions_lock:
                positions.pop(token_address, None)
            continue

        # Nếu giá chưa chạm stop-loss, kiểm tra trailing take-profit
        profit_percent = (current_price/entry_price - 1) * 100
        if profit_percent >= next_tp_trigger:
            # Nếu đạt ngưỡng tiếp theo để dời SL
            if next_tp_trigger == 15 and sl_percent_level < 0:
                # Giá tăng >=15%: dời SL lên entry (hòa vốn)
                sl_percent_level = 0
                stop_loss_price = entry_price  # hòa vốn
                next_tp_trigger = 25  # ngưỡng tiếp theo
                notify(f"🔔 {symbol} tăng >=15% -> dời stoploss lên mức hòa vốn ({entry_price:.6f} USD).")
            else:
                # Mỗi khi giá tăng thêm 10% từ đỉnh trước -> tăng SL thêm 10%
                sl_percent_level += 10
                # Tính giá stop-loss mới tương ứng
                stop_loss_price = entry_price * (1 + sl_percent_level/100.0)
                next_tp_trigger += 10
                notify(f"🔔 Giá {symbol} đạt ~{profit_percent:.1f}% -> nâng stoploss lên +{sl_percent_level}% (≈ {stop_loss_price:.6f} USD).")
            # Cập nhật vị thế trong danh sách
            with positions_lock:
                if token_address in positions:
                    positions[token_address]["stop_loss_price"] = stop_loss_price
                    positions[token_address]["sl_percent_level"] = sl_percent_level
                    positions[token_address]["next_tp_trigger"] = next_tp_trigger

# Lệnh Telegram: đóng lệnh thủ công
def close_command(update, context):
    """Xử lý lệnh /close <symbol>: đóng vị thế thủ công theo ký hiệu token."""
    # Chỉ cho phép người dùng hợp lệ (chat id khớp) thực hiện
    if update.effective_chat.id != TELEGRAM_CHAT_ID:
        return
    args = context.args
    if not args:
        update.message.reply_text("Vui lòng chỉ định token cần đóng. Ví dụ: /close ABC")
        return
    symbol_query = args[0].upper()
    to_close = None
    # Tìm token trong danh sách vị thế theo symbol
    with positions_lock:
        for token_addr, pos in positions.items():
            if pos["symbol"].upper() == symbol_query:
                to_close = (token_addr, pos)
                break
    if not to_close:
        update.message.reply_text(f"Không tìm thấy vị thế cho token {symbol_query}.")
        return
    token_address, pos = to_close
    symbol = pos["symbol"]
    # Thực hiện bán toàn bộ token để đóng vị thế
    try:
        resp = client.get_token_accounts_by_owner(wallet_public_key, mint=PublicKey(token_address))
        vals = resp.get("result", {}).get("value", [])
        token_account_pubkey = PublicKey(vals[0]["pubkey"]) if vals else None
    except Exception as e:
        token_account_pubkey = None
    if not token_account_pubkey:
        update.message.reply_text(f"❌ Không tìm thấy tài khoản token {symbol} để đóng lệnh.")
        return
    bal = client.get_token_account_balance(token_account_pubkey)
    balance_info = bal.get("result", {}).get("value", {})
    amount_str = balance_info.get("amount", "0")
    if not amount_str or int(amount_str) == 0:
        update.message.reply_text(f"❌ Số dư {symbol} = 0, không thể đóng lệnh.")
        return
    tx_swap_close = jupiter_swap(token_address, "So11111111111111111111111111111111111111112", amount_str)
    if tx_swap_close:
        send_transaction(tx_swap_close)
    # Tính PNL tại thời điểm đóng
    current_price = None
    # Thử lấy giá hiện tại từ DexScreener
    url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{pos['pair_address']}"
    try:
        resp = requests.get(url, timeout=5)
        data = resp.json()
        pairs_data = data.get("pairs") or []
        if pairs_data:
            cp = pairs_data[0].get("priceUsd")
            current_price = float(cp) if cp else None
    except:
        current_price = None
    entry_price = pos["entry_price"]
    if current_price:
        pnl_percent = (current_price/entry_price - 1) * 100
    else:
        pnl_percent = 0
    pnl_percent_str = f"{pnl_percent:.2f}%"
    pnl_usd = (current_price - entry_price) * pos["quantity"] if current_price else 0
    pnl_usd_str = f"{pnl_usd:.2f} USD"
    if pnl_percent >= 0:
        notify(f"✅ Đã đóng lệnh {symbol} thủ công tại giá ~{current_price:.6f} USD, PNL = +{pnl_percent_str} (~{pnl_usd_str}).")
    else:
        notify(f"✅ Đã đóng lệnh {symbol} thủ công tại giá ~{current_price:.6f} USD, PNL = {pnl_percent_str} (~{pnl_usd_str}).")
    # Xóa vị thế
    with positions_lock:
        positions.pop(token_address, None)

# Thêm handler cho lệnh /close
dp = updater.dispatcher
dp.add_handler(CommandHandler("close", close_command, pass_args=True))

# Khởi động lịch trình kiểm tra vị thế
scheduler = BackgroundScheduler()
scheduler.add_job(update_positions, 'interval', seconds=5, id='update_positions')
scheduler.start()

# Khởi chạy luồng lắng nghe cặp mới trending
ws_thread = threading.Thread(target=trending_pairs_listener, daemon=True)
ws_thread.start()

# Bắt đầu bot Telegram
updater.start_polling()
notify("🤖 Bot giao dịch tự động đã khởi động.")
updater.idle()
