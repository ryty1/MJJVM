#!/opt/mjjvm/mjjvm-venv/bin/python3
# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
import time
import json
import os
import sys
import telegram
from telegram.ext import Updater, CommandHandler
import logging
from logging.handlers import RotatingFileHandler
import threading
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import warnings

# ---------------------------- 配置 ----------------------------
URLS = {
    "白银区": "https://www.mjjvm.com/cart?fid=1&gid=1",
    "黄金区": "https://www.mjjvm.com/cart?fid=1&gid=2",
    "钻石区": "https://www.mjjvm.com/cart?fid=1&gid=3",
    "星耀区": "https://www.mjjvm.com/cart?fid=1&gid=4",
    "特别活动区": "https://www.mjjvm.com/cart?fid=1&gid=6",
}

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "max-age=0",
    "Referer": "https://www.mjjvm.com",
    "Sec-CH-UA": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
}

TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_IDS = os.getenv("TG_CHAT_IDS", "").split(",")

INTERVAL = 20  # 秒
DATA_FILE = "stock_data.json"
LOG_FILE = "stock_out.log"

# FlareSolverr 配置
FLARESOLVERR_URL = "http://127.0.0.1:8191/v1"
FLARE_SESSION = None  # 全局复用 session id
FLARE_SESSION_FILE = "flaresolverr_session.json"

# ---------------------------- 日志 ----------------------------
warnings.filterwarnings("ignore", category=FutureWarning)
logger = logging.getLogger("StockMonitor")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
console_handler = logging.StreamHandler(stream=sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1*1024*1024, backupCount=1, encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ---------------------------- FlareSolverr Session 管理 ----------------------------
def save_session_id(session_id):
    try:
        with open(FLARE_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"session": session_id}, f)
    except Exception as e:
        logger.error("保存  session 失败: %s", e)

def load_session_id():
    if os.path.exists(FLARE_SESSION_FILE):
        try:
            with open(FLARE_SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("session")
        except:
            return None
    return None

def flaresolverr_request(payload):
    try:
        resp = requests.post(FLARESOLVERR_URL, json=payload, timeout=70)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("请求失败: %s", e)
        return None

def create_session():
    global FLARE_SESSION
    if FLARE_SESSION:
        delete_session(FLARE_SESSION)
    payload = {"cmd": "sessions.create"}
    data = flaresolverr_request(payload)
    if data and "session" in data:
        FLARE_SESSION = data["session"]
        save_session_id(FLARE_SESSION)
        logger.info("新建 session: %s", FLARE_SESSION)
        return True
    else:
        logger.error("新建 session 失败")
        FLARE_SESSION = None
        return False

def delete_session(session_id):
    payload = {"cmd": "sessions.destroy", "session": session_id}
    _ = flaresolverr_request(payload)

def fetch_with_flaresolverr(url):
    global FLARE_SESSION
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }
    if FLARE_SESSION:
        payload["session"] = FLARE_SESSION

    data = flaresolverr_request(payload)
    if data and "solution" in data:
        return data["solution"]["response"]

    # session 失效
    logger.warning("会话失效，重建 session...")
    if FLARE_SESSION:
        delete_session(FLARE_SESSION)
        FLARE_SESSION = None
    if create_session():
        payload["session"] = FLARE_SESSION
        data = flaresolverr_request(payload)
        if data and "solution" in data:
            return data["solution"]["response"]

    raise Exception("获取页面失败")

def init_session():
    global FLARE_SESSION
    old_session = load_session_id()
    if old_session:
        logger.info("加载已保存的 session: %s", old_session)
        FLARE_SESSION = old_session
        try:
            test_url = list(URLS.values())[0]
            _ = fetch_with_flaresolverr(test_url)
            logger.info("复用旧 session 成功")
            return
        except Exception as e:
            logger.warning("旧 session 已失效: %s", e)
            delete_session(old_session)

    # 新建 session
    create_session()

# ---------------------------- 工具函数 ----------------------------
def load_previous_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def group_by_region(all_products):
    grouped = {}
    for key, info in all_products.items():
        region = info.get("region", "未知地区")
        grouped.setdefault(region, []).append(info)
    return grouped

# 数字会员值 -> 文字名称映射
MEMBER_NAME_MAP = {
    1: "社区成员",
    2: "白银会员",
    3: "黄金会员",
    4: "钻石会员",
    5: "星曜会员"
}


# ---------------------------- TG 消息 ----------------------------
def send_telegram(messages):
    if not messages:
        return

    bot = telegram.Bot(token=TG_TOKEN)

    for msg in messages:
        html_msg = ""
        delete_delay = None
        reply_markup = None
        region = msg.get("region", "未知地区")

        member_text = ""
        if msg.get("member_only", 0):
            member_name = MEMBER_NAME_MAP.get(msg["member_only"], "会员")
            member_text = f"要求：<b>{member_name}</b>\n"

        if msg["type"] == "上架":
            prefix = "🟢"
            html_msg += (
                f"{prefix} <b>{msg['type']} - {region}</b>\n\n"
                f"名称: <b>{msg['name']}</b>\n"
                f"库存: <b>{msg['stock']}</b>\n"
                f"{member_text}"
            )
            if msg.get("config"):
                html_msg += f"配置:\n<pre>{msg['config']}</pre>\n"
            button = InlineKeyboardButton(text="快速进入通道", url=msg['url'])
            reply_markup = InlineKeyboardMarkup([[button]])

        elif msg["type"] == "库存变化":
            prefix = "🟡"
            html_msg += (
                f"{prefix} <b>{msg['type']} - {region}</b>\n"
                f"名称: <b>{msg['name']}</b>\n"
                f"库存: <b>{msg['stock']}</b>\n"
                f"{member_text}\n"
            )
            delete_delay = 5

        else:  # 售罄
            prefix = "🔴"
            html_msg += (
                f"{prefix} <b>{msg['type']} - {region}</b>\n"
                f"名称: <b>{msg['name']}</b>\n"
                f"库存: <b>{msg['stock']}</b>\n"
                f"{member_text}\n"
            )

        for chat_id in TG_CHAT_IDS:
            try:
                sent_msg = bot.send_message(
                    chat_id=chat_id,
                    text=html_msg,
                    parse_mode=telegram.ParseMode.HTML,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error("TG 推送失败 %s: %s", chat_id, e)
                continue

            if delete_delay:
                def delete_msg_after(delay, chat_id=chat_id, message_id=sent_msg.message_id):
                    time.sleep(delay)
                    try:
                        bot.delete_message(chat_id=chat_id, message_id=message_id)
                    except:
                        pass
                threading.Thread(target=delete_msg_after, args=(delete_delay,)).start()

# ---------------------------- 页面解析 ----------------------------
def parse_products(html, url, region):
    soup = BeautifulSoup(html, "html.parser")
    products = {}

    MEMBER_MAP = {
        "成员": 1,
        "白银会员": 2,
        "黄金会员": 3,
        "钻石会员": 4,
        "星曜会员": 5,
    }

    for card in soup.select("div.card.cartitem"):
        name_tag = card.find("h4")
        if not name_tag:
            continue
        name = name_tag.get_text(strip=True)

        config_items = []
        member_only = 0
        for li in card.select("ul.vps-config li"):
            text = li.get_text(" ", strip=True)
            matched = False
            for key, value in MEMBER_MAP.items():
                if key in text:
                    member_only = value
                    matched = True
                    break
            if matched:
                continue
            config_items.append(text)
        config = "\n".join(config_items)

        stock_tag = card.find("p", class_="card-text")
        stock = 0
        if stock_tag:
            try:
                stock = int(stock_tag.get_text(strip=True).split("库存：")[-1])
            except:
                stock = 0

        price_tag = card.select_one("a.cart-num")
        price = price_tag.get_text(strip=True) if price_tag else "未知"

        link_tag = card.select_one("div.card-footer a")
        pid = None
        if link_tag and "pid=" in link_tag.get("href", ""):
            pid = link_tag["href"].split("pid=")[-1]

        products[f"{region} - {name}"] = {
            "name": name,
            "config": config,
            "stock": stock,
            "price": price,
            "member_only": member_only,
            "url": url,
            "pid": pid,
            "region": region
        }

    return products

# ---------------------------- /vps 命令 ----------------------------
REGION_FLAGS = {
    "白银区": "🥈",
    "黄金区": "🏅",
    "钻石区": "💎",
    "星耀区": "🏆",
    "特别活动区": "🎁",
}

def delete_after(context, chat_id, msg_ids, delay):
    """延迟删除消息"""
    def _delete():
        time.sleep(delay)
        for mid in msg_ids:
            try:
                context.bot.delete_message(chat_id, mid)
            except Exception as e:
                logger.error("删除消息失败: %s", e)
            time.sleep(0.2)
    threading.Thread(target=_delete, daemon=True).start()

def vps_command(update, context):
    """手动查看当前所有地区的商品库存 (仅群管理员可用)"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # --- 权限检查 ---
    try:
        member = context.bot.get_chat_member(chat_id, user_id)
        if member.status not in ["administrator", "creator"]:
            warn_msg = update.message.reply_text("⚠️ 只有群管理员才能使用此命令。")
            delete_after(context, chat_id, [update.message.message_id, warn_msg.message_id], 3)
            return
    except BadRequest as e:
        logger.error("获取用户权限失败: %s", e)
        return

    # --- 读取缓存数据 ---
    current_data = load_previous_data()  # stock_data.json
    mjjvm_lines = []

    if not current_data:
        mjjvm_lines.append("📦 暂无库存数据，请等待下一次监控刷新。")
    else:
        for region, products in current_data.items():
            flag = REGION_FLAGS.get(region, "🌍")
            mjjvm_lines.append(f"{flag} <b>{region}</b>:")
            for p in products:
                stock = p.get("stock")
                if stock is None or stock < 0:
                    status, stock_text = "🟡", "未知"
                elif stock == 0:
                    status, stock_text = "🔴", "0"
                else:
                    status, stock_text = "🟢", str(stock)

                member_level = p.get("member_only", 0)
                vip = "月费服务" if member_level == 0 else MEMBER_NAME_MAP.get(member_level, "会员")

                name = p.get("name", "未知商品")
                mjjvm_lines.append(f"   {status} {name} | 库存: {stock_text} | {vip}")
            mjjvm_lines.append("")

    final_text = "🖥️ VPS库存情况：\n━━━━━━━━━━━━━━━━━━\n" + "\n".join(mjjvm_lines)

    sent_msg = context.bot.send_message(
        chat_id=chat_id,
        text=final_text,
        parse_mode=telegram.ParseMode.HTML
    )

    # 60秒后删除
    delete_after(context, chat_id, [update.message.message_id, sent_msg.message_id], 10)

# ---------------------------- TG Bot 启动 ----------------------------
def start_telegram_bot():
    updater = Updater(TG_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("vps", vps_command))
    updater.start_polling()

# ---------------------------- TG Bot 启动 ----------------------------
def start_telegram_bot():
    updater = Updater(TG_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("vps", vps_command))
    updater.start_polling()

# ---------------------------- 主循环 ----------------------------
consecutive_fail_rounds = 0

def main_loop():
    global consecutive_fail_rounds
    prev_data_raw = load_previous_data()
    prev_data = {}
    for region, plist in prev_data_raw.items():
        for p in plist:
            prev_data[f"{region} - {p['name']}"] = p

    logger.info("库存监控启动，每 %s 秒检查一次...", INTERVAL)

    while True:
        logger.info("正在检查库存...")
        all_products = {}
        success_count = 0
        fail_count = 0
        success = False

        for region, url in URLS.items():
            success_this_url = False
            for attempt in range(3):
                try:
                    html = fetch_with_flaresolverr(url)
                    products = parse_products(html, url, region)
                    all_products.update(products)
                    success_this_url = True
                    logger.info("[%s] 请求成功 (第 %d 次尝试)", region, attempt + 1)
                    break
                except Exception as e:
                    logger.warning("[%s] 请求失败 (第 %d 次尝试): %s", region, attempt + 1, e)
                    time.sleep(2)

            if success_this_url:
                success = True
                success_count += 1
            else:
                fail_count += 1
                logger.error("[%s] 请求失败: 尝试 3 次均失败", region)

        logger.info("本轮请求完成: 成功 %d / %d, 失败 %d", success_count, len(URLS), fail_count)

        if success_count == 0:
            consecutive_fail_rounds += 1
            logger.warning("本轮全部请求失败，连续失败轮数: %d", consecutive_fail_rounds)
        else:
            consecutive_fail_rounds = 0

        if consecutive_fail_rounds >= 10:
            try:
                bot = telegram.Bot(token=TG_TOKEN)
                alert_msg = f"⚠️ 警告：库存监控请求失败，请检查网络或服务器！"
                for chat_id in TG_CHAT_IDS:
                    bot.send_message(chat_id=chat_id, text=alert_msg)
            except Exception as e:
                logger.error("TG报警发送失败: %s", e)
            consecutive_fail_rounds = 0

        if not success:
            logger.warning("本轮请求全部失败，跳过数据更新。")
            time.sleep(INTERVAL)
            continue

        messages = []
        for name, info in all_products.items():
            if info.get("member_only", 0) == 0:
                continue
            prev_stock = prev_data.get(name, {}).get("stock", 0)
            curr_stock = info["stock"]
            msg_type = None
            if prev_stock == 0 and curr_stock > 0:
                msg_type = "上架"
            elif prev_stock > 0 and curr_stock == 0:
                msg_type = "售罄"
            elif prev_stock != curr_stock:
                msg_type = "库存变化"

            if msg_type:
                msg = {
                    "type": msg_type,
                    "name": info["name"],
                    "stock": curr_stock,
                    "config": info.get('config', ''),
                    "member_only": info.get("member_only", 0),
                    "url": info['url'],
                    "region": info.get("region", "未知地区")
                }
                messages.append(msg)
                member_name = MEMBER_NAME_MAP.get(info.get("member_only", 0), "会员")
                logger.info("%s - %s  |  库存: %s  |  %s", msg_type, info["name"], curr_stock, member_name)

        if messages:
            send_telegram(messages)

        grouped_data = group_by_region(all_products)
        save_data(grouped_data)
        prev_data = all_products

        logger.info("当前库存快照:")
        for name, info in all_products.items():
            member_name = MEMBER_NAME_MAP.get(info.get("member_only", 0), "会员")
            logger.info("- [%s] %s  |  库存: %s  |  %s", info.get("region", "未知地区"), info["name"], info["stock"], member_name)

        time.sleep(INTERVAL)

# ---------------------------- 启动 ----------------------------
if __name__ == "__main__":
    init_session()
    threading.Thread(target=start_telegram_bot, daemon=True).start()
    main_loop()
