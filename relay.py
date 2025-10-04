
#!/usr/bin/env python3
import os
import time
import json
import signal
import logging
from typing import List
from urllib.parse import quote_plus

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
MQTT_TOPICS = [t.strip() for t in os.getenv("MQTT_TOPICS", "freezer/status,freezer/temp,freezer/door").split(",") if t.strip()]
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "mqtt-telegram-relay")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

if not BOT_TOKEN or not CHAT_ID:
    log.error("BOT_TOKEN and/or CHAT_ID are missing. Create a .env file based on .env.sample.")
    raise SystemExit(1)

session = requests.Session()
session.headers.update({"Content-Type": "application/x-www-form-urlencoded"})

def send_to_telegram(text: str):
    try:
        resp = session.post(TELEGRAM_API, data={
            "chat_id": CHAT_ID,
            "text": text
        }, timeout=10)
        if resp.status_code != 200:
            log.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.exception("Telegram send error: %s", e)

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("Connected to MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        for topic in MQTT_TOPICS:
            client.subscribe(topic, qos=1)
            log.info("Subscribed to topic: %s", topic)
        send_to_telegram("✅ Relay connected to MQTT broker and ready.")
    else:
        log.error("MQTT connect failed with rc=%s", rc)

def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    text = f"📡 *{msg.topic}*\n{payload}"
    # Telegram MarkdownV2 escaping minimal (we avoid markdown parse by not setting parse_mode)
    send_to_telegram(text)

def on_disconnect(client, userdata, rc):
    log.warning("Disconnected from MQTT (rc=%s). Reconnecting...", rc)

def main():
    client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
    if MQTT_USER or MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)

    # Graceful shutdown
    stop = False
    def handle_sig(*_):
        nonlocal stop
        stop = True
        log.info("Stopping relay ...")
    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    # Loop forever with reconnect
    while not stop:
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
