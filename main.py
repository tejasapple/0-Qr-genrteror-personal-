import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import pymongo
import qrcode
import io
import uuid
from datetime import datetime, timedelta
from PIL import Image
import csv
import os
from bson.objectid import ObjectId

# ================= कॉन्फ़िगरेशन =================
BOT_TOKEN = "8740636028:AAFKOpliANI816prOplKF1FB9qxF7TkKoG8"
MONGO_URI = "mongodb+srv://Tejas7xx:mrxtejas7@cluster0.akhlgjf.mongodb.net/?appName=Cluster0"
OWNER_ID = 8702240402 # अपनी टेलीग्राम यूजर आईडी यहाँ डालें

LOG_GROUP_ID = OWNER_ID 

bot = telebot.TeleBot(BOT_TOKEN)

# ================= डेटाबेस सेटअप =================
client = pymongo.MongoClient(MONGO_URI)
db = client['upi_master_bot']

upi_col = db['upi_ids']        
admins_col = db['admins']      
tx_col = db['transactions']    
saved_qrs_col = db['saved_qrs']

# Owner को डिफ़ॉल्ट एडमिन बनाना
if not admins_col.find_one({"user_id": OWNER_ID}):
    admins_col.insert_one({
        "user_id": OWNER_ID, 
        "name": "Owner", 
        "is_sharing": False,
        "primary_id": OWNER_ID,
        "last_cleared": datetime.now(),
        "advance_received": 0,
        "last_claimed_amount": 0
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

def generate_qr_image(upi_id, name, amount, tx_id):
    # क्लीन QR जेनरेट करना (कोई टेक्स्ट या लोगो नहीं)
    upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&tr={tx_id}&tn={tx_id}"
        
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(upi_url)
    qr.make(fit=True)
    
    # ब्लैक & व्हाइट क्लीन QR
    img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    
    bio = io.BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

# ================= मेन्यू और कीबोर्ड्स =================

def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("💸 Generate QR"), KeyboardButton("🖼 My Saved QRs")
    )
    markup.add(KeyboardButton("📊 Status/Stats"))
    if user_id == OWNER_ID:
        markup.add(KeyboardButton("⚙️ Admin Panel"))
    return markup

# ================= मुख्य कमांड्स =================

@bot.message_handler(commands=['start'])
def start_cmd(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "🚫 आप इस बोट को इस्तेमाल करने के लिए ऑथराइज्ड नहीं हैं।")
        return
    bot.send_message(message.chat.id, "🤖 Welcome to Master UPI Bot!\nसिस्टम रेडी है।", reply_markup=main_menu(message.from_user.id))

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
        
    tx_id = "TXN" + str(uuid.uuid4().hex)[:10].upper()
    qr_img = generate_qr_image(upi['upi_id'], upi['name'], amount, tx_id)
    
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

# ================= 2. My Saved QRs =================

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
        return bot.send_message(message.chat.id, "❌ आपने फोटो नहीं भेजी।")
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


# ================= 3. स्टेटस और एनालिटिक्स (New Custom Dashboard) =================

@bot.message_handler(func=lambda msg: msg.text == "📊 Status/Stats")
def show_stats_menu(message):
    user_id = message.from_user.id
    if not is_admin(user_id): return
    
    # अगर ओनर है, तो उसे ग्लोबल मेन्यू दिखाएं
    if user_id == OWNER_ID:
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Today (All)", callback_data="stat_today_all"),
            InlineKeyboardButton("Yesterday (All)", callback_data="stat_yesterday_all"),
            InlineKeyboardButton("This Month (All)", callback_data="stat_month_all"),
            InlineKeyboardButton("Total (All)", callback_data="stat_all_all")
        )
        bot.send_message(message.chat.id, "👑 **Owner Global Stats View:**", reply_markup=markup, parse_mode="Markdown")
    else:
        # अगर पार्टनर/एडमिन है, तो सीधा उसका कैलकुलेटर बोर्ड दिखाएं
        send_partner_dashboard(message.chat.id, user_id)

def send_partner_dashboard(chat_id, user_id):
    admin_data = admins_col.find_one({"user_id": user_id})
    primary_id = admin_data.get("primary_id", user_id)
    p_admin = admins_col.find_one({"user_id": primary_id})
    
    # सभी लिंक्ड आईडी खोजें
    linked_admins = admins_col.find({"primary_id": primary_id})
    p_ids = [a["user_id"] for a in linked_admins]
    if primary_id not in p_ids: p_ids.append(primary_id)
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # 1. Today Stats
    today_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gte": today_start}}))
    today_total = sum(t['amount'] for t in today_txns)
    
    # 2. Unclaimed Balance Calculation
    last_cleared = p_admin.get("last_cleared", datetime.min)
    uncleared_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gt": last_cleared}}))
    uncleared_total = sum(t['amount'] for t in uncleared_txns)
    
    advance = p_admin.get("advance_received", 0)
    last_claimed = p_admin.get("last_claimed_amount", 0)
    
    # 3. This Month Revenue
    month_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gte": month_start}}))
    month_total = sum(t['amount'] for t in month_txns)
    
    is_sharing = p_admin.get("is_sharing", False)
    
    # डैशबोर्ड मैसेज तैयार करना
    msg = "📊 **Your Performance Dashboard**\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    
    msg += f"📅 **TODAY'S REVENUE**\n"
    msg += f"Total Balance Received Today: ₹{today_total}\n"
    if is_sharing:
        msg += f"Your Cut (30%): ₹{today_total * 0.30:.2f}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    
    if is_sharing:
        unclaimed = (uncleared_total * 0.30) - advance
        msg += f"💰 **BALANCE CALCULATOR**\n"
        msg += f"Your Total Unclaimed Balance: ₹{unclaimed:.2f}\n\n"
        
        msg += f"Your Last Claimed Balance: ₹{last_claimed:.2f}\n"
        msg += f"Advance Received: ₹{advance:.2f}\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        
    msg += f"📆 **THIS MONTH REVENUE**\n"
    msg += f"Total Balance: ₹{month_total}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━"
    
    bot.send_message(chat_id, msg, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("stat_"))
def process_owner_stats(call):
    if call.from_user.id != OWNER_ID: return
    period = call.data.split("_")[1]
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    query = {"status": "done"}
    
    if period == "today":
        query["time"] = {"$gte": today_start}
        time_text = "Today"
    elif period == "yesterday":
        query["time"] = {"$gte": today_start - timedelta(days=1), "$lt": today_start}
        time_text = "Yesterday"
    elif period == "month":
        query["time"] = {"$gte": now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)}
        time_text = "This Month"
    else: 
        time_text = "Total All Time"

    transactions = list(tx_col.find(query))
    total_amount = sum(t['amount'] for t in transactions)
    total_txns = len(transactions)
    
    stats_msg = f"👥 **Global Stats (All Admins)**\n🗓 **Period:** {time_text}\n\n💸 **Total Received:** ₹{total_amount}\n🔢 **Total TXNs:** {total_txns}\n\n**Admin Wise Breakdown:**\n"
    
    admins = admins_col.find()
    for admin in admins:
        admin_txns = [t for t in transactions if t.get('admin_id') == admin['user_id']]
        if admin_txns:
            admin_total = sum(t['amount'] for t in admin_txns)
            stats_msg += f"🔸 {admin.get('name', admin['user_id'])}: ₹{admin_total} ({len(admin_txns)} txn)\n"

    bot.send_message(call.message.chat.id, stats_msg, parse_mode="Markdown")
    bot.answer_callback_query(call.id)


# ================= 4. एडमिन पैनल & Partner Management =================

@bot.message_handler(func=lambda msg: msg.text == "⚙️ Admin Panel")
def admin_panel(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "🚫 सिर्फ Owner ही इसे खोल सकता है।")
        
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Add UPI", "➕ Add Admin")
    markup.add("🔗 Link Sub-ID", "💰 Partner Balances")
    markup.add("🗑 Remove Manual TXN", "⬅️ Back to Main")
    bot.send_message(message.chat.id, "⚙️ Admin Panel में आपका स्वागत है।", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "⬅️ Back to Main")
def back_main(message):
    bot.send_message(message.chat.id, "Main Menu:", reply_markup=main_menu(message.from_user.id))

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
    bot.send_message(message.chat.id, f"✅ UPI ID Successfully Added!\nUPI: {upi_id}\nGroup: {group}", reply_markup=main_menu(message.from_user.id))

# --- Add Admin Flow ---
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
        bot.send_message(message.chat.id, "❌ अमान्य आईडी!", reply_markup=main_menu(message.from_user.id))

def step_admin_type(message, new_admin_id):
    name = message.text
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("Regular (No Share)", "Sharing (30% Admin / 70% Owner)")
    msg = bot.send_message(message.chat.id, "यह एडमिन किस प्रकार का है?", reply_markup=markup)
    bot.register_next_step_handler(msg, save_admin, new_admin_id, name)

def save_admin(message, new_admin_id, name):
    is_sharing = "Sharing" in message.text
    if admins_col.find_one({"user_id": new_admin_id}):
        bot.send_message(message.chat.id, "❌ यह एडमिन पहले से मौजूद है!", reply_markup=main_menu(message.from_user.id))
    else:
        admins_col.insert_one({
            "user_id": new_admin_id, 
            "name": name, 
            "is_sharing": is_sharing,
            "primary_id": new_admin_id,
            "advance_received": 0,
            "last_claimed_amount": 0,
            "last_cleared": datetime.now()
        })
        bot.send_message(message.chat.id, f"✅ Admin Added!\nName: {name}\nType: {'Sharing (30%)' if is_sharing else 'Regular'}", reply_markup=main_menu(message.from_user.id))

# --- Link Sub-ID Flow (Multiple IDs mapping) ---
@bot.message_handler(func=lambda msg: msg.text == "🔗 Link Sub-ID")
def link_subid_start(message):
    if message.from_user.id != OWNER_ID: return
    msg = bot.send_message(message.chat.id, "नई Telegram ID भेजें जिसे लिंक करना है:")
    bot.register_next_step_handler(msg, step_link_subid)
    
def step_link_subid(message):
    try:
        new_id = int(message.text)
        if admins_col.find_one({"user_id": new_id}):
            return bot.send_message(message.chat.id, "❌ यह ID सिस्टम में पहले से मौजूद है।")
        
        all_partners = list(admins_col.find({"is_sharing": True}))
        main_partners = [p for p in all_partners if p.get("primary_id", p["user_id"]) == p["user_id"]]
        
        if not main_partners:
            return bot.send_message(message.chat.id, "❌ कोई Sharing Partner मौजूद नहीं है। पहले ➕ Add Admin से पार्टनर बनाएँ।")
            
        markup = InlineKeyboardMarkup(row_width=1)
        for p in main_partners:
            markup.add(InlineKeyboardButton(p["name"], callback_data=f"linkto_{new_id}_{p['user_id']}"))
        
        bot.send_message(message.chat.id, "इस ID को किस मेन पार्टनर के खाते से लिंक करना है?", reply_markup=markup)
    except ValueError:
        bot.send_message(message.chat.id, "❌ अमान्य आईडी।")
        
@bot.callback_query_handler(func=lambda call: call.data.startswith("linkto_"))
def process_link(call):
    if call.from_user.id != OWNER_ID: return
    parts = call.data.split("_")
    new_id = int(parts[1])
    primary_id = int(parts[2])
    
    p_admin = admins_col.find_one({"user_id": primary_id})
    
    admins_col.insert_one({
        "user_id": new_id,
        "name": p_admin["name"] + " (Linked)",
        "is_sharing": True,
        "primary_id": primary_id,
        "last_cleared": datetime.now()
    })
    bot.edit_message_text(f"✅ ID {new_id} सफलतापूर्वक {p_admin['name']} के साथ लिंक हो गई है!\n(दोनों का हिसाब एक ही जगह दिखेगा)", call.message.chat.id, call.message.message_id)

# --- Partner Balance / Advance / Clear System ---
@bot.message_handler(func=lambda msg: msg.text == "💰 Partner Balances")
def partner_balances(message):
    if message.from_user.id != OWNER_ID: return
    
    all_partners = list(admins_col.find({"is_sharing": True}))
    main_partners = [p for p in all_partners if p.get("primary_id", p["user_id"]) == p["user_id"]]
    
    if not main_partners:
        return bot.send_message(message.chat.id, "❌ आपका कोई Sharing Partner नहीं है।")
        
    markup = InlineKeyboardMarkup(row_width=1)
    for partner in main_partners:
        partner_id = partner["user_id"]
        
        linked = list(admins_col.find({"primary_id": partner_id}))
        p_ids = [a["user_id"] for a in linked]
        if partner_id not in p_ids: p_ids.append(partner_id)
        
        last_cleared = partner.get("last_cleared", datetime.min)
        uncleared_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gt": last_cleared}}))
        uncleared_total = sum(t['amount'] for t in uncleared_txns)
        
        btn_text = f"👤 {partner['name']} | Total: ₹{uncleared_total}"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"partner_{partner_id}"))
        
    bot.send_message(message.chat.id, "👥 **पार्टनर बैलेंस मैनेज करें:**\n(किसी भी पार्टनर का हिसाब देखने और क्लियर करने के लिए क्लिक करें)", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("partner_"))
def partner_details(call):
    if call.from_user.id != OWNER_ID: return
    partner_id = int(call.data.split("_")[1])
    partner = admins_col.find_one({"user_id": partner_id})
    
    linked = list(admins_col.find({"primary_id": partner_id}))
    p_ids = [a["user_id"] for a in linked]
    if partner_id not in p_ids: p_ids.append(partner_id)
    
    last_cleared = partner.get("last_cleared", datetime.min)
    uncleared_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gt": last_cleared}}))
    uncleared_total = sum(t['amount'] for t in uncleared_txns)
    
    share_30 = uncleared_total * 0.30
    share_70 = uncleared_total * 0.70
    advance = partner.get("advance_received", 0)
    net_payable = share_30 - advance
    
    msg = (f"👤 **Partner:** {partner['name']}\n"
           f"🔗 **Linked Accounts:** {len(p_ids)}\n\n"
           f"💸 **Total Uncleared Amount:** ₹{uncleared_total}\n"
           f"🔹 **Partner Share (30%):** ₹{share_30:.2f}\n"
           f"👑 **Your Share (70%):** ₹{share_70:.2f}\n\n"
           f"💵 **Advance Given:** ₹{advance:.2f}\n"
           f"✅ **Net Payable to Partner:** ₹{net_payable:.2f}\n")
           
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("➕ Give Advance", callback_data=f"giveadv_{partner_id}"))
    markup.add(InlineKeyboardButton("✅ Mark as Cleared (Claim)", callback_data=f"clearbal_{partner_id}_{net_payable}"))
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

# Advance Handler
@bot.callback_query_handler(func=lambda call: call.data.startswith("giveadv_"))
def give_advance_start(call):
    if call.from_user.id != OWNER_ID: return
    partner_id = int(call.data.split("_")[1])
    msg = bot.send_message(call.message.chat.id, "इस पार्टनर को कितना एडवांस देना है? (Amount लिखें):")
    bot.register_next_step_handler(msg, process_give_advance, partner_id)
    
def process_give_advance(message, partner_id):
    try:
        amt = float(message.text)
        admins_col.update_one(
            {"user_id": partner_id},
            {"$inc": {"advance_received": amt}}
        )
        bot.send_message(message.chat.id, f"✅ ₹{amt} का एडवांस सफलतापूर्वक जुड़ गया!")
    except ValueError:
        bot.send_message(message.chat.id, "❌ अमान्य अमाउंट।")

# Clear Balance Handler
@bot.callback_query_handler(func=lambda call: call.data.startswith("clearbal_"))
def clear_partner_balance(call):
    if call.from_user.id != OWNER_ID: return
    parts = call.data.split("_")
    partner_id = int(parts[1])
    net_paid = float(parts[2])
    
    admins_col.update_one(
        {"user_id": partner_id}, 
        {
            "$set": {
                "last_cleared": datetime.now(),
                "last_claimed_amount": net_paid,
                "advance_received": 0
            }
        }
    )
    bot.answer_callback_query(call.id, "✅ बैलेंस सफलतापूर्वक क्लियर कर दिया गया है!", show_alert=True)
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
print("Bot is running with Multi-ID, Calculator Board & Clean QR...")
bot.infinity_polling()
