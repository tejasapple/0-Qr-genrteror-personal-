import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import pymongo
import qrcode
import io
import uuid
from datetime import datetime, timedelta
from PIL import Image
from bson.objectid import ObjectId

# ================= कॉन्फ़िगरेशन =================
BOT_TOKEN = "8966888657:AAEl5dGNrPk_QzPz8r4XZat01mIfJMNVC7k"
MONGO_URI = "mongodb+srv://Tejas7xx:mrxtejas7@cluster0.akhlgjf.mongodb.net/?appName=Cluster0"
OWNER_ID = 7121137252

LOG_GROUP_ID = OWNER_ID 

bot = telebot.TeleBot(BOT_TOKEN)

# ================= डेटाबेस सेटअप =================
client = pymongo.MongoClient(MONGO_URI)
db = client['upi_master_bot']

upi_col = db['upi_ids']        
admins_col = db['admins']      
tx_col = db['transactions']    
saved_qrs_col = db['saved_qrs']
settings_col = db['settings']

# डिफ़ॉल्ट सेटिंग्स इनिशियलाइज़ करना
if not settings_col.find_one({"_id": "qr_setting"}):
    settings_col.insert_one({"_id": "qr_setting", "include_txn": True})

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
    now = datetime.now()
    # Waiting filter: check if waiting_until is either absent or expired
    query = {
        "$or": [
            {"waiting_until": {"$exists": False}},
            {"waiting_until": None},
            {"waiting_until": {"$lte": now}}
        ]
    }
    if group != "All":
        query["group"] = group

    # Round-robin / Least recently used selection
    upi = upi_col.find_one(query, sort=[("last_used", 1)])
    return upi

def generate_qr_image(upi_id, name, amount, tx_id):
    setting = settings_col.find_one({"_id": "qr_setting"})
    include_txn = setting.get("include_txn", True) if setting else True
    
    if include_txn:
        upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&tr={tx_id}&tn={tx_id}"
    else:
        upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}"
        
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(upi_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    bio = io.BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

# ================= मेन्यू और कीबोर्ड्स =================

def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("💸 Generate QR"), KeyboardButton("🖼 My Saved QRs"))
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
        bot.send_message(message.chat.id, f"❌ {group} ग्रुप में कोई एक्टिव UPI ID उपलब्ध नहीं है!")
        return
        
    tx_id = "TXN" + str(uuid.uuid4().hex)[:10].upper()
    qr_img = generate_qr_image(upi['upi_id'], upi['name'], amount, tx_id)
    
    tx_col.insert_one({
        "tx_id": tx_id, "amount": amount, "group": group, "upi_id": upi['upi_id'], 
        "admin_id": admin_id, "status": "pending", "time": datetime.now()
    })
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Add Payment", callback_data=f"done_{tx_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"cancel_{tx_id}")
    )
    markup.add(
        InlineKeyboardButton("⏳ Waiting (12h)", callback_data=f"wait_12_{tx_id}"),
        InlineKeyboardButton("⏳ Waiting (24h)", callback_data=f"wait_24_{tx_id}")
    )
    markup.add(InlineKeyboardButton("🔄 Regenerate (Next UPI)", callback_data=f"regen_{tx_id}"))
    
    caption = f"🧾 **Payment QR**\n\n💸 Amount: ₹{amount}\n🆔 TXN ID: `{tx_id}`\n🏦 UPI: `{upi['upi_id']}`\n👤 Name: {upi['name']}"
    bot.send_photo(message.chat.id, qr_img, caption=caption, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith(("done_", "cancel_", "regen_", "wait_")))
def handle_tx_action(call):
    data_parts = call.data.split("_")
    action = data_parts[0]
    
    if action == "wait":
        hours = int(data_parts[1])
        tx_id = data_parts[2]
    else:
        tx_id = data_parts[1]
        
    tx = tx_col.find_one({"tx_id": tx_id})
    if not tx: 
        return bot.answer_callback_query(call.id, "Transaction not found!")

    if action == "done":
        # Update transaction status
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "done"}})
        
        # Mark UPI as recently used so other UPIs get priority in round-robin
        upi_col.update_one({"upi_id": tx["upi_id"]}, {"$set": {"last_used": datetime.now()}})
        
        bot.edit_message_caption("✅ **PAYMENT RECEIVED & ADDED!**\n\n" + call.message.caption, 
                                 call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                                 
        admin_info = admins_col.find_one({"user_id": call.from_user.id})
        admin_name = admin_info.get('name', str(call.from_user.id)) if admin_info else str(call.from_user.id)
        username = f"@{call.from_user.username}" if call.from_user.username else "No Username"
        time_str = datetime.now().strftime("%I:%M %p, %d %b %Y")
        
        notify_msg = (
            f"🟢 **PAYMENT APPROVED**\n\n"
            f"👤 **Admin Name:** {admin_name}\n"
            f"🔗 **Username:** {username}\n"
            f"💸 **Amount Received:** ₹{tx['amount']}\n"
            f"🏦 **UPI ID Used:** `{tx['upi_id']}`\n"
            f"⏰ **Time:** {time_str}\n"
            f"🆔 **TXN ID:** `{tx_id}`"
        )
        
        try:
            bot.send_message(OWNER_ID, notify_msg, parse_mode="Markdown")
            if call.from_user.id != OWNER_ID:
                bot.send_message(call.from_user.id, notify_msg, parse_mode="Markdown")
        except Exception as e:
            print("Notification error:", e)

    elif action == "cancel":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "cancelled"}})
        bot.edit_message_caption("❌ **PAYMENT DECLINED**\n\n" + call.message.caption, 
                                 call.message.chat.id, call.message.message_id, parse_mode="Markdown")

    elif action == "wait":
        until = datetime.now() + timedelta(hours=hours)
        upi_col.update_one({"upi_id": tx["upi_id"]}, {"$set": {"waiting_until": until}})
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "waiting"}})
        bot.answer_callback_query(call.id, f"⚠️ UPI put on hold for {hours} hours!", show_alert=True)
        bot.edit_message_caption(f"⏳ **UPI PUT IN WAITING ({hours} Hours)**\n\n" + call.message.caption, 
                                 call.message.chat.id, call.message.message_id, parse_mode="Markdown")

    elif action == "regen":
        tx_col.update_one({"tx_id": tx_id}, {"$set": {"status": "cancelled"}})
        bot.delete_message(call.message.chat.id, call.message.message_id)
        create_and_send_qr(call.message, tx['amount'], tx['group'], call.from_user.id)

# ================= 2. स्टेटस और एनालिटिक्स (Dashboards) =================

@bot.message_handler(func=lambda msg: msg.text == "📊 Status/Stats")
def show_stats_menu(message):
    user_id = message.from_user.id
    if not is_admin(user_id): return
    
    markup = InlineKeyboardMarkup(row_width=2)
    if user_id == OWNER_ID:
        markup.add(
            InlineKeyboardButton("📅 Today", callback_data="stat_today"),
            InlineKeyboardButton("📆 Yesterday", callback_data="stat_yesterday"),
            InlineKeyboardButton("📊 Last 3 Days", callback_data="stat_last3"),
            InlineKeyboardButton("📈 Last 7 Days (Daily)", callback_data="stat_last7"),
            InlineKeyboardButton("🗓 This Month", callback_data="stat_month"),
            InlineKeyboardButton("🌐 Total (All Time)", callback_data="stat_all")
        )
        bot.send_message(message.chat.id, "👑 **Owner Global Stats View:**", reply_markup=markup, parse_mode="Markdown")
    else:
        markup.add(
            InlineKeyboardButton("📅 Today", callback_data="pstat_today"),
            InlineKeyboardButton("📆 Yesterday", callback_data="pstat_yesterday"),
            InlineKeyboardButton("📊 Last 3 Days", callback_data="pstat_last3"),
            InlineKeyboardButton("📈 Last 7 Days (Daily)", callback_data="pstat_last7"),
            InlineKeyboardButton("🗓 This Month", callback_data="pstat_month")
        )
        bot.send_message(message.chat.id, "📊 **Select Period to View Stats:**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("pstat_"))
def process_partner_stats(call):
    user_id = call.from_user.id
    period = call.data.split("_")[1]
    
    admin_data = admins_col.find_one({"user_id": user_id})
    primary_id = admin_data.get("primary_id", user_id)
    p_admin = admins_col.find_one({"user_id": primary_id})
    
    linked_admins = admins_col.find({"primary_id": primary_id})
    p_ids = [a["user_id"] for a in linked_admins]
    if primary_id not in p_ids: p_ids.append(primary_id)
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    is_sharing = p_admin.get("is_sharing", False)
    
    if period == "last7":
        msg = "📈 **PAST 7 DAYS DAILY REPORT**\n━━━━━━━━━━━━━━━━━━━━\n"
        total_7d = 0
        for i in range(7):
            d_start = today_start - timedelta(days=i)
            d_end = d_start + timedelta(days=1)
            txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gte": d_start, "$lt": d_end}}))
            day_sum = sum(t['amount'] for t in txns)
            total_7d += day_sum
            day_label = "Today" if i == 0 else ("Yesterday" if i == 1 else f"Day -{i} ({d_start.strftime('%d %b')})")
            msg += f"▫️ **{day_label}:** ₹{day_sum}\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"💰 **7-Day Total:** ₹{total_7d}\n"
        if is_sharing:
            msg += f"💵 **7-Day Income (30%):** ₹{total_7d * 0.30:.2f}\n"
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        return

    if period == "today":
        q_start, q_end = today_start, now
        title = "TODAY'S"
    elif period == "yesterday":
        q_start, q_end = today_start - timedelta(days=1), today_start
        title = "YESTERDAY'S"
    elif period == "last3":
        q_start, q_end = today_start - timedelta(days=2), now
        title = "LAST 3 DAYS'"
    else: # month
        q_start, q_end = month_start, now
        title = "THIS MONTH'S"

    period_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gte": q_start, "$lt": q_end}}))
    period_total = sum(t['amount'] for t in period_txns)
    
    month_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gte": month_start}}))
    month_total = sum(t['amount'] for t in month_txns)
    month_income = month_total * 0.30 if is_sharing else month_total

    last_cleared = p_admin.get("last_cleared", datetime.min)
    uncleared_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gt": last_cleared}}))
    uncleared_total = sum(t['amount'] for t in uncleared_txns)
    
    advance = p_admin.get("advance_received", 0)
    last_claimed = p_admin.get("last_claimed_amount", 0)
    pending_balance = (uncleared_total * 0.30) - advance if is_sharing else uncleared_total
    
    msg = f"📊 **{title} DASHBOARD**\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📥 **Total Received {title.title()}:** ₹{period_total}\n"
    if is_sharing:
        msg += f"💵 **{title.title()} Income (30%):** ₹{period_total * 0.30:.2f}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    
    if is_sharing:
        msg += f"⏳ **Total Pending Balance:** ₹{pending_balance:.2f}\n"
        msg += f"✅ **Last Claimed Balance:** ₹{last_claimed:.2f}\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        
    msg += f"📈 **Total Income This Month:** ₹{month_income:.2f}\n"
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("stat_"))
def process_owner_stats(call):
    if call.from_user.id != OWNER_ID: return
    period = call.data.split("_")[1]
    
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if period == "last7":
        msg = "📈 **GLOBAL PAST 7 DAYS DAILY BREAKDOWN**\n━━━━━━━━━━━━━━━━━━━━\n"
        total_7d = 0
        for i in range(7):
            d_start = today_start - timedelta(days=i)
            d_end = d_start + timedelta(days=1)
            txns = list(tx_col.find({"status": "done", "time": {"$gte": d_start, "$lt": d_end}}))
            day_sum = sum(t['amount'] for t in txns)
            total_7d += day_sum
            day_label = "Today" if i == 0 else ("Yesterday" if i == 1 else f"Day -{i} ({d_start.strftime('%d %b')})")
            msg += f"▫️ **{day_label}:** ₹{day_sum} ({len(txns)} txns)\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"💰 **Total 7-Day Collection:** ₹{total_7d}\n"
        bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        return

    query = {"status": "done"}
    if period == "today":
        query["time"] = {"$gte": today_start}
        time_text = "Today"
    elif period == "yesterday":
        query["time"] = {"$gte": today_start - timedelta(days=1), "$lt": today_start}
        time_text = "Yesterday"
    elif period == "last3":
        query["time"] = {"$gte": today_start - timedelta(days=2)}
        time_text = "Last 3 Days"
    elif period == "month":
        query["time"] = {"$gte": now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)}
        time_text = "This Month"
    else: 
        time_text = "Total All Time"

    transactions = list(tx_col.find(query))
    total_amount = sum(t['amount'] for t in transactions)
    
    stats_msg = f"👥 **Global Stats (All Admins)**\n🗓 **Period:** {time_text}\n\n💸 **Total Received:** ₹{total_amount}\n\n**Admin Wise Breakdown:**\n"
    
    main_admins = list(admins_col.find({"$expr": {"$eq": ["$user_id", "$primary_id"]}}))
    for main_admin in main_admins:
        sub_admins = list(admins_col.find({"primary_id": main_admin["user_id"]}))
        all_ids = [a["user_id"] for a in sub_admins]
        
        main_txns = [t for t in transactions if t.get('admin_id') in all_ids]
        if main_txns:
            main_total = sum(t['amount'] for t in main_txns)
            stats_msg += f"🔸 **{main_admin['name']} (Group):** ₹{main_total}\n"
            for sub in sub_admins:
                sub_txns = [t for t in main_txns if t.get('admin_id') == sub["user_id"]]
                if sub_txns:
                    sub_total = sum(t['amount'] for t in sub_txns)
                    stats_msg += f"   └ ID ({sub['name']}): ₹{sub_total}\n"

    bot.edit_message_text(stats_msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# ================= 3. एडमिन पैनल & UPI Management =================

@bot.message_handler(func=lambda msg: msg.text == "⚙️ Admin Panel")
def admin_panel(message):
    if message.from_user.id != OWNER_ID: return
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Add UPI", "📋 List & Manage UPIs")
    markup.add("👥 Manage Admins", "💰 Partner Balances")
    markup.add("⚙️ QR Setting (TXN ID)", "🗑 Remove Manual TXN")
    markup.add("⬅️ Back to Main")
    bot.send_message(message.chat.id, "⚙️ Admin Panel में आपका स्वागत है।", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "📋 List & Manage UPIs")
def list_manage_upis(message):
    if message.from_user.id != OWNER_ID: return
    upis = list(upi_col.find())
    if not upis:
        bot.send_message(message.chat.id, "❌ कोई भी UPI ID डेटाबेस में नहीं मिली।")
        return
        
    markup = InlineKeyboardMarkup(row_width=1)
    now = datetime.now()
    for u in upis:
        status = "🟢 Active"
        if u.get("waiting_until") and u["waiting_until"] > now:
            status = f"⏳ Wait ({u['waiting_until'].strftime('%H:%M')})"
        btn_text = f"{u['name']} | {u['group']} | {status} | {u['upi_id']}"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"viewupi_{str(u['_id'])}"))
        
    bot.send_message(message.chat.id, "📋 **मौजूदा UPI IDs की लिस्ट:**\nहटाने या डिटेल्स देखने के लिए किसी पर क्लिक करें:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("viewupi_"))
def view_upi_options(call):
    if call.from_user.id != OWNER_ID: return
    upi_id_str = call.data.split("_")[1]
    upi = upi_col.find_one({"_id": ObjectId(upi_id_str)})
    if not upi:
        return bot.answer_callback_query(call.id, "UPI नहीं मिली!")
        
    now = datetime.now()
    is_waiting = upi.get("waiting_until") and upi["waiting_until"] > now
    status_str = f"⏳ In Waiting till {upi['waiting_until'].strftime('%I:%M %p, %d %b')}" if is_waiting else "🟢 Active & Available"
    
    msg = (f"🏦 **UPI ID:** `{upi['upi_id']}`\n"
           f"👤 **Name:** {upi['name']}\n"
           f"📁 **Group:** {upi['group']}\n"
           f"📊 **Status:** {status_str}\n")
           
    markup = InlineKeyboardMarkup(row_width=1)
    if is_waiting:
        markup.add(InlineKeyboardButton("🟢 Remove Waiting (Make Active)", callback_data=f"unwait_{upi_id_str}"))
    markup.add(InlineKeyboardButton("🗑 Delete/Remove This UPI", callback_data=f"delupi_{upi_id_str}"))
    markup.add(InlineKeyboardButton("⬅️ Back to List", callback_data="back_to_upi_list"))
    
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_upi_list")
def back_to_upi_list_cb(call):
    if call.from_user.id != OWNER_ID: return
    upis = list(upi_col.find())
    markup = InlineKeyboardMarkup(row_width=1)
    now = datetime.now()
    for u in upis:
        status = "🟢 Active"
        if u.get("waiting_until") and u["waiting_until"] > now:
            status = f"⏳ Wait ({u['waiting_until'].strftime('%H:%M')})"
        btn_text = f"{u['name']} | {u['group']} | {status} | {u['upi_id']}"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"viewupi_{str(u['_id'])}"))
    bot.edit_message_text("📋 **मौजूदा UPI IDs की लिस्ट:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("unwait_"))
def unwait_upi_action(call):
    if call.from_user.id != OWNER_ID: return
    upi_id_str = call.data.split("_")[1]
    upi_col.update_one({"_id": ObjectId(upi_id_str)}, {"$unset": {"waiting_until": ""}})
    bot.answer_callback_query(call.id, "✅ UPI को Active कर दिया गया है!", show_alert=True)
    view_upi_options(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delupi_"))
def delete_upi_action(call):
    if call.from_user.id != OWNER_ID: return
    upi_id_str = call.data.split("_")[1]
    upi_col.delete_one({"_id": ObjectId(upi_id_str)})
    bot.answer_callback_query(call.id, "🗑 UPI ID हटा दी गई!", show_alert=True)
    back_to_upi_list_cb(call)

@bot.message_handler(func=lambda msg: msg.text == "⚙️ QR Setting (TXN ID)")
def toggle_qr_setting(message):
    if message.from_user.id != OWNER_ID: return
    setting = settings_col.find_one({"_id": "qr_setting"})
    current_status = setting.get("include_txn", True) if setting else True
    
    new_status = not current_status
    settings_col.update_one({"_id": "qr_setting"}, {"$set": {"include_txn": new_status}}, upsert=True)
    
    status_text = "🟢 ON" if new_status else "🔴 OFF"
    bot.send_message(message.chat.id, f"⚙️ **QR Prefilled TXN ID Setting:**\nअब से QR कोड में TXN ID **{status_text}** रहेगी।", parse_mode="Markdown")

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
    msg = bot.send_message(message.chat.id, "इस UPI का नाम बताएं:")
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
        "upi_id": upi_id, 
        "name": name, 
        "group": group, 
        "last_used": datetime.min,
        "waiting_until": None
    })
    bot.send_message(message.chat.id, f"✅ UPI ID '{upi_id}' सफलतापूर्वक जुड़ गई!", reply_markup=main_menu(message.from_user.id))

# --- Manage Admins Flow ---
@bot.message_handler(func=lambda msg: msg.text == "👥 Manage Admins")
def manage_admins_menu(message):
    if message.from_user.id != OWNER_ID: return
    
    main_admins = list(admins_col.find({"$expr": {"$eq": ["$user_id", "$primary_id"]}}))
    markup = InlineKeyboardMarkup(row_width=1)
    
    for adm in main_admins:
        markup.add(InlineKeyboardButton(f"👤 {adm['name']}", callback_data=f"mngadm_{adm['user_id']}"))
    markup.add(InlineKeyboardButton("➕ Add New Main Admin", callback_data="add_new_main_admin"))
    
    bot.send_message(message.chat.id, "👥 **Manage Admins:**\nकिसी भी एडमिन को चुनें या नया बनाएँ:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "add_new_main_admin")
def add_main_admin_cb(call):
    msg = bot.send_message(call.message.chat.id, "नए एडमिन की Telegram User ID भेजें:")
    bot.register_next_step_handler(msg, step_admin_name)

def step_admin_name(message):
    try:
        new_admin_id = int(message.text)
        msg = bot.send_message(message.chat.id, "नए एडमिन का नाम बताएं:")
        bot.register_next_step_handler(msg, step_admin_type, new_admin_id)
    except ValueError:
        bot.send_message(message.chat.id, "❌ अमान्य आईडी!")

def step_admin_type(message, new_admin_id):
    name = message.text
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("Regular (No Share)", "Sharing (30% Admin)")
    msg = bot.send_message(message.chat.id, "यह एडमिन किस प्रकार का है?", reply_markup=markup)
    bot.register_next_step_handler(msg, save_admin, new_admin_id, name)

def save_admin(message, new_admin_id, name):
    is_sharing = "Sharing" in message.text
    if admins_col.find_one({"user_id": new_admin_id}):
        bot.send_message(message.chat.id, "❌ यह एडमिन पहले से मौजूद है!", reply_markup=main_menu(message.from_user.id))
    else:
        admins_col.insert_one({
            "user_id": new_admin_id, "name": name, "is_sharing": is_sharing,
            "primary_id": new_admin_id, "advance_received": 0,
            "last_claimed_amount": 0, "last_cleared": datetime.now()
        })
        bot.send_message(message.chat.id, f"✅ Admin Added!\nName: {name}", reply_markup=main_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("mngadm_"))
def admin_details_manage(call):
    adm_id = int(call.data.split("_")[1])
    adm = admins_col.find_one({"user_id": adm_id})
    sub_ids = list(admins_col.find({"primary_id": adm_id}))
    
    msg = f"👤 **Admin:** {adm['name']}\n🆔 **Main ID:** `{adm_id}`\n\n**Linked Sub-IDs:**\n"
    for s in sub_ids:
        if s['user_id'] != adm_id:
            msg += f"🔹 {s['name']} (`{s['user_id']}`)\n"
            
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("➕ Add Sub-ID (Link)", callback_data=f"linknew_{adm_id}"))
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("linknew_"))
def ask_sub_id(call):
    adm_id = int(call.data.split("_")[1])
    msg = bot.send_message(call.message.chat.id, "लिंक करने के लिए नई Telegram User ID भेजें:")
    bot.register_next_step_handler(msg, save_linked_subid, adm_id)

def save_linked_subid(message, primary_id):
    try:
        new_id = int(message.text)
        if admins_col.find_one({"user_id": new_id}):
            return bot.send_message(message.chat.id, "❌ यह ID सिस्टम में पहले से मौजूद है।")
        p_admin = admins_col.find_one({"user_id": primary_id})
        admins_col.insert_one({
            "user_id": new_id, "name": p_admin["name"] + " (Sub)",
            "is_sharing": p_admin["is_sharing"], "primary_id": primary_id,
            "last_cleared": p_admin["last_cleared"]
        })
        bot.send_message(message.chat.id, f"✅ ID {new_id} सफलतापूर्वक {p_admin['name']} से लिंक हो गई!")
    except ValueError:
        bot.send_message(message.chat.id, "❌ अमान्य आईडी।")

# --- Partner Balance & Advance System ---
@bot.message_handler(func=lambda msg: msg.text == "💰 Partner Balances")
def partner_balances(message):
    if message.from_user.id != OWNER_ID: return
    
    all_partners = list(admins_col.find({"is_sharing": True}))
    main_partners = [p for p in all_partners if p.get("primary_id", p["user_id"]) == p["user_id"]]
    
    markup = InlineKeyboardMarkup(row_width=1)
    for partner in main_partners:
        partner_id = partner["user_id"]
        linked = list(admins_col.find({"primary_id": partner_id}))
        p_ids = [a["user_id"] for a in linked]
        
        last_cleared = partner.get("last_cleared", datetime.min)
        uncleared_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gt": last_cleared}}))
        uncleared_total = sum(t['amount'] for t in uncleared_txns)
        
        markup.add(InlineKeyboardButton(f"👤 {partner['name']} | Uncleared: ₹{uncleared_total}", callback_data=f"partner_{partner_id}"))
        
    bot.send_message(message.chat.id, "👥 **पार्टनर बैलेंस मैनेज करें:**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("partner_"))
def partner_details(call):
    if call.from_user.id != OWNER_ID: return
    partner_id = int(call.data.split("_")[1])
    partner = admins_col.find_one({"user_id": partner_id})
    
    linked = list(admins_col.find({"primary_id": partner_id}))
    p_ids = [a["user_id"] for a in linked]
    
    last_cleared = partner.get("last_cleared", datetime.min)
    uncleared_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gt": last_cleared}}))
    uncleared_total = sum(t['amount'] for t in uncleared_txns)
    
    share_30 = uncleared_total * 0.30
    advance = partner.get("advance_received", 0)
    net_payable = share_30 - advance
    
    msg = (f"👤 **Partner:** {partner['name']}\n"
           f"💸 **Total Uncleared Base:** ₹{uncleared_total}\n"
           f"🔹 **Partner Share (30%):** ₹{share_30:.2f}\n"
           f"💵 **Advance Given:** ₹{advance:.2f}\n"
           f"✅ **Net Pending Balance:** ₹{net_payable:.2f}\n")
           
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("➕ Add/Minus Advance", callback_data=f"giveadv_{partner_id}"))
    markup.add(InlineKeyboardButton("✅ Mark as Cleared (Claim)", callback_data=f"clearbal_{partner_id}_{net_payable}"))
    bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("giveadv_"))
def give_advance_start(call):
    if call.from_user.id != OWNER_ID: return
    partner_id = int(call.data.split("_")[1])
    msg = bot.send_message(call.message.chat.id, "अमाउंट लिखें (ऐड करने के लिए '2000' लिखें, घटाने के लिए '-500' लिखें):")
    bot.register_next_step_handler(msg, process_give_advance, partner_id)
    
def process_give_advance(message, partner_id):
    try:
        amt = float(message.text)
        partner = admins_col.find_one({"user_id": partner_id})
        
        linked = list(admins_col.find({"primary_id": partner_id}))
        p_ids = [a["user_id"] for a in linked]
        last_cleared = partner.get("last_cleared", datetime.min)
        uncleared_txns = list(tx_col.find({"status": "done", "admin_id": {"$in": p_ids}, "time": {"$gt": last_cleared}}))
        uncleared_total = sum(t['amount'] for t in uncleared_txns)
        
        old_advance = partner.get("advance_received", 0)
        old_pending = (uncleared_total * 0.30) - old_advance
        
        new_advance = old_advance + amt
        new_pending = (uncleared_total * 0.30) - new_advance
        
        admins_col.update_one({"user_id": partner_id}, {"$set": {"advance_received": new_advance}})
        bot.send_message(message.chat.id, f"✅ एडवांस अपडेट हो गया!")
        
        notify_msg = (
            f"🔔 **Advance Balance Update**\n\n"
            f"🔹 **Previous Pending Balance:** ₹{old_pending:.2f}\n"
            f"💵 **Advance Added by Owner:** ₹{amt:.2f}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"✅ **New Pending Balance:** ₹{new_pending:.2f}\n"
            f"*(If balance is in minus, you have taken extra advance)*"
        )
        try:
            bot.send_message(partner_id, notify_msg, parse_mode="Markdown")
        except:
            pass
            
    except ValueError:
        bot.send_message(message.chat.id, "❌ अमान्य अमाउंट।")

@bot.callback_query_handler(func=lambda call: call.data.startswith("clearbal_"))
def clear_partner_balance(call):
    if call.from_user.id != OWNER_ID: return
    parts = call.data.split("_")
    partner_id = int(parts[1])
    net_paid = float(parts[2])
    
    admins_col.update_one(
        {"user_id": partner_id}, 
        {"$set": {"last_cleared": datetime.now(), "last_claimed_amount": net_paid, "advance_received": 0}}
    )
    bot.answer_callback_query(call.id, "✅ बैलेंस सफलतापूर्वक क्लियर कर दिया गया ডান!", show_alert=True)
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

# ================= 4. My Saved QRs =================
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
    if not message.photo: return bot.send_message(message.chat.id, "❌ आपने फोटो नहीं भेजी।")
    photo_id = message.photo[-1].file_id
    msg = bot.send_message(message.chat.id, "📝 इस QR को किस नाम से सेव करना है?")
    bot.register_next_step_handler(msg, saved_add_step3, photo_id)

def saved_add_step3(message, photo_id):
    name = message.text
    msg = bot.send_message(message.chat.id, "🏦 इस QR की UPI ID भी भेजें:")
    bot.register_next_step_handler(msg, saved_add_final, photo_id, name)

def saved_add_final(message, photo_id, name):
    saved_qrs_col.insert_one({"admin_id": message.from_user.id, "photo_id": photo_id, "name": name, "upi_id": message.text})
    bot.send_message(message.chat.id, f"✅ QR '{name}' सफलतापूर्वक सेव हो गया!")

@bot.callback_query_handler(func=lambda call: call.data == "saved_list")
def saved_list_show(call):
    qrs = list(saved_qrs_col.find({"admin_id": call.from_user.id}))
    if not qrs: return bot.answer_callback_query(call.id, "❌ कोई सेव्ड QR नहीं मिला!", show_alert=True)
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(qr['name'], callback_data=f"show_qr_{str(qr['_id'])}") for qr in qrs]
    markup.add(*buttons)
    bot.edit_message_text("👇 अपने सेव्ड QR चुनें:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("show_qr_"))
def saved_show_specific(call):
    qr = saved_qrs_col.find_one({"_id": ObjectId(call.data.split("show_qr_")[1])})
    if qr:
        bot.send_photo(call.message.chat.id, qr['photo_id'], caption=f"👤 **Name:** {qr['name']}\n🏦 **UPI ID:** `{qr['upi_id']}`", parse_mode="Markdown")
        bot.answer_callback_query(call.id)
    else:
        bot.answer_callback_query(call.id, "QR not found!")

# ================= मेमोरी और स्टार्टअप चेक =================
def check_memory():
    print("\n🔄 MongoDB से पुरानी मेमोरी फेच की जा रही है...")
    try:
        upi_count = upi_col.count_documents({})
        admin_count = admins_col.count_documents({})
        tx_count = tx_col.count_documents({})
        qr_count = saved_qrs_col.count_documents({})
        
        print("✅ सारा डेटा सफलतापूर्वक लोड हो गया!")
        print(f"📊 डेटाबेस स्टेट्स:")
        print(f"  - 🏦 सेव्ड UPIs: {upi_count}")
        print(f"  - 👥 कुल Admins: {admin_count}")
        print(f"  - 💸 कुल Transactions: {tx_count}")
        print(f"  - 🖼 सेव्ड QRs: {qr_count}")
        print("\n🚀 Bot is running securely on the new VPS with memory loaded...")
    except Exception as e:
        print(f"❌ मेमोरी लोड करने में एरर (MongoDB Connection Issue): {e}")

# ================= BOT RUNNER =================
if __name__ == "__main__":
    check_memory()
    bot.infinity_polling()
