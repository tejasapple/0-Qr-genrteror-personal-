import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import pymongo
import qrcode
import io
import uuid
from datetime import datetime, timedelta

# ================= कॉन्फ़िगरेशन =================
BOT_TOKEN = "8740636028:AAFKOpliANI816prOplKF1FB9qxF7TkKoG8"
MONGO_URI = "mongodb+srv://Tejas7xx:mrxtejas7@cluster0.akhlgjf.mongodb.net/?appName=Cluster0"
OWNER_ID = 8702240402# अपनी टेलीग्राम यूजर आईडी यहाँ डालें (नंबर में)

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
    admins_col.insert_one({"user_id": OWNER_ID, "name": "Owner"})

# ================= हेल्पर फंक्शन्स =================

def is_admin(user_id):
    return user_id == OWNER_ID or admins_col.find_one({"user_id": user_id}) is not None

def get_next_upi(group):
    """Round Robin एल्गोरिदम: जिस UPI को सबसे पहले यूज़ किया गया था, उसे चुनता है"""
    query = {} if group == "All" else {"group": group}
    # last_used के हिसाब से असेंडिंग ऑर्डर (पुराना सबसे पहले)
    upi = upi_col.find_one(query, sort=[("last_used", 1)])
    if upi:
        # उसे अभी का टाइम दे दो ताकि वो कतार में सबसे पीछे चला जाए
        upi_col.update_one({"_id": upi["_id"]}, {"$set": {"last_used": datetime.now()}})
    return upi

def generate_qr_image(upi_id, name, amount, tx_id):
    """UPI लिंक बनाकर उसका QR इमेज बनाता है"""
    upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&tr={tx_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    bio = io.BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

# ================= मेन्यू और कीबोर्ड्स =================

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("💸 Generate QR"), KeyboardButton("🖼 My Saved QRs"),
        KeyboardButton("📊 Status/Stats"), KeyboardButton("⚙️ Admin Panel")
    )
    return markup

# ================= मुख्य कमांड्स =================

@bot.message_handler(commands=['start'])
def start_cmd(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "🚫 आप इस बोट को इस्तेमाल करने के लिए ऑथराइज्ड नहीं हैं।")
        return
    bot.send_message(message.chat.id, "🤖 Welcome to Master UPI Bot!\nसिस्टम रेडी है।", reply_markup=main_menu())

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

# ================= QR बटन्स हैंडलर (Done, Cancel, Regen) =================

@bot.callback_query_handler(func=lambda call: call.data.startswith(("done_", "cancel_", "regen_")))
def handle_tx_action(call):
    action, tx_id = call.data.split("_")
    tx = tx_col.find_one({"tx_id": tx_id})
    if not tx: return bot.answer_callback_query(call.id, "Transaction not found!")

    if action == "done":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "done"}})
        bot.edit_message_caption("✅ **PAYMENT RECEIVED & SAVED!**\n\n" + call.message.caption, 
                                 call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        # Owner को नोटिफिकेशन
        if call.from_user.id != OWNER_ID:
            bot.send_message(OWNER_ID, f"💰 **New Payment Received!**\nAdmin ID: {call.from_user.id}\nAmount: ₹{tx['amount']}\nTXN: {tx_id}")
            
    elif action == "cancel":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "cancelled"}})
        bot.edit_message_caption("❌ **PAYMENT CANCELLED**\n\n" + call.message.caption, 
                                 call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                                 
    elif action == "regen":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "cancelled"}})
        bot.delete_message(call.message.chat.id, call.message.message_id)
        # उसी अमाउंट और ग्रुप के साथ नया QR
        create_and_send_qr(call.message, tx['amount'], tx['group'], call.from_user.id)

# ================= 2. स्टेटस और एनालिटिक्स =================

@bot.message_handler(func=lambda msg: msg.text == "📊 Status/Stats")
def show_stats(message):
    if not is_admin(message.from_user.id): return
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    
    # आज का टोटल
    today_tx = list(tx_col.find({"status": "done", "time": {"$gte": today_start}}))
    today_total = sum(t['amount'] for t in today_tx)
    
    # 7 दिन का टोटल
    week_tx = list(tx_col.find({"status": "done", "time": {"$gte": week_start}}))
    week_total = sum(t['amount'] for t in week_tx)
    
    # Admin वाइज आज का डेटा
    admin_stats = ""
    admins = admins_col.find()
    for admin in admins:
        admin_total = sum(t['amount'] for t in today_tx if t['admin_id'] == admin['user_id'])
        admin_stats += f"👤 {admin.get('name', admin['user_id'])}: ₹{admin_total}\n"
        
    stats_msg = (
        f"📊 **PAYMENT STATS**\n\n"
        f"📅 **Today's Total:** ₹{today_total}\n"
        f"🗓 **Last 7 Days:** ₹{week_total}\n\n"
        f"👥 **Today by Admins:**\n{admin_stats}"
    )
    bot.send_message(message.chat.id, stats_msg, parse_mode="Markdown")

# ================= 3. एडमिन पैनल (UPI/Admin Add/Remove) =================

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
        "last_used": datetime.now() # शुरुआत में आज का टाइम
    })
    bot.send_message(message.chat.id, f"✅ UPI ID Successfully Added!\nUPI: {upi_id}\nGroup: {group}", reply_markup=main_menu())

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

# ================= 4. My Saved QRs (पर्सनल क्यूआर) =================

@bot.message_handler(func=lambda msg: msg.text == "🖼 My Saved QRs")
def my_qrs(message):
    if not is_admin(message.from_user.id): return
    # अभी के लिए एक डमी/सिंपल रिप्लाई, आप इसे Admin पैनल से ऐड करने का फंक्शन बना सकते हैं
    bot.send_message(message.chat.id, "यह सेक्शन आपके पर्सनल फिक्स QR (बिना अमाउंट वाले) के लिए है। कोड में `saved_qrs_col` डेटाबेस बना हुआ है, आप इसमें फोटोज सेव करवा सकते हैं।")

# ================= BOT RUNNER =================
print("Bot is running...")
bot.infinity_polling()
