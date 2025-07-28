import os
import logging
import base58
import requests
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.rpc.types import TxOpts

# Thiết lập logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Đọc cấu hình từ biến môi trường (chú ý sử dụng TELEGRAM_TOKEN thay vì TELEGRAM_BOT_TOKEN)
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
PRIVATE_KEY = os.getenv('PRIVATE_KEY')
SOLANA_ENDPOINT = os.getenv('SOLANA_ENDPOINT')
if not TELEGRAM_TOKEN or not CHAT_ID or not PRIVATE_KEY or not SOLANA_ENDPOINT:
    raise Exception('Thiếu cấu hình môi trường: TELEGRAM_TOKEN, CHAT_ID, PRIVATE_KEY, SOLANA_ENDPOINT')
CHAT_ID = int(CHAT_ID)

# Khởi tạo Keypair từ private key Phantom (sử dụng solders để tránh lỗi import) [oai_citation:4‡stackoverflow.com](https://stackoverflow.com/questions/77814538/cannot-import-publickey-from-solana-publickey#:~:text=Based%20on%20their%20github%2C%20it,Have%20you%20tried%20this%20instead)
try:
    secret_key_bytes = base58.b58decode(PRIVATE_KEY)
except Exception as e:
    raise Exception('Private key base58 không hợp lệ')
if len(secret_key_bytes) not in (64, 32):
    raise Exception(f'Độ dài private key bytes không hợp lệ: {len(secret_key_bytes)} bytes')
if len(secret_key_bytes) == 64:
    keypair = Keypair.from_bytes(secret_key_bytes)
else:
    # Nếu chỉ có 32 byte (seed), tạo Keypair từ seed
    keypair = Keypair.from_bytes(secret_key_bytes + bytes(32))
public_key = keypair.pubkey()
wallet_address = str(public_key)
logger.info(f'Wallet address: {wallet_address}')

# Kết nối tới RPC Solana
solana_client = Client(SOLANA_ENDPOINT)

open_trades = {}
max_parallel_trades = 3

def get_solana_price_usd():
    try:
        resp = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd', timeout=5)
        data = resp.json()
        return float(data['solana']['usd']) if 'solana' in data else None
    except Exception as e:
        logger.error(f'Lỗi lấy giá SOL: {e}')
        return None

def usd_to_lamports(usd_amount, sol_price_usd):
    if sol_price_usd is None or sol_price_usd <= 0:
        return None
    sol_amount = usd_amount / sol_price_usd
    lamports = int(sol_amount * 1_000_000_000)
    if lamports < 1000:
        lamports = 1000
    return lamports

def scan_dexscreener():
    try:
        resp = requests.get('https://dexscreener.com/gainers/solana', timeout=10)
        html = resp.text
    except Exception as e:
        logger.error(f'Lỗi fetch DexScreener: {e}')
        return []
    tokens_found = []
    lines = html.split('href="/solana/')
    for segment in lines[1:]:
        pair_id = segment.split('"')[0] if '"' in segment else None
        if not pair_id:
            continue
        text = segment.split('</a>')[0].split('>')[-1]
        if '#' not in text or '%' not in text:
            continue
        # Lấy biến động 5m (percent đầu tiên nếu không bị '-' thay thế)
        percent_parts = text.split('%')
        percent_vals = []
        for i in range(len(percent_parts)-1):
            val = percent_parts[i].split()[-1]
            percent_vals.append(val)
        if not percent_vals:
            continue
        if percent_vals[0] == '-':
            five_min_change = 0.0
        else:
            try:
                five_min_change = float(percent_vals[0])
            except:
                continue
        # Lấy volume (USD) sau số lượng giao dịch (txns)
        parts = text.split()
        # Tìm ký tự '$' thứ 2 trong chuỗi (volume USD)
        dollars = [i for i, p in enumerate(parts) if p == '$']
        if len(dollars) < 2:
            continue
        vol_index = dollars[1] + 1
        if vol_index >= len(parts):
            continue
        vol_str = parts[vol_index]
        try:
            if vol_str.endswith('M'):
                volume_usd = float(vol_str[:-1]) * 1_000_000
            elif vol_str.endswith('K'):
                volume_usd = float(vol_str[:-1]) * 1_000
            else:
                volume_usd = float(vol_str)
        except:
            continue
        # Lấy symbol (trước dấu /)
        if '/' in text:
            symbol = text.split('/')[0].split()[-1]
        else:
            symbol = None
        if symbol and five_min_change >= 2 and volume_usd >= 50000:
            tokens_found.append({'symbol': symbol, 'pair_id': pair_id})
    return tokens_found

def get_token_info(pair_id):
    try:
        data = requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair_id}', timeout=5).json()
    except Exception as e:
        logger.error(f'Lỗi API DexScreener: {e}')
        return None
    pairs = data.get('pairs') or []
    if not pairs:
        return None
    info = pairs[0]
    base = info.get('baseToken', {})
    quote = info.get('quoteToken', {})
    base_sym = base.get('symbol'); base_addr = base.get('address')
    quote_sym = quote.get('symbol'); quote_addr = quote.get('address')
    token_addr = None; token_sym = None
    if quote_sym in ['SOL', 'USDC', 'USDT']:
        token_addr = base_addr; token_sym = base_sym
    elif base_sym in ['SOL', 'USDC', 'USDT']:
        token_addr = quote_addr; token_sym = quote_sym
    else:
        token_addr = base_addr; token_sym = base_sym
    return {'address': token_addr, 'symbol': token_sym, 'pair_id': pair_id}

def execute_swap(input_mint, output_mint, amount, user_keypair, unwrap_sol=False):
    try:
        quote_url = f'https://quote-api.jup.ag/v4/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippage=0.5'
        routes = requests.get(quote_url, timeout=10).json().get('data', [])
        if not routes:
            return None
        route = routes[0]
        swap_req = {
            'route': route,
            'userPublicKey': wallet_address,
            'wrapUnwrapSol': unwrap_sol,
            'feeAccount': None,
            'asLegacyTransaction': True
        }
        swap_res = requests.post('https://quote-api.jup.ag/v4/swap', json=swap_req, timeout=10).json()
        swap_tx = swap_res.get('swapTransaction')
        if not swap_tx:
            return None
        import base64
        tx_bytes = base64.b64decode(swap_tx)
        from solana.transaction import Transaction
        tx = Transaction.deserialize(tx_bytes)
        tx.sign(user_keypair)
        opts = TxOpts(skip_confirmation=False, preflight_commitment='confirmed')
        try:
            sig = solana_client.send_raw_transaction(tx.serialize(), opts=opts)
        except Exception as e:
            logger.error(f'Gửi giao dịch lỗi: {e}')
            return None
        return sig['result']
    except Exception as e:
        logger.error(f'Lỗi swap: {e}')
        return None

def close_trade(symbol):
    if symbol not in open_trades:
        return 'Không có lệnh cho token này.'
    trade = open_trades[symbol]
    token_addr = trade['token_address']
    token_accs = solana_client.get_token_accounts_by_owner(wallet_address, mint=token_addr)
    vals = token_accs.get('result', {}).get('value', [])
    if not vals:
        return 'Ví không giữ token này.'
    token_account = vals[0]['pubkey']
    bal_resp = solana_client.get_token_account_balance(token_account)
    amount_str = bal_resp.get('result', {}).get('value', {}).get('amount')
    if not amount_str or int(amount_str) == 0:
        return 'Số dư token = 0.'
    token_amount = int(amount_str)
    sig = execute_swap(token_addr, 'So11111111111111111111111111111111111111112', token_amount, keypair, unwrap_sol=True)
    if not sig:
        return 'Lỗi khi bán token.'
    initial_spent = trade['initial_sol_spent']
    new_balance = solana_client.get_balance()['result']['value']
    profit_lamports = new_balance - trade.get('balance_snapshot', new_balance)
    profit_sol = profit_lamports / 1_000_000_000
    pnl_pct = (profit_sol * 1_000_000_000 / initial_spent - 1) * 100 if initial_spent else 0
    del open_trades[symbol]
    return f'Đã đóng lệnh {symbol}. PNL: {pnl_pct:.2f}% ({profit_sol:.4f} SOL)'

def check_open_trades():
    for sym, trade in list(open_trades.items()):
        pair_id = trade['pair_id']
        try:
            data = requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair_id}', timeout=5).json()
        except Exception as e:
            logger.error(f'Lỗi tải giá cho {sym}: {e}')
            continue
        pair_info = data.get('pairs', [{}])[0]
        cur_price = None
        if pair_info.get('priceUsd'):
            try:
                cur_price = float(pair_info['priceUsd'])
            except:
                cur_price = None
        if cur_price is None:
            continue
        entry_price = trade['entry_price_usd']
        if not trade['trailing_active']:
            if cur_price > trade['peak_price']:
                trade['peak_price'] = cur_price
            if cur_price >= entry_price * 1.15:
                trade['trailing_active'] = True
                trade['stop_loss_price'] = entry_price  # dời SL về điểm mua [oai_citation:5‡dexscreener.com](https://dexscreener.com/solana/9qppy1kxrtfeewkfaysyhd7eu9glg5pgxdlkdl51p7ex#:~:text=5M%200.45)
                bot.send_message(chat_id=CHAT_ID, text=f'🚀 Giá {sym} tăng >=15%, dời SL về điểm mua.')
        else:
            if cur_price > trade['peak_price']:
                trade['peak_price'] = cur_price
                trade['stop_loss_price'] = trade['peak_price'] * 0.90
                sl_price = trade['stop_loss_price']
                bot.send_message(chat_id=CHAT_ID, text=f'Cập nhật trailing SL {sym}: {sl_price:.4f} USD')
        if cur_price <= trade['stop_loss_price']:
            result_msg = close_trade(sym)
            bot.send_message(chat_id=CHAT_ID, text=f'⚠️ Chạm SL {sym} - {result_msg}')

def buy_command(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text('Usage: /buy <token_symbol>')
        return
    symbol = context.args[0].upper()
    if symbol in open_trades:
        update.message.reply_text(f'Đã có lệnh mở cho {symbol}')
        return
    try:
        search_data = requests.get(f'https://api.dexscreener.com/latest/dex/search?q={symbol}', timeout=5).json()
    except Exception as e:
        update.message.reply_text('Lỗi tìm kiếm token.')
        return
    sol_pairs = [p for p in search_data.get('pairs', []) if p.get('chainId') == 'solana']
    if not sol_pairs:
        update.message.reply_text('Không tìm thấy token trên Solana.')
        return
    sol_pairs.sort(key=lambda x: float(x.get('volume', {}).get('usd', 0)), reverse=True)
    pair = sol_pairs[0]
    base = pair.get('baseToken', {}); quote = pair.get('quoteToken', {})
    if quote.get('symbol') in ['SOL','USDC','USDT']:
        token_addr = base.get('address'); token_sym = base.get('symbol')
    else:
        token_addr = quote.get('address'); token_sym = quote.get('symbol')
    if not token_addr or not token_sym:
        update.message.reply_text('Không xác định được token để mua.')
        return
    balance_sol = solana_client.get_balance()['result']['value']
    spend = int(balance_sol * 0.3)
    if spend < 1000000:
        update.message.reply_text('Số dư SOL không đủ.')
        return
    sig = execute_swap('So11111111111111111111111111111111111111112', token_addr, spend, keypair, unwrap_sol=True)
    if not sig:
        update.message.reply_text('Giao dịch mua thất bại.')
        return
    entry_price_usd = 0
    try:
        price_data = requests.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair.get('pairAddress')}", timeout=5).json()
        if price_data.get('pairs'):
            entry_price_usd = float(price_data['pairs'][0]['priceUsd'])
    except:
        entry_price_usd = 0
    open_trades[token_sym] = {
        'token_address': token_addr,
        'pair_id': pair.get('pairAddress'),
        'entry_price_usd': entry_price_usd,
        'stop_loss_price': entry_price_usd * 0.90,
        'peak_price': entry_price_usd,
        'trailing_active': False,
        'initial_sol_spent': spend,
        'balance_snapshot': solana_client.get_balance()['result']['value']
    }
    update.message.reply_text(f'Đã mua {token_sym}. Tx: {sig}')
    bot.send_message(chat_id=CHAT_ID, text=f'✅ Đã mua {token_sym} (lệnh thủ công).')

def sell_command(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text('Usage: /sell <token_symbol>')
        return
    symbol = context.args[0].upper()
    result_msg = close_trade(symbol)
    update.message.reply_text(result_msg)
    bot.send_message(chat_id=CHAT_ID, text=f'ℹ️ Đã đóng lệnh {symbol} (theo lệnh người dùng). {result_msg}')

bot = Bot(token=TELEGRAM_TOKEN)
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher
dispatcher.add_handler(CommandHandler('buy', buy_command))
dispatcher.add_handler(CommandHandler('sell', sell_command))

scheduler = BackgroundScheduler()
def scheduled_scan():
    tokens = scan_dexscreener()
    for token in tokens:
        sym = token['symbol']; pair_id = token['pair_id']
        if sym in open_trades or len(open_trades) >= max_parallel_trades:
            continue
        sol_price = get_solana_price_usd()
        lamports_test = usd_to_lamports(0.01, sol_price)
        if not lamports_test:
            continue
        info = get_token_info(pair_id)
        if not info:
            continue
        token_addr = info['address']; token_sym = info['symbol']
        sig_buy_test = execute_swap('So11111111111111111111111111111111111111112', token_addr, lamports_test, keypair, unwrap_sol=True)
        if not sig_buy_test:
            continue
        token_accs = solana_client.get_token_accounts_by_owner(wallet_address, mint=token_addr)
        vals = token_accs.get('result', {}).get('value', [])
        token_amount = 0
        if vals:
            token_acc = vals[0]['pubkey']
            bal = solana_client.get_token_account_balance(token_acc)
            amt_str = bal.get('result', {}).get('value', {}).get('amount')
            token_amount = int(amt_str) if amt_str else 0
        sig_sell_test = None
        if token_amount > 0:
            sig_sell_test = execute_swap(token_addr, 'So11111111111111111111111111111111111111112', token_amount, keypair, unwrap_sol=True)
        if not sig_sell_test:
            continue
        balance_sol = solana_client.get_balance()['result']['value']
        spend = int(balance_sol * 0.3)
        if spend < 1000000:
            continue
        sig_main = execute_swap('So11111111111111111111111111111111111111112', token_addr, spend, keypair, unwrap_sol=True)
        if not sig_main:
            continue
        entry_price_usd = 0
        try:
            pd = requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair_id}', timeout=5).json()
            if pd.get('pairs'):
                entry_price_usd = float(pd['pairs'][0]['priceUsd'])
        except:
            entry_price_usd = 0
        open_trades[token_sym] = {
            'token_address': token_addr,
            'pair_id': pair_id,
            'entry_price_usd': entry_price_usd,
            'stop_loss_price': entry_price_usd * 0.90,
            'peak_price': entry_price_usd,
            'trailing_active': False,
            'initial_sol_spent': spend,
            'balance_snapshot': solana_client.get_balance()['result']['value']
        }
        bot.send_message(chat_id=CHAT_ID, text=f'🔥 Phát hiện {token_sym} biến động mạnh - mở lệnh mua.')
        logger.info(f'Đã mở lệnh mua {token_sym} - Tx: {sig_main}')
    if open_trades:
        check_open_trades()

scheduler.add_job(scheduled_scan, 'interval', seconds=60)
scheduler.start()

logger.info('🤖 Bot giao dịch đang chạy...')
updater.start_polling()
updater.idle()
