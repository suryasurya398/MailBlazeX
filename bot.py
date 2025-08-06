import smtplib
import telebot
from telebot import types
import threading
import json
import os
from datetime import datetime
from itertools import cycle

# --- Configuration ---
# Telegram Bot Token - Replace with your bot's token
TELEGRAM_BOT_TOKEN = "8231712675:AAEESI5twmOENtG7vd4uzvHQvvHNMYEeZ2E"
# Admin User ID - Replace with your Telegram user ID
ADMIN_ID = 6563988669  # Replace with your actual Telegram User ID

# --- Globals & Persistence ---
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML')
user_states = {}  # In-memory store for multi-step conversations
CONFIG_FILE = 'bot_config.json'
AUTHORIZED_USERS_FILE = 'authorized_users.json'
STATS_FILE = 'bot_stats.json'

# --- Configuration and Data Loading ---
def load_config():
    """Loads configuration from a JSON file, ensuring essential keys exist."""
    defaults = {
        "GMAIL_ACCOUNTS": [],
        "DAILY_LIMIT_PER_USER": 500
    }
    if not os.path.exists(CONFIG_FILE):
        return defaults
    try:
        with open(CONFIG_FILE, 'r') as f:
            # Handle empty file case
            content = f.read()
            if not content:
                return defaults
            file_config = json.loads(content)
        # Update defaults with file content. This preserves defaults if keys are missing.
        defaults.update(file_config)
        return defaults
    except (json.JSONDecodeError, FileNotFoundError):
        return defaults # Return defaults on any error

def save_config(config_data):
    """Saves configuration to a JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config_data, f, indent=4)

def load_authorized_users():
    """Loads the set of authorized user IDs."""
    if not os.path.exists(AUTHORIZED_USERS_FILE):
        return {ADMIN_ID} # Admin is always authorized
    try:
        with open(AUTHORIZED_USERS_FILE, 'r') as f:
            return set(json.load(f))
    except (json.JSONDecodeError, FileNotFoundError):
        return {ADMIN_ID}

def save_authorized_users(user_ids):
    """Saves the set of authorized user IDs."""
    with open(AUTHORIZED_USERS_FILE, 'w') as f:
        json.dump(list(user_ids), f)

def load_stats():
    """Loads bot statistics from a JSON file."""
    if not os.path.exists(STATS_FILE):
        return {'total_sent': 0, 'user_daily_usage': {}}
    try:
        with open(STATS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {'total_sent': 0, 'user_daily_usage': {}}

def save_stats(stats):
    """Saves bot statistics to a JSON file."""
    with open(STATS_FILE, 'w') as f:
        json.dump(stats, f, indent=4)

# Initialize data from files
config = load_config()
authorized_users = load_authorized_users()
bot_stats = load_stats()

# --- Helper & Access Control Functions ---
def is_authorized(user_id):
    """Checks if a user is authorized to use the bot."""
    return user_id in authorized_users

def get_user_daily_sent(user_id):
    """Gets the number of emails a user has sent today."""
    today_str = datetime.utcnow().strftime('%Y-%m-%d')
    if today_str not in bot_stats.get('user_daily_usage', {}):
        bot_stats['user_daily_usage'] = {today_str: {}}
        save_stats(bot_stats)
    return bot_stats['user_daily_usage'].get(today_str, {}).get(str(user_id), 0)

def increment_user_sent_count(user_id, count=1):
    """Increments total and daily sent counts."""
    today_str = datetime.utcnow().strftime('%Y-%m-%d')
    user_id_str = str(user_id)
    bot_stats['total_sent'] = bot_stats.get('total_sent', 0) + count
    if today_str not in bot_stats.get('user_daily_usage', {}):
        bot_stats['user_daily_usage'][today_str] = {}
    current_daily = bot_stats['user_daily_usage'][today_str].get(user_id_str, 0)
    bot_stats['user_daily_usage'][today_str][user_id_str] = current_daily + count
    save_stats(bot_stats)

def send_email(credentials, to_addr, subject, message):
    """Sends a single email using specific credentials."""
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp_server:
            smtp_server.starttls()
            smtp_server.login(credentials['username'], credentials['password'])
            full_message = f"Subject: {subject}\n\n{message}"
            smtp_server.sendmail(credentials['username'], to_addr, full_message.encode('utf-8'))
        return True, None
    except Exception as e:
        return False, str(e)

# --- Main Email Sending Logic (Threaded & Round-Robin) ---
def execute_email_sending(chat_id, target_email, num_emails, subject, message_body):
    """Runs in a separate thread, sending emails via multiple accounts."""
    if not config['GMAIL_ACCOUNTS']:
        bot.send_message(chat_id, "<b>❌ Error:</b> No Gmail accounts configured by the admin.")
        return

    success_count, failure_count = 0, 0
    user_states[chat_id]['is_sending'] = True
    
    account_cycler = cycle(config['GMAIL_ACCOUNTS'])
    used_accounts = set()

    for i in range(num_emails):
        if not user_states.get(chat_id, {}).get('is_sending', False):
            bot.send_message(chat_id, f"🛑 <b>Process stopped.</b> Emails sent: {success_count}")
            break
        
        if get_user_daily_sent(chat_id) >= config['DAILY_LIMIT_PER_USER']:
            bot.send_message(chat_id, f"🚫 <b>Daily limit reached.</b> Stopping.")
            break

        current_credentials = next(account_cycler)
        used_accounts.add(current_credentials['username'])

        success, error = send_email(current_credentials, target_email, f"{subject} ({i+1}/{num_emails})", message_body)
        if success:
            success_count += 1
            increment_user_sent_count(chat_id)
            print(f"Chat {chat_id}: Email {i+1} sent via {current_credentials['username']}.")
        else:
            failure_count += 1
            print(f"Chat {chat_id}: Email {i+1} failed via {current_credentials['username']}: {error}")
            if "Authentication failed" in str(error):
                bot.send_message(chat_id, f"<b>❌ Critical Error with {current_credentials['username']}:</b> Auth failed. Skipping this account.")
    
    summary_message = (
        f"<b>✅ Task Finished</b>\n\n"
        f"Sent: {success_count}\nFailed: {failure_count}\n\n"
        f"Used {len(used_accounts)} Gmail account(s) to complete this task."
    )
    bot.send_message(chat_id, summary_message)
    
    if chat_id in user_states:
        del user_states[chat_id]

# --- Bot UI and Handlers ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ You are not authorized to use this bot. Please contact the admin.")
        return

    remaining = config['DAILY_LIMIT_PER_USER'] - get_user_daily_sent(message.chat.id)
    welcome_text = (
        f"👋 Welcome, {message.from_user.first_name}!\n\n"
        f"You can send <b>{remaining}</b> more emails today."
    )
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🚀 Start Sending Emails", callback_data='start_spam'))
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'start_spam')
def start_spam_callback(call):
    chat_id = call.message.chat.id
    if not is_authorized(chat_id):
        bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True)
        return
    
    if not config['GMAIL_ACCOUNTS']:
        bot.answer_callback_query(call.id, "No sending accounts are configured. Please contact the admin.", show_alert=True)
        return

    user_states[chat_id] = {}
    msg = bot.send_message(chat_id, "<b>Step 1:</b> Enter the recipient's email address.")
    bot.register_next_step_handler(msg, process_email_step)

# --- Conversation Steps ---
def process_email_step(message):
    chat_id = message.chat.id
    target_email = message.text
    if "@" not in target_email or "." not in target_email:
        msg = bot.reply_to(message, "❌ Invalid email. Please try again or type /cancel.")
        bot.register_next_step_handler(msg, process_email_step)
        return
    user_states[chat_id]['target_email'] = target_email
    msg = bot.send_message(chat_id, "<b>Step 2:</b> What is the subject?")
    bot.register_next_step_handler(msg, process_subject_step)

def process_subject_step(message):
    user_states[message.chat.id]['subject'] = message.text
    msg = bot.send_message(message.chat.id, "<b>Step 3:</b> What message to send?")
    bot.register_next_step_handler(msg, process_body_step)

def process_body_step(message):
    user_states[message.chat.id]['message_body'] = message.text
    msg = bot.send_message(message.chat.id, "<b>Step 4:</b> How many times? (Enter a number)")
    bot.register_next_step_handler(msg, process_count_step)

def process_count_step(message):
    chat_id = message.chat.id
    if not message.text.isdigit() or int(message.text) <= 0:
        msg = bot.reply_to(message, "❌ Please enter a positive number, or type /cancel.")
        bot.register_next_step_handler(msg, process_count_step)
        return
    
    num_emails = int(message.text)
    daily_sent = get_user_daily_sent(chat_id)
    limit = config['DAILY_LIMIT_PER_USER']
    
    if daily_sent >= limit:
        bot.send_message(chat_id, f"🚫 You have already reached your daily limit of {limit} emails.")
        return
        
    if (daily_sent + num_emails) > limit:
        bot.send_message(chat_id, f"🚫 This request exceeds your daily limit. You can only send <b>{limit - daily_sent}</b> more emails today. Please start again with /start.")
        return

    user_states[chat_id]['num_emails'] = num_emails
    state = user_states[chat_id]
    confirm_text = (
        "<b>Please confirm the details:</b>\n\n"
        f"<b>To:</b> {state['target_email']}\n<b>Subject:</b> {state['subject']}\n<b>Count:</b> {num_emails}\n\n"
        f"<b>Message:</b>\n<i>{state['message_body']}</i>"
    )
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    confirm_btn = types.InlineKeyboardButton("✅ Confirm & Send", callback_data='confirm_send')
    cancel_btn = types.InlineKeyboardButton("❌ Cancel", callback_data='cancel_process')
    markup.add(confirm_btn, cancel_btn)
    bot.send_message(chat_id, confirm_text, reply_markup=markup)

# --- Action Callbacks ---
@bot.callback_query_handler(func=lambda call: call.data == 'confirm_send')
def confirm_send_callback(call):
    chat_id = call.message.chat.id
    state = user_states.get(chat_id)
    
    if not state or 'num_emails' not in state:
        bot.edit_message_text("Something went wrong, please start over.", chat_id, call.message.message_id)
        return

    bot.edit_message_text(f"🚀 <b>Starting to send {state['num_emails']} emails...</b>", chat_id, call.message.message_id)
    
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🛑 Stop Sending", callback_data='stop_sending'))
    bot.send_message(chat_id, "You can stop the process at any time below.", reply_markup=markup)

    threading.Thread(target=execute_email_sending, args=(
        chat_id, state['target_email'], state['num_emails'], state['subject'], state['message_body']
    )).start()

@bot.callback_query_handler(func=lambda call: call.data == 'stop_sending')
def stop_sending_callback(call):
    chat_id = call.message.chat.id
    if user_states.get(chat_id, {}).get('is_sending'):
        user_states[chat_id]['is_sending'] = False
        bot.edit_message_text("🛑 Stop command received! Halting...", chat_id, call.message.message_id)
    else:
        bot.edit_message_text("No active process to stop.", chat_id, call.message.message_id)

@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    if message.chat.id in user_states: del user_states[message.chat.id]
    bot.send_message(message.chat.id, "Operation cancelled.")

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_process')
def cancel_process_callback(call):
    if call.message.chat.id in user_states: del user_states[call.message.chat.id]
    bot.edit_message_text("Operation cancelled.", call.message.chat.id, call.message.message_id)

# --- Admin Panel ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID: return
    
    admin_help_text = (
        "<b>👑 Admin Commands 👑</b>\n\n"
        "<b>User Management:</b>\n"
        "<code>/grant &lt;user_id&gt;</code> - Grant access.\n"
        "<code>/revoke &lt;user_id&gt;</code> - Revoke access.\n\n"
        "<b>Gmail Management:</b>\n"
        "<code>/addgmails</code> - Start bulk-adding accounts.\n"
        "<code>/listgmails</code> - List all accounts.\n"
        "<code>/removegmail &lt;number&gt;</code> - Remove an account.\n\n"
        "<b>Bot Configuration:</b>\n"
        "<code>/setlimit &lt;number&gt;</code> - Set daily user limit.\n"
        "<code>/stats</code> - View bot statistics.\n"
        "<code>/broadcast</code> - Send a message to all users."
    )
    bot.send_message(message.chat.id, admin_help_text)

# --- Admin Command Handlers ---
@bot.message_handler(commands=['grant'])
def grant_access_command(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        user_id = int(message.text.split()[1])
        authorized_users.add(user_id)
        save_authorized_users(authorized_users)
        bot.reply_to(message, f"✅ Access granted to user <code>{user_id}</code>.")
    except (IndexError, ValueError):
        bot.reply_to(message, "Usage: <code>/grant &lt;user_id&gt;</code>")

@bot.message_handler(commands=['revoke'])
def revoke_access_command(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        user_id = int(message.text.split()[1])
        if user_id == ADMIN_ID:
            bot.reply_to(message, "⛔ Cannot revoke admin's own access.")
            return
        authorized_users.discard(user_id)
        save_authorized_users(authorized_users)
        bot.reply_to(message, f"❌ Access revoked for user <code>{user_id}</code>.")
    except (IndexError, ValueError):
        bot.reply_to(message, "Usage: <code>/revoke &lt;user_id&gt;</code>")

@bot.message_handler(commands=['addgmails'])
def add_gmails_command(message):
    if message.from_user.id != ADMIN_ID: return
    prompt_text = (
        "Please send the Gmail accounts as a list.\n"
        "Use the format <code>email:password</code> for each line.\n\n"
        "<b>Example:</b>\n"
        "<code>example1@gmail.com:yourpassword1</code>\n"
        "<code>example2@gmail.com:yourpassword2</code>"
    )
    msg = bot.send_message(message.chat.id, prompt_text)
    bot.register_next_step_handler(msg, process_bulk_add_gmail)

def process_bulk_add_gmail(message):
    if message.from_user.id != ADMIN_ID: return
    
    lines = message.text.strip().split('\n')
    added_count = 0
    duplicate_count = 0
    invalid_lines = []
    
    existing_emails = {acc['username'] for acc in config['GMAIL_ACCOUNTS']}

    for i, line in enumerate(lines):
        parts = line.strip().split(':', 1)
        if len(parts) == 2:
            username, password = parts[0].strip(), parts[1].strip()
            if username in existing_emails:
                duplicate_count += 1
            else:
                config['GMAIL_ACCOUNTS'].append({'username': username, 'password': password})
                existing_emails.add(username)
                added_count += 1
        else:
            invalid_lines.append(i + 1)
    
    if added_count > 0:
        save_config(config)

    summary_text = f"<b>Bulk Add Summary:</b>\n\n"
    summary_text += f"✅ Successfully added: {added_count}\n"
    summary_text += f"⚠️ Duplicates skipped: {duplicate_count}\n"
    if invalid_lines:
        summary_text += f"❌ Invalid lines: {len(invalid_lines)} (Lines: {', '.join(map(str, invalid_lines))})"
    
    bot.reply_to(message, summary_text)

@bot.message_handler(commands=['listgmails'])
def list_gmails_command(message):
    if message.from_user.id != ADMIN_ID: return
    if not config['GMAIL_ACCOUNTS']:
        bot.reply_to(message, "No Gmail accounts are configured.")
        return

    response = "<b>Configured Gmail Accounts:</b>\n\n"
    for i, acc in enumerate(config['GMAIL_ACCOUNTS']):
        response += f"{i+1}. <code>{acc['username']}</code>\n"
    response += "\nTo remove an account, use <code>/removegmail &lt;number&gt;</code>."
    bot.send_message(message.chat.id, response)

@bot.message_handler(commands=['removegmail'])
def remove_gmail_command(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        index = int(message.text.split()[1]) - 1
        if 0 <= index < len(config['GMAIL_ACCOUNTS']):
            removed_acc = config['GMAIL_ACCOUNTS'].pop(index)
            save_config(config)
            bot.reply_to(message, f"✅ Account <code>{removed_acc['username']}</code> has been removed.")
        else:
            bot.reply_to(message, "❌ Invalid number. Check <code>/listgmails</code>.")
    except (IndexError, ValueError):
        bot.reply_to(message, "Usage: <code>/removegmail &lt;number&gt;</code>")

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != ADMIN_ID: return
    active_processes = sum(1 for state in user_states.values() if state.get('is_sending'))
    stats_text = (
        "<b>📊 Bot Statistics</b>\n\n"
        f"<b>Authorized Users:</b> {len(authorized_users)}\n"
        f"<b>Active Processes:</b> {active_processes}\n"
        f"<b>Total Emails Sent:</b> {bot_stats.get('total_sent', 0)}\n"
        f"<b>Configured Gmails:</b> {len(config['GMAIL_ACCOUNTS'])}\n"
        f"<b>Daily Limit/User:</b> {config['DAILY_LIMIT_PER_USER']}"
    )
    bot.send_message(message.chat.id, stats_text)

@bot.message_handler(commands=['setlimit'])
def set_limit_command(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        limit = int(message.text.split()[1])
        if limit <= 0:
            bot.reply_to(message, "❌ Limit must be a positive number.")
            return
        config['DAILY_LIMIT_PER_USER'] = limit
        save_config(config)
        bot.reply_to(message, f"✅ Daily limit updated to: {limit}")
    except (IndexError, ValueError):
        bot.reply_to(message, "Usage: <code>/setlimit &lt;number&gt;</code>")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    if message.from_user.id != ADMIN_ID: return
    msg = bot.reply_to(message, "Please send the message you want to broadcast to all authorized users. You can send text, images, etc.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    if message.from_user.id != ADMIN_ID: return
    sent_count, failed_count = 0, 0
    bot.send_message(message.chat.id, f"Broadcasting to {len(authorized_users)} users...")
    for user_id in authorized_users:
        try:
            bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
            sent_count += 1
        except Exception as e:
            failed_count += 1
            print(f"Broadcast failed for {user_id}: {e}")
    bot.send_message(message.chat.id, f"Broadcast finished. Sent: {sent_count}, Failed: {failed_count}")

# --- Main Execution ---
if __name__ == "__main__":
    print("Bot is running...")
    print(f"Admin ID: {ADMIN_ID}")
    if not config.get('GMAIL_ACCOUNTS'):
        print("WARNING: No Gmail accounts are configured. Use the bot's admin panel to add one.")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
