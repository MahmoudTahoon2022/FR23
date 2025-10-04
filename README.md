
# MQTT → Telegram Relay

وسيط بسيط يحوّل أي رسالة MQTT على Topics محددة إلى رسالة تيليجرام.

## المتطلبات
- Python 3.9+
- Broker MQTT مثل Mosquitto (محليًا أو على خادم)
- إنشاء بوت تيليجرام (BotFather) ومعرفة `BOT_TOKEN`
- معرفة `CHAT_ID` (رقم)

## التثبيت السريع
```bash
cd relay
python3 -m venv .venv
source .venv/bin/activate  # في ويندوز: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.sample .env
nano .env  # اكتب القيم
python relay.py
```

## تشغيل كخدمة (Linux systemd)
1. عدّل المسارات في `relay.service` حسب مكان المشروع والمستخدم.
2. انسخ الملف:
   ```bash
   sudo cp relay.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable relay
   sudo systemctl start relay
   sudo systemctl status relay
   ```

## الاختبار
- من أي عميل MQTT:
  ```bash
  mosquitto_pub -h 127.0.0.1 -t freezer/status -m "Door Open"
  ```
- ستصلك رسالة تيليجرام فورًا.

## ملاحظات أمان
- خزن التوكن داخل `.env` فقط.
- لو هتعمل نشر عام، استخدم TLS في MQTT (منفذ 8883) وحسابات/صلاحيات.
