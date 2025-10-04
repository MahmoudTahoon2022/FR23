#!/usr/bin/env python3
import os
import time
import json
import signal
import logging
import threading
from typing import List, Optional

import paho.mqtt.client as mqtt
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("relay")

# --- Environment / Config ---
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

# Topics coming from ESP32 -> we forward them to Telegram
MQTT_TOPICS = [
    t.strip() for t in os.getenv("MQTT_TOPICS", "freezer/status,freezer/temp,freezer/door").split(",") if t.strip()
]
# Topic to publish commands from Telegram -> ESP32
MQTT_CMD_TOPIC = os.getenv("MQTT_CMD_TOPIC", "freezer/cmd")

# Topic where ESP32 publishes allowed chat ids (retained)
MQTT_CHATS_TOPIC = os.getenv("MQTT_CHATS_TOPIC", "freezer/relay/chats")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Legacy single chat id (optional now)
CHAT_ID_ENV = os.getenv("CHAT_ID", "").strip()

# Optional static allow-list from env (comma separated)
CHAT_IDS_ENV = os.getenv("CHAT_IDS", "").strip()

ALLOW_ANY_CHAT = os.getenv("ALLOW_ANY_CHAT", "false").lower() in ("1", "true", "yes", "y")

if not BOT_TOKEN:
    log.error("BOT_TOKEN is missing. Create a .env file based on .env.sample and set BOT_TOKEN.")
    raise SystemExit(1)

TELEGRAM_SEND = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
TELEGRAM_UPDATES = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

session = requests.Session()
session.headers.update({"Content-Type": "application/x-www-form-urlencoded"})

# ---------- Allowed chats handling ----------
allowed_chats = set()  # strings

def _normalize_ids(cands: List[str]) -> List[str]:
    out = []
    for x in cands:
        s = str(x).strip()
        if not s:
            continue
        # allow positive digits or -digits (supergroups)
        if s.startswith("-") and s[1:].isdigit():
            out.append(s)
        elif s.isdigit():
            out.append(s)
    return out

# seed from CHAT_IDS env if provided
if CHAT_IDS_ENV:
    allowed_chats.update(_normalize_ids(CHAT_IDS_ENV.split(",")))

# fallback to legacy CHAT_ID if provided
if CHAT_ID_ENV:
    allowed_chats.add(CHAT_ID_ENV)

def send_to_telegram(text: str, chat_id: Optional[str] = None, broadcast: bool = False):
    targets: List[str] = []
    if broadcast:
        # prefer dynamic list, otherwise fallback to CHAT_ID_ENV if exists
        if allowed_chats:
            targets = list(allowed_chats)
        elif CHAT_ID_ENV:
            targets = [CHAT_ID_ENV]
        else:
            log.warning("No chat IDs to broadcast to.")
            return
    elif chat_id:
        targets = [chat_id]
    else:
        # default behavior: use dynamic list or fallback
        if allowed_chats:
            targets = list(allowed_chats)
        elif CHAT_ID_ENV:
            targets = [CHAT_ID_ENV]
        else:
            log.warning("No chat IDs to send to.")
            return

    for cid in targets:
        try:
            resp = session.post(TELEGRAM_SEND, data={"chat_id": cid, "text": text}, timeout=10)
            if resp.status_code != 200:
                log.warning("Telegram send failed to %s: %s %s", cid, resp.status_code, resp.text[:200])
        except Exception as e:
            log.exception("Telegram send error to %s: %s", cid, e)

def _update_allowed_from_payload(payload: str) -> bool:
    """
    Accepts CSV like:  "12345,-100778899,67890"
    Or JSON like:     {"admin":"123","manager":"456","users":["789","-100777"]}
    """
    try:
        payload = (payload or "").strip()
        if not payload:
            return False

        ids: List[str] = []
        if payload.startswith("{"):
            obj = json.loads(payload)
            for k in ("admin", "manager"):
                v = obj.get(k)
                if isinstance(v, str):
                    ids.append(v)
            users = obj.get("users", [])
            if isinstance(users, list):
                for u in users:
                    if isinstance(u, (str, int)):
                        ids.append(str(u))
        else:
            ids.extend([s for s in payload.split(",")])

        norm = _normalize_ids(ids)
        if not norm:
            return False

        allowed_chats.clear()
        allowed_chats.update(norm)
        log.info("Allowed chats updated: %s", ",".join(sorted(allowed_chats)))
        # نبلّغ الشاتات (لو كانت معروفة سابقًا) إن القائمة اتحدّثت
        send_to_telegram("✅ تم تحديث قائمة الشّاتات المسموح بها.", broadcast=True)
        return True
    except Exception as e:
        log.exception("Failed to parse chat list payload: %s", e)
        return False

# ---------- MQTT (ESP32 → Telegram) ----------
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        # subscribe to data topics
        for topic in MQTT_TOPICS:
            client.subscribe(topic, qos=1)
            log.info("Subscribed to topic: %s", topic)
        # subscribe to chat list topic (retained)
        client.subscribe(MQTT_CHATS_TOPIC, qos=1)
        log.info("Subscribed to chats topic: %s", MQTT_CHATS_TOPIC)

        send_to_telegram("✅ Relay connected to MQTT broker and ready.", broadcast=True)
    else:
        log.error("MQTT connect failed with rc=%s", rc)

def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    topic = msg.topic

    if topic == MQTT_CHATS_TOPIC:
        # update allowed chats from ESP32
        if _update_allowed_from_payload(payload):
            log.info("Allowed chats updated from MQTT retained message.")
        return

    # forward other topics to Telegram (broadcast to all allowed)
    text = f"📡 {topic}\n{payload}"
    send_to_telegram(text, broadcast=True)

def on_disconnect(client, userdata, rc):
    log.warning("Disconnected from MQTT (rc=%s). Reconnecting...", rc)

# ---------- Telegram (Telegram → MQTT) ----------
HELP_TEXT = (
    "🤖 أوامر متاحة:\n"
    "/temp  — قراءة الحرارة الآن\n"
    "/door  — حالة الباب الآن\n"
    "/status — حالة الجهاز\n"
    "/reboot — إعادة تشغيل ESP32 (اختياري)\n"
)

def map_text_to_cmd(txt: str) -> Optional[str]:
    t = (txt or "").strip().lower()
    if t == "/temp":   return "TEMP?"
    if t == "/door":   return "DOOR?"
    if t == "/status": return "STATUS?"
    if t == "/reboot": return "REBOOT"
    if t in ("/start", "/help"): return None
    return None

def _is_chat_allowed(chat_id: str) -> bool:
    if allowed_chats:
        return chat_id in allowed_chats
    if CHAT_ID_ENV:
        return chat_id == CHAT_ID_ENV
    return ALLOW_ANY_CHAT  # last resort (if enabled)

def telegram_poller(stop_event: threading.Event, mqtt_client: mqtt.Client):
    offset = 0
    while not stop_event.is_set():
        try:
            r = session.get(TELEGRAM_UPDATES, params={"timeout": 25, "offset": offset}, timeout=30)
            data = r.json()
            if not data.get("ok"):
                time.sleep(1)
                continue

            for upd in data.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue

                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = (msg.get("text") or "").strip()
                if not text:
                    continue

                # allow only authorized chats
                if not _is_chat_allowed(chat_id):
                    # تجاهل بصمت (أو أرسل رسالة رفض لو حابب)
                    log.info("Ignoring message from unauthorized chat: %s", chat_id)
                    continue

                if text.lower() in ("/start", "/help"):
                    send_to_telegram(HELP_TEXT, chat_id=chat_id)
                    continue

                cmd = map_text_to_cmd(text)
                if cmd:
                    try:
                        mqtt_client.publish(MQTT_CMD_TOPIC, cmd, qos=1, retain=False)
                        send_to_telegram(f"↗️ تم إرسال الأمر إلى MQTT: {cmd}", chat_id=chat_id)
                    except Exception as e:
                        send_to_telegram("❌ فشل إرسال الأمر إلى MQTT", chat_id=chat_id)
                        log.exception("Publish error: %s", e)
                else:
                    send_to_telegram("ℹ️ أمر غير معروف.\n" + HELP_TEXT, chat_id=chat_id)

        except Exception as e:
            log.exception("Telegram poll error: %s", e)
            time.sleep(2)

def main():
    client = mqtt.Client(client_id=os.getenv("MQTT_CLIENT_ID", "mqtt-telegram-relay"), clean_session=True)
    if MQTT_USER or MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)

    # TLS (اختياري) لو بروكر 8883
    if str(MQTT_PORT) == "8883":
        try:
            client.tls_set()  # يستخدم شهادات النظام
            log.info("TLS enabled for port 8883")
        except Exception as e:
            log.warning("TLS setup failed: %s", e)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)

    stop_event = threading.Event()
    def handle_sig(*_):
        stop_event.set()
        log.info("Stopping relay ...")
    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    t = threading.Thread(target=telegram_poller, args=(stop_event, client), daemon=True)
    t.start()

    while not stop_event.is_set():
        try:
            client.loop(timeout=1.0)
            time.sleep(0.05)
        except Exception as e:
            log.exception("MQTT loop error: %s", e)
            time.sleep(2)

    try:
        client.disconnect()
    except Exception:
        pass

if __name__ == "__main__":
    main()
