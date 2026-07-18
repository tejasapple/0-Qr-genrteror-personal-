import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import pymongo
import qrcode
import io
import uuid
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont # QR पर लोगो और टेक्स्ट के लिए
import csv
import os

# ================= कॉन्फ़िगरेशन =================
BOT_TOKEN = "8740636028:AAFKOpliANI816prOplKF1FB9qxF7TkKoG8"
MONGO_URI = "mongodb+srv://Tejas7xx:mrxtejas7@cluster0.akhlgjf.mongodb.net/?appName=Cluster0"
OWNER_ID = 8702240402 # अपनी टेलीग्राम यूजर आईडी यहाँ डालें (नंबर में)

# लॉग इन बोर्ड (लॉग्स चैनल/ग्रुप) की आईडी यहाँ डालें (अगर ग्रुप प्राइवेट है तो -100 से शुरू होने वाली आईडी डालें)
# अभी के लिए यह ओनर को ही लॉग्स भेजेगा, आप इसे अपने ग्रुप की आईडी से बदल सकते हैं।
LOG_GROUP_ID = OWNER_ID 

bot = telebot.TeleBot(BOT_TOKEN)

# ================= डेटाबेस सेटअप =================
client = pymongo.MongoClient(MONGO_URI)
db = client['upi_master_bot']

# Collections (Tables)
upi_col = db['upi_ids']        # UPI IDs स्टोर करने के लिए
admins_col = db['admins']      # Admins स्टोर करने के लिए
tx_col = db['transactions']    # पेमेंट्स की हिस्ट्री
saved_qrs_col = db['saved_qrs']# पर्सनल सेव्ड QRs

# Owner को डिफ़ॉल्ट एडमिन बनाना
if not admins_col.find_one({"user_id": OWNER_ID}):
    admins_col.insert_one({"user_id": OWNER_ID, "name": "Owner", "use_tr": True})

# ================= हेल्पर फंक्शन्स =================

def is_admin(user_id):
    return user_id == OWNER_ID or admins_col.find_one({"user_id": user_id}) is not None

def get_next_upi(group):
    """Round Robin एल्गोरिदम: जिस UPI को सबसे पहले यूज़ किया गया था, उसे चुनता है"""
    query = {} if group == "All" else {"group": group}
    upi = upi_col.find_one(query, sort=[("last_used", 1)])
    if upi:
        upi_col.update_one({"_id": upi["_id"]}, {"$set": {"last_used": datetime.now()}})
    return upi

def generate_qr_image(upi_id, name, amount, tx_id, logo_file_id=None, use_tr=True):
    """UPI लिंक बनाकर उसका QR इमेज बनाता है, साथ ही नीचे लोगो और TXN ID लगाता है"""
    # अगर एडमिन ने TR (Transaction ID) इनेबल रखा है, तो ही लिंक में जुड़ेगा
    if use_tr:
        upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&tr={tx_id}"
    else:
        upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}"
        
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    
    # इमेज के नीचे एक बैनर/पट्टी जोड़ना ताकि TXN ID और लोगो आ सके
    w, h = img.size
    banner_height = 60
    new_img = Image.new('RGB', (w, h + banner_height), 'white')
    new_img.paste(img, (0, 0))
    
    draw = ImageDraw.Draw(new_img)
    # नीचे की तरफ TXN ID लिखना
    display_text = f"TXN: {tx_id}" if use_tr else f"Amount: {amount} (No TXN ID)"
    draw.text((15, h + 20), display_text, fill="black")
    
    # अगर लोगो है, तो डाउनलोड करके लगाना
    if logo_file_id:
        try:
            file_info = bot.get_file(logo_file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            logo = Image.open(io.BytesIO(downloaded_file)).convert("RGBA")
            # लोगो को छोटा करना
            logo.thumbnail((50, 50))
            # लोगो को नीचे राइट साइड में चिपकाना
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

# ================= नया फीचर: CSV Export (ओनर के लिए आसान वेरिफिकेशन) =================
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
    
    # पेंडिंग ट्रांजेक्शन डेटाबेस में सेव करें
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

# ================= QR बटन्स हैंडलर & लॉग इन बोर्ड =================

@bot.callback_query_handler(func=lambda call: call.data.startswith(("done_", "cancel_", "regen_")))
def handle_tx_action(call):
    action, tx_id = call.data.split("_")
    tx = tx_col.find_one({"tx_id": tx_id})
    if not tx: return bot.answer_callback_query(call.id, "Transaction not found!")

    if action == "done":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "done"}})
        bot.edit_message_caption("✅ **PAYMENT RECEIVED & SAVED!**\n\n" + call.message.caption, 
                                 call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                                 
        # --- लॉग इन बोर्ड (Log Board) में एंट्री भेजना ---
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
        # उसी अमाउंट और ग्रुप के साथ नया QR
        create_and_send_qr(call.message, tx['amount'], tx['group'], call.from_user.id)


# ================= 2. माय सेटिंग्स (Logo & TXN ID Toggle) =================

@bot.message_handler(func=lambda msg: msg.text == "🛠 My Settings")
def my_settings(message):
    admin_id = message.from_user.id
    if not is_admin(admin_id): return
    
    admin_data = admins_col.find_one({"user_id": admin_id})
    use_tr = admin_data.get("use_tr", True)
    status_text = "ON 🟢" if use_tr else "OFF 🔴"
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(f"🔄 Toggle TXN Prefill (Current: {status_text})", callback_data="set_toggle_tr"),
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
        bot.answer_callback_query(call.id, "✅ TXN Prefill Status Updated!")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        my_settings(call.message) # रिफ्रेश मेन्यू
        
    elif action == "set_logo":
        msg = bot.send_message(call.message.chat.id, "कृपया अपना लोगो (Photo) भेजें।\n(नोट: फोटो बिना कम्प्रेशन के डॉक्यूमेंट में नहीं, नॉर्मल फोटो की तरह भेजें):")
        bot.register_next_step_handler(msg, process_new_logo)
        
    elif action == "set_rm_logo":
        admins_col.update_one({"user_id": admin_id}, {"$unset": {"logo_id": ""}})
        bot.answer_callback_query(call.id, "✅ Logo Removed!", show_alert=True)

def process_new_logo(message):
    if not message.photo:
        bot.send_message(message.chat.id, "❌ आपने फोटो नहीं भेजी। कृपया सेटिंग्स में जाकर दोबारा प्रयास करें।")
        return
    # सबसे अच्छी क्वालिटी वाली फोटो लेना
    logo_id = message.photo[-1].file_id
    admins_col.update_one({"user_id": message.from_user.id}, {"$set": {"logo_id": logo_id}})
    bot.send_message(message.chat.id, "✅ आपका कस्टम लोगो सेव हो गया है! अब जनरेट होने वाले हर QR के नीचे यह दिखेगा।")


# ================= 3. स्टेटस और एनालिटिक्स (एडवांस फ़िल्टर) =================

@bot.message_handler(func=lambda msg: msg.text == "📊 Status/Stats")
def show_stats_menu(message):
    user_id = message.from_user.id
    if not is_admin(user_id): return
    
    # ओनर और एडमिन दोनों का डिफ़ॉल्ट व्यू 'self' (पर्सनल) रहेगा
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("Today", callback_data="stat_today_self"),
        InlineKeyboardButton("Yesterday", callback_data="stat_yesterday_self"),
        InlineKeyboardButton("Last 3 Days", callback_data="stat_3d_self"),
        InlineKeyboardButton("Last 7 Days", callback_data="stat_7d_self"),
        InlineKeyboardButton("This Month", callback_data="stat_month_self"),
        InlineKeyboardButton("Total All", callback_data="stat_all_self")
    )
    
    # अगर ओनर है, तो उसे 'सबका' (All Admins) डेटा देखने का ऑप्शन भी दें
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
    scope = parts[2] # 'self' या 'all'
    admin_id = call.from_user.id
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    query = {"status": "done"}
    
    # स्कोप फ़िल्टर (Self vs All)
    if scope == "self":
        query["admin_id"] = admin_id
        header_text = "👤 **Your Personal Stats**"
    else:
        header_text = "👥 **Global Stats (All Admins)**"
        
    # टाइम फ़िल्टर
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
    else: # all
        time_text = "Total All Time"

    transactions = list(tx_col.find(query))
    total_amount = sum(t['amount'] for t in transactions)
    total_txns = len(transactions)
    
    stats_msg = f"{header_text}\n🗓 **Period:** {time_text}\n\n💸 **Total Revenue:** ₹{total_amount}\n🔢 **Total TXNs:** {total_txns}\n"
    
    # अगर ओनर ने 'all' चुना है, तो एडमिन-वाइज ब्रेकडाउन भी दिखाएँ
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

# ================= 4. एडमिन पैनल (UPI/Admin Add/Remove) =================

@bot.message_handler(func=lambda msg: msg.text == "⚙️ Admin Panel")
def admin_panel(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "🚫 सिर्फ Owner ही इसे खोल सकता है।")
        
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Add UPI", "➕ Add Admin")
    markup.add("🗑 Remove Manual TXN", "⬅️ Back to Main")
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
        bot.register_next_step_handler(msg, save_admin, new_admin_id)
    except ValueError:
        bot.send_message(message.chat.id, "❌ अमान्य आईडी! कृपया केवल नंबर भेजें।", reply_markup=main_menu())

def save_admin(message, new_admin_id):
    name = message.text
    if admins_col.find_one({"user_id": new_admin_id}):
        bot.send_message(message.chat.id, "❌ यह एडमिन पहले से मौजूद है!", reply_markup=main_menu())
    else:
        admins_col.insert_one({"user_id": new_admin_id, "name": name, "use_tr": True})
        bot.send_message(message.chat.id, f"✅ Admin Successfully Added!\nName: {name}\nID: {new_admin_id}", reply_markup=main_menu())

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
        bot.send_message(message.chat.id, f"✅ TXN {tx_id} सफलतापूर्वक डिलीट कर दिया गया!")
    else:
        bot.send_message(message.chat.id, "❌ यह TXN ID नहीं मिली।")

# ================= 5. My Saved QRs (पर्सनल क्यूआर) =================

@bot.message_handler(func=lambda msg: msg.text == "🖼 My Saved QRs")
def my_qrs(message):
    if not is_admin(message.from_user.id): return
    bot.send_message(message.chat.id, "यह सेक्शन आपके पर्सनल फिक्स QR (बिना अमाउंट वाले) के लिए है। कोड में `saved_qrs_col` डेटाबेस बना हुआ है, आप इसमें फोटोज सेव करवा सकते हैं।")

# ================= BOT RUNNER =================
print("Bot is running with Advanced Features...")
bot.infinity_polling()
