import os
import base58
import requests
from solders.keypair import Keypair  # Keypair from solders (for Solana v0.30.2)
from solders.pubkey import Pubkey
from solana.publickey import PublicKey
from solana.rpc.api import Client
from solders.system_program import transfer, TransferParams
from solana.transaction import Transaction
from telegram.ext import Updater, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# Load required environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Environment variable TELEGRAM_TOKEN is not set.")
KEYPAIR_STR = os.environ.get("KEYPAIR")
if not KEYPAIR_STR:
    raise RuntimeError("Environment variable KEYPAIR (base58 secret key) is not set.")

# Initialize Solana RPC client (default to mainnet if SOLANA_RPC_URL not provided)
RPC_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
client = Client(RPC_URL)

# Load the keypair from base58 secret key string
try:
    # Decode base58 string to bytes to verify length
    secret_key_bytes = base58.b58decode(KEYPAIR_STR)
except Exception as e:
    raise RuntimeError(f"Invalid base58-encoded secret key: {e}")
if len(secret_key_bytes) != 64:
    raise RuntimeError("KEYPAIR base58 string is not a valid 64-byte secret key.")
try:
    signer = Keypair.from_base58_string(KEYPAIR_STR)
except Exception as e:
    raise RuntimeError(f"Failed to load Keypair from provided secret key: {e}")

# Determine target public key for transactions (if not provided, use the signer's pubkey)
TARGET_ADDRESS = os.environ.get("TARGET_ADDRESS")
if TARGET_ADDRESS:
    try:
        # Validate the target address using solana.publickey.PublicKey
        _ = PublicKey(TARGET_ADDRESS)
        target_pubkey = Pubkey.from_string(TARGET_ADDRESS)
    except Exception as e:
        raise RuntimeError(f"Invalid TARGET_ADDRESS provided: {e}")
else:
    target_pubkey = signer.pubkey()  # solders Pubkey of the signer (sending to self)

# Determine transfer amount (in SOL) and convert to lamports (1 SOL = 1e9 lamports)
TRANSFER_AMOUNT_SOL = float(os.environ.get("TRANSFER_AMOUNT_SOL", "0.001"))
LAMPORTS_PER_SOL = 1_000_000_000
lamports_amount = int(TRANSFER_AMOUNT_SOL * LAMPORTS_PER_SOL)
if lamports_amount <= 0:
    raise RuntimeError("TRANSFER_AMOUNT_SOL must be greater than 0.")
    
# Telegram command handler for /start
def start(update, context):
    """Respond to the /start command with a simple message."""
    update.message.reply_text("Bot is up and running.")

# Scheduled job to send a Solana transaction
def scheduled_job():
    """Periodic task to send a Solana transaction."""
    try:
        # Build the transaction with a simple SOL transfer instruction
        txn = Transaction()
        txn.add(
            transfer(
                TransferParams(
                    from_pubkey= signer.pubkey(),
                    to_pubkey= target_pubkey,
                    lamports= lamports_amount
                )
            )
        )
        # Send the transaction and retrieve the signature
        response = client.send_transaction(txn, signer)
        # The send_transaction returns a dictionary with a 'result' key if successful
        tx_signature = response.get("result") if isinstance(response, dict) else response
        print(f"Transaction sent successfully. Signature: {tx_signature}")
    except Exception as error:
        # Handle errors (e.g., RPC timeout, invalid key, insufficient funds, etc.)
        print(f"Error sending transaction: {error}")

# Set up Telegram bot with the legacy Updater/Dispatcher (python-telegram-bot v13.15)
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher
dispatcher.add_handler(CommandHandler("start", start))

# Set up the scheduler for periodic tasks
timezone_str = os.environ.get("TIMEZONE", "Asia/Ho_Chi_Minh")
scheduler = BackgroundScheduler(timezone=pytz.timezone(timezone_str))
# Schedule the job to run daily at 7:00 AM (customize as needed)
scheduler.add_job(scheduled_job, 'cron', hour=7, minute=0)

if __name__ == "__main__":
    # Start the scheduler and Telegram bot
    scheduler.start()
    updater.start_polling()
    print("Bot started. Waiting for commands...")
    try:
        updater.idle()  # Keep the bot running until interrupted
    finally:
        # Shutdown the scheduler on exit to clean up background threads
        scheduler.shutdown()
