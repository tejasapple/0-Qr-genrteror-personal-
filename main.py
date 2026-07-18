import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import pymongo
import qrcode
import io
import uuid
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import csv
import os
from bson.objectid import ObjectId

# ================= कॉन्फ़िगरेशन =================
BOT_TOKEN = "8740636028:AAFKOpliANI816prOplKF1FB9qxF7TkKoG8"
MONGO_URI = "mongodb+srv://Tejas7xx:mrxtejas7@cluster0.akhlgjf.mongodb.net/?appName=Cluster0"
OWNER_ID = 8702240402 # अपनी टेलीग्राम यूजर आईडी यहाँ डालें (नंबर में)

LOG_GROUP_ID = OWNER_ID 

bot = telebot.TeleBot(BOT_TOKEN)

# ================= डेटाबेस सेटअप =================
client = pymongo.MongoClient(MONGO_URI)
db = client['upi_master_bot']

# Collections (Tables)
upi_col = db['upi_ids']        
admins_col = db['admins']      
tx_col = db['transactions']    
saved_qrs_col = db['saved_qrs']

# Owner को डिफ़ॉल्ट एडमिन बनाना (Regular)
if not admins_col.find_one({"user_id": OWNER_ID}):
    admins_col.insert_one({
        "user_id": OWNER_ID, 
        "name": "Owner", 
        "use_tr": True, 
        "is_sharing": False, 
        "last_cleared": datetime.now()
    })

# ================= हेल्पर फंक्शन्स =================

def is_admin(user_id):
    return user_id == OWNER_ID or admins_col.find_one({"user_id": user_id}) is not None

def get_next_upi(group):
    query = {} if group == "All" else {"group": group}
    upi = upi_col.find_one(query, sort=[("last_used", 1)])
    if upi:
        upi_col.update_one({"_id": upi["_id"]}, {"$set": {"last_used": datetime.now()}})
    return upi

def generate_qr_image(upi_id, name, amount, tx_id, logo_file_id=None, use_tr=True):
    # 'tn' पैरामीटर टाइप किए हुए मैसेज (Note) के लिए होता है
    if use_tr:
        upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&tr={tx_id}&tn={tx_id}"
    else:
        upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}"
        
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    
    w, h = img.size
    banner_height = 60
    new_img = Image.new('RGB', (w, h + banner_height), 'white')
    new_img.paste(img, (0, 0))
    
    draw = ImageDraw.Draw(new_img)
    display_text = f"TXN: {tx_id}" if use_tr else f"Amount: {amount} (No TXN ID)"
    draw.text((15, h + 20), display_text, fill="black")
    
    if logo_file_id:
        try:
            file_info = bot.get_file(logo_file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            logo = Image.open(io.BytesIO(downloaded_file)).convert("RGBA")
            logo.thumbnail((50, 50))
            new_img.paste(logo, (w - 60, h + 5), logo)
        except Exception as e:
            print(f"Logo error: {e}")
            
    bio = io.BytesIO()
    new_img.save(bio, 'PNG')
    bio.seek(0)
    return bio

# ================= मेन्यू और कीबोर्ड्स =================

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("💸 Generate QR"), KeyboardButton("🖼 My Saved QRs"),
        KeyboardButton("📊 Status/Stats"), KeyboardButton("🛠 My Settings")
    )
    markup.add(KeyboardButton("⚙️ Admin Panel"))
    return markup

# ================= मुख्य कमांड्स =================

@bot.message_handler(commands=['start'])
def start_cmd(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "🚫 आप इस बोट को इस्तेमाल करने के लिए ऑथराइज्ड नहीं हैं।")
        return
    bot.send_message(message.chat.id, "🤖 Welcome to Master UPI Bot!\nसिस्टम रेडी है।", reply_markup=main_menu())

@bot.message_handler(commands=['export'])
def export_data(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "🚫 यह कमांड सिर्फ ओनर इस्तेमाल कर सकता है।")
    
    bot.send_message(message.chat.id, "⏳ डेटा एक्सपोर्ट किया जा रहा है...")
    transactions = tx_col.find({"status": "done"})
    filename = f"payments_{datetime.now().strftime('%Y%m%d')}.csv"
    
    with open(filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["Time", "TXN ID", "Amount", "Group", "UPI ID", "Admin ID"])
        for t in transactions:
            writer.writerow([t.get('time', ''), t.get('tx_id'), t.get('amount'), t.get('group'), t.get('upi_id'), t.get('admin_id')])
            
    with open(filename, 'rb') as file:
        bot.send_document(message.chat.id, file, caption="📊 आपके सभी सफल पेमेंट्स की लिस्ट।")
    os.remove(filename)

# ================= 1. QR जनरेशन फ्लो =================

@bot.message_handler(func=lambda msg: msg.text == "💸 Generate QR")
def generate_qr_start(message):
    if not is_admin(message.from_user.id): return
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🏦 Only Bank", callback_data="grp_Bank"),
        InlineKeyboardButton("👛 Only Wallet", callback_data="grp_Wallet"),
        InlineKeyboardButton("🌐 All", callback_data="grp_All")
    )
    bot.send_message(message.chat.id, "किस ग्रुप से UPI ID लेनी है?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("grp_"))
def ask_amount(call):
    group = call.data.split("_")[1]
    markup = InlineKeyboardMarkup(row_width=2)
    amounts = [200, 499, 999, 1999]
    buttons = [InlineKeyboardButton(f"₹{amt}", callback_data=f"amt_{amt}_{group}") for amt in amounts]
    markup.add(*buttons)
    markup.add(InlineKeyboardButton("✍️ Custom Amount", callback_data=f"amt_custom_{group}"))
    
    bot.edit_message_text(f"Group: {group}\nअमाउंट सेलेक्ट करें:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("amt_"))
def process_amount(call):
    data = call.data.split("_")
    amount = data[1]
    group = data[2]
    
    if amount == "custom":
        msg = bot.send_message(call.message.chat.id, "कृपया अमाउंट टाइप करके भेजें (Ex: 1500):")
        bot.register_next_step_handler(msg, process_custom_amount, group)
    else:
        create_and_send_qr(call.message, int(amount), group, call.from_user.id)

def process_custom_amount(message, group):
    try:
        amount = int(message.text)
        create_and_send_qr(message, amount, group, message.from_user.id)
    except ValueError:
        bot.send_message(message.chat.id, "❌ अमान्य अमाउंट! केवल नंबर दर्ज करें।")

def create_and_send_qr(message, amount, group, admin_id):
    upi = get_next_upi(group)
    if not upi:
        bot.send_message(message.chat.id, f"❌ {group} ग्रुप में कोई UPI ID नहीं मिली!")
        return
        
    admin_data = admins_col.find_one({"user_id": admin_id})
    use_tr = admin_data.get("use_tr", True)
    logo_file_id = admin_data.get("logo_id", None)
        
    tx_id = "TXN" + str(uuid.uuid4().hex)[:10].upper()
    qr_img = generate_qr_image(upi['upi_id'], upi['name'], amount, tx_id, logo_file_id, use_tr)
    
    tx_col.insert_one({
        "tx_id": tx_id, "amount": amount, "group": group, "upi_id": upi['upi_id'], 
        "admin_id": admin_id, "status": "pending", "time": datetime.now()
    })
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Payment Done", callback_data=f"done_{tx_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{tx_id}")
    )
    markup.add(InlineKeyboardButton("🔄 Regenerate (Next UPI)", callback_data=f"regen_{tx_id}"))
    
    caption = f"🧾 **Payment QR**\n\n💸 Amount: ₹{amount}\n🆔 TXN ID: `{tx_id}`\n🏦 UPI: `{upi['upi_id']}`"
    bot.send_photo(message.chat.id, qr_img, caption=caption, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith(("done_", "cancel_", "regen_")))
def handle_tx_action(call):
    action, tx_id = call.data.split("_")
    tx = tx_col.find_one({"tx_id": tx_id})
    if not tx: return bot.answer_callback_query(call.id, "Transaction not found!")

    if action == "done":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "done"}})
        bot.edit_message_caption("✅ **PAYMENT RECEIVED & SAVED!**\n\n" + call.message.caption, 
                                 call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                                 
        admin_info = admins_col.find_one({"user_id": call.from_user.id})
        admin_name = admin_info.get('name', call.from_user.id) if admin_info else call.from_user.id
        
        log_msg = (
            f"🟢 **NEW PAYMENT CONFIRMED**\n\n"
            f"👤 **Admin:** {admin_name} (`{call.from_user.id}`)\n"
            f"💸 **Amount:** ₹{tx['amount']}\n"
            f"🆔 **TXN ID:** `{tx_id}`\n"
            f"🏦 **UPI ID:** `{tx['upi_id']}`\n"
            f"📁 **Group:** {tx['group']}"
        )
        try:
            bot.send_message(LOG_GROUP_ID, log_msg, parse_mode="Markdown")
        except Exception as e:
            print("Log channel error:", e)
            
    elif action == "cancel":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "cancelled"}})
        bot.edit_message_caption("❌ **PAYMENT CANCELLED**\n\n" + call.message.caption, 
                                 call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                                 
    elif action == "regen":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "cancelled"}})
        bot.delete_message(call.message.chat.id, call.message.message_id)
        create_and_send_qr(call.message, tx['amount'], tx['group'], call.from_user.id)

# ================= 2. My Saved QRs (पर्सनल क्यूआर) =================

@bot.message_handler(func=lambda msg: msg.text == "🖼 My Saved QRs")
def my_qrs_menu(message):
    if not is_admin(message.from_user.id): return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📂 My QRs", callback_data="saved_list"),
        InlineKeyboardButton("➕ Add New QR", callback_data="saved_add")
    )
    bot.send_message(message.chat.id, "🖼 **My Saved QRs Menu:**", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "saved_add")
def saved_add_step1(call):
    msg = bot.send_message(call.message.chat.id, "📸 कृपया QR कोड की Photo भेजें:")
    bot.register_next_step_handler(msg, saved_add_step2)

def saved_add_step2(message):
    if not message.photo:
        return bot.send_message(message.chat.id, "❌ आपने फोटो नहीं भेजी। प्रक्रिया रद्द कर दी गई।")
    photo_id = message.photo[-1].file_id
    msg = bot.send_message(message.chat.id, "📝 इस QR को किस नाम से सेव करना है? (Ex: Shabnam):")
    bot.register_next_step_handler(msg, saved_add_step3, photo_id)

def saved_add_step3(message, photo_id):
    name = message.text
    msg = bot.send_message(message.chat.id, "🏦 इस QR की UPI ID भी भेजें:")
    bot.register_next_step_handler(msg, saved_add_final, photo_id, name)

def saved_add_final(message, photo_id, name):
    upi_id = message.text
    saved_qrs_col.insert_one({
        "admin_id": message.from_user.id,
        "photo_id": photo_id,
        "name": name,
        "upi_id": upi_id
    })
    bot.send_message(message.chat.id, f"✅ QR '{name}' सफलतापूर्वक सेव हो गया!")

@bot.callback_query_handler(func=lambda call: call.data == "saved_list")
def saved_list_show(call):
    qrs = list(saved_qrs_col.find({"admin_id": call.from_user.id}))
    if not qrs:
        return bot.answer_callback_query(call.id, "❌ कोई सेव्ड QR नहीं मिला!", show_alert=True)
        
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(qr['name'], callback_data=f"show_qr_{str(qr['_id'])}") for qr in qrs]
    markup.add(*buttons)
    bot.edit_message_text("👇 अपने सेव्ड QR चुनें:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("show_qr_"))
def saved_show_specific(call):
    qr_id = call.data.split("show_qr_")[1]
    qr = saved_qrs_col.find_one({"_id": ObjectId(qr_id)})
    if qr:
        caption = f"👤 **Name:** {qr['name']}\n🏦 **UPI ID:** `{qr['upi_id']}`"
        bot.send_photo(call.message.chat.id, qr['photo_id'], caption=caption, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
    else:
        bot.answer_callback_query(call.id, "QR not found!")


# ================= 3. माय सेटिंग्स (Logo & TXN ID Toggle) =================

@bot.message_handler(func=lambda msg: msg.text == "🛠 My Settings")
def my_settings(message):
    admin_id = message.from_user.id
    if not is_admin(admin_id): return
    
    admin_data = admins_col.find_one({"user_id": admin_id})
    use_tr = admin_data.get("use_tr", True)
    status_text = "ON 🟢" if use_tr else "OFF 🔴"
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(f"🔄 Pre-filled Message / TXN (Current: {status_text})", callback_data="set_toggle_tr"),
        InlineKeyboardButton("🖼 Upload Custom Logo", callback_data="set_logo"),
        InlineKeyboardButton("🗑 Remove Logo", callback_data="set_rm_logo")
    )
    bot.send_message(message.chat.id, "🛠 **अपनी पर्सनल सेटिंग्स चुनें:**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_"))
def handle_settings(call):
    admin_id = call.from_user.id
    action = call.data
    
    if action == "set_toggle_tr":
        admin_data = admins_col.find_one({"user_id": admin_id})
        new_status = not admin_data.get("use_tr", True)
        admins_col.update_one({"user_id": admin_id}, {"$set": {"use_tr": new_status}})
        bot.answer_callback_query(call.id, "✅ TXN/Message Prefill Status Updated!")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        my_settings(call.message) 
        
    elif action == "set_logo":
        msg = bot.send_message(call.message.chat.id, "कृपया अपना लोगो (Photo) भेजें।\n(नॉर्मल फोटो की तरह भेजें, डॉक्यूमेंट नहीं):")
        bot.register_next_step_handler(msg, process_new_logo)
        
    elif action == "set_rm_logo":
        admins_col.update_one({"user_id": admin_id}, {"$unset": {"logo_id": ""}})
        bot.answer_callback_query(call.id, "✅ Logo Removed!", show_alert=True)

def process_new_logo(message):
    if not message.photo:
        return bot.send_message(message.chat.id, "❌ आपने फोटो नहीं भेजी।")
    logo_id = message.photo[-1].file_id
    admins_col.update_one({"user_id": message.from_user.id}, {"$set": {"logo_id": logo_id}})
    bot.send_message(message.chat.id, "✅ आपका कस्टम लोगो सेव हो गया है!")


# ================= 4. स्टेटस और एनालिटिक्स (पार्टनर शेयरिंग के साथ) =================

@bot.message_handler(func=lambda msg: msg.text == "📊 Status/Stats")
def show_stats_menu(message):
    user_id = message.from_user.id
    if not is_admin(user_id): return
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("Today", callback_data="stat_today_self"),
        InlineKeyboardButton("Yesterday", callback_data="stat_yesterday_self"),
        InlineKeyboardButton("Last 3 Days", callback_data="stat_3d_self"),
        InlineKeyboardButton("Last 7 Days", callback_data="stat_7d_self"),
        InlineKeyboardButton("This Month", callback_data="stat_month_self"),
        InlineKeyboardButton("Total All", callback_data="stat_all_self")
    )
    
    if user_id == OWNER_ID:
        markup.add(InlineKeyboardButton("👑 Owner View: ALL Admins Data 👑", callback_data="stat_owner_menu"))
        
    bot.send_message(message.chat.id, "📊 **रिपोर्ट का समय चुनें (Your Personal Stats):**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "stat_owner_menu")
def owner_stats_menu(call):
    if call.from_user.id != OWNER_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("Today (All)", callback_data="stat_today_all"),
        InlineKeyboardButton("Yesterday (All)", callback_data="stat_yesterday_all"),
        InlineKeyboardButton("Last 3 Days (All)", callback_data="stat_3d_all"),
        InlineKeyboardButton("Last 7 Days (All)", callback_data="stat_7d_all"),
        InlineKeyboardButton("This Month (All)", callback_data="stat_month_all"),
        InlineKeyboardButton("Total (All)", callback_data="stat_all_all")
    )
    bot.edit_message_text("📊 **रिपोर्ट का समय चुनें (ALL ADMINS DATA):**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("stat_") and call.data != "stat_owner_menu")
def process_stats(call):
    parts = call.data.split("_")
    period = parts[1]
    scope = parts[2] 
    admin_id = call.from_user.id
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    query = {"status": "done"}
    admin_data = admins_col.find_one({"user_id": admin_id})
    is_sharing = admin_data.get("is_sharing", False)
    
    if scope == "self":
        query["admin_id"] = admin_id
        header_text = "👤 **Your Personal Stats**"
    else:
        header_text = "👥 **Global Stats (All Admins)**"
        
    if period == "today":
        query["time"] = {"$gte": today_start}
        time_text = "Today"
    elif period == "yesterday":
        query["time"] = {"$gte": today_start - timedelta(days=1), "$lt": today_start}
        time_text = "Yesterday"
    elif period == "3d":
        query["time"] = {"$gte": today_start - timedelta(days=3)}
        time_text = "Last 3 Days"
    elif period == "7d":
        query["time"] = {"$gte": today_start - timedelta(days=7)}
        time_text = "Last 7 Days"
    elif period == "month":
        query["time"] = {"$gte": now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)}
        time_text = "This Month"
    else: 
        time_text = "Total All Time"

    transactions = list(tx_col.find(query))
    total_amount = sum(t['amount'] for t in transactions)
    total_txns = len(transactions)
    
    stats_msg = f"{header_text}\n🗓 **Period:** {time_text}\n\n💸 **Total Received:** ₹{total_amount}\n🔢 **Total TXNs:** {total_txns}\n"
    
    # अगर यह शेयरिंग एडमिन है (30%), तो उसका खुद का प्रॉफिट दिखाएं
    if scope == "self" and is_sharing:
        my_share = total_amount * 0.30
        owner_share = total_amount * 0.70
        stats_msg += f"\n💰 **Your Profit (30%):** ₹{my_share:.2f}"
        stats_msg += f"\n👑 **Owner's Cut (70%):** ₹{owner_share:.2f}\n"

        # Uncleared Balance Calculator
        last_cleared = admin_data.get("last_cleared", datetime.min)
        uncleared_txns = list(tx_col.find({"status": "done", "admin_id": admin_id, "time": {"$gt": last_cleared}}))
        uncleared_total = sum(t['amount'] for t in uncleared_txns)
        stats_msg += f"\n⚠️ **Pending Balance (Since Last Clear):**"
        stats_msg += f"\nTotal: ₹{uncleared_total}"
        stats_msg += f"\nYour Balance to Keep: ₹{uncleared_total * 0.30:.2f}"
    
    if scope == "all":
        stats_msg += "\n**Admin Wise Breakdown:**\n"
        admins = admins_col.find()
        for admin in admins:
            admin_txns = [t for t in transactions if t.get('admin_id') == admin['user_id']]
            if admin_txns:
                admin_total = sum(t['amount'] for t in admin_txns)
                stats_msg += f"🔸 {admin.get('name', admin['user_id'])}: ₹{admin_total} ({len(admin_txns)} txn)\n"

    bot.send_message(call.message.chat.id, stats_msg, parse_mode="Markdown")
    bot.answer_callback_query(call.id)

# ================= 5. एडमिन पैनल & Partner Management =================

@bot.message_handler(func=lambda msg: msg.text == "⚙️ Admin Panel")
def admin_panel(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "🚫 सिर्फ Owner ही इसे खोल सकता है।")
        
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Add UPI", "➕ Add Admin")
    markup.add("💰 Partner Balances", "🗑 Remove Manual TXN")
    markup.add("⬅️ Back to Main")
    bot.send_message(message.chat.id, "⚙️ Admin Panel में आपका स्वागत है।", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "⬅️ Back to Main")
def back_main(message):
    bot.send_message(message.chat.id, "Main Menu:", reply_markup=main_menu())

# --- Add UPI Flow ---
@bot.message_handler(func=lambda msg: msg.text == "➕ Add UPI")
def add_upi_start(message):
    if message.from_user.id != OWNER_ID: return
    msg = bot.send_message(message.chat.id, "UPI ID भेजें (Ex: number@paytm):")
    bot.register_next_step_handler(msg, step_upi_name)

def step_upi_name(message):
    upi_id = message.text
    msg = bot.send_message(message.chat.id, "इस UPI का नाम बताएं (Ex: HDFC Account):")
    bot.register_next_step_handler(msg, step_upi_group, upi_id)

def step_upi_group(message, upi_id):
    name = message.text
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("Bank", "Wallet")
    msg = bot.send_message(message.chat.id, "यह बैंक है या वॉलेट?", reply_markup=markup)
    bot.register_next_step_handler(msg, save_upi, upi_id, name)

def save_upi(message, upi_id, name):
    group = message.text
    upi_col.insert_one({
        "upi_id": upi_id, "name": name, "group": group, 
        "last_used": datetime.now()
    })
    bot.send_message(message.chat.id, f"✅ UPI ID Successfully Added!\nUPI: {upi_id}\nGroup: {group}", reply_markup=main_menu())

# --- Add Admin Flow (Partner System Upgraded) ---
@bot.message_handler(func=lambda msg: msg.text == "➕ Add Admin")
def add_admin_start(message):
    if message.from_user.id != OWNER_ID: return
    msg = bot.send_message(message.chat.id, "नए एडमिन की Telegram User ID भेजें:")
    bot.register_next_step_handler(msg, step_admin_name)

def step_admin_name(message):
    try:
        new_admin_id = int(message.text)
        msg = bot.send_message(message.chat.id, "नए एडमिन का नाम बताएं:")
        bot.register_next_step_handler(msg, step_admin_type, new_admin_id)
    except ValueError:
        bot.send_message(message.chat.id, "❌ अमान्य आईडी!", reply_markup=main_menu())

def step_admin_type(message, new_admin_id):
    name = message.text
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("Regular (No Share)", "Sharing (30% Admin / 70% Owner)")
    msg = bot.send_message(message.chat.id, "यह एडमिन किस प्रकार का है?", reply_markup=markup)
    bot.register_next_step_handler(msg, save_admin, new_admin_id, name)

def save_admin(message, new_admin_id, name):
    is_sharing = "Sharing" in message.text
    if admins_col.find_one({"user_id": new_admin_id}):
        bot.send_message(message.chat.id, "❌ यह एडमिन पहले से मौजूद है!", reply_markup=main_menu())
    else:
        admins_col.insert_one({
            "user_id": new_admin_id, 
            "name": name, 
            "use_tr": True,
            "is_sharing": is_sharing,
            "last_cleared": datetime.now()
        })
        bot.send_message(message.chat.id, f"✅ Admin Added!\nName: {name}\nType: {'Sharing (30%)' if is_sharing else 'Regular'}", reply_markup=main_menu())

# --- Partner Balance / Clear System ---
@bot.message_handler(func=lambda msg: msg.text == "💰 Partner Balances")
def partner_balances(message):
    if message.from_user.id != OWNER_ID: return
    
    partners = list(admins_col.find({"is_sharing": True}))
    if not partners:
        return bot.send_message(message.chat.id, "❌ आपका कोई Sharing Partner नहीं है।")
        
    markup = InlineKeyboardMarkup(row_width=1)
    for partner in partners:
        last_cleared = partner.get("last_cleared", datetime.min)
        # पिछले क्लियर के बाद का टोटल
        uncleared_txns = list(tx_col.find({"status": "done", "admin_id": partner["user_id"], "time": {"$gt": last_cleared}}))
        uncleared_total = sum(t['amount'] for t in uncleared_txns)
        
        btn_text = f"👤 {partner['name']} | Pending Total: ₹{uncleared_total}"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"partner_{partner['user_id']}"))
        
    bot.send_message(message.chat.id, "👥 **पार्टनर बैलेंस मैनेज करें:**\n(किसी भी पार्टनर का हिसाब क्लियर करने के लिए उस पर क्लिक करें)", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("partner_"))
def partner_details(call):
    if call.from_user.id != OWNER_ID: return
    partner_id = int(call.data.split("_")[1])
    partner = admins_col.find_one({"user_id": partner_id})
    
    last_cleared = partner.get("last_cleared", datetime.min)
    uncleared_txns = list(tx_col.find({"status": "done", "admin_id": partner_id, "time": {"$gt": last_cleared}}))
    uncleared_total = sum(t['amount'] for t in uncleared_txns)
    
    msg = (f"👤 **Partner:** {partner['name']}\n"
           f"💸 **Total Uncleared Amount:** ₹{uncleared_total}\n\n"
           f"🔹 **Partner Share (30%):** ₹{uncleared_total * 0.30:.2f}\n"
           f"👑 **Your Share (70%):** ₹{uncleared_total * 0.70:.2f}\n")
           
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Mark as Cleared / Reset", callback_data=f"clear_bal_{partner_id}"))
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("clear_bal_"))
def clear_partner_balance(call):
    if call.from_user.id != OWNER_ID: return
    partner_id = int(call.data.split("clear_bal_")[1])
    
    admins_col.update_one({"user_id": partner_id}, {"$set": {"last_cleared": datetime.now()}})
    bot.answer_callback_query(call.id, "✅ बैलेंस सफलतापूर्वक क्लियर (0) कर दिया गया है!", show_alert=True)
    bot.delete_message(call.message.chat.id, call.message.message_id)

# --- Remove Manual Transaction ---
@bot.message_handler(func=lambda msg: msg.text == "🗑 Remove Manual TXN")
def remove_txn_start(message):
    if message.from_user.id != OWNER_ID: return
    msg = bot.send_message(message.chat.id, "जिस पेमेंट को हटाना है उसकी TXN ID भेजें:")
    bot.register_next_step_handler(msg, delete_txn)

def delete_txn(message):
    tx_id = message.text
    result = tx_col.delete_one({"tx_id": tx_id})
    if result.deleted_count > 0:
        bot.send_message(message.chat.id, f"✅ TXN {tx_id} डिलीट कर दिया गया!")
    else:
        bot.send_message(message.chat.id, "❌ यह TXN ID नहीं मिली।")

# ================= BOT RUNNER =================
print("Bot is running with Advanced Sharing & Pre-fill Message Features...")
bot.infinity_polling()
