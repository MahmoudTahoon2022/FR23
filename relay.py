#include <WiFi.h>
#include <PubSubClient.h>
#include "config.h"

WiFiClient espClient;
PubSubClient mqtt(espClient);

unsigned long lastPublish = 0;
bool doorOpen = false;
float lastTemp = 4.2;

void wifiConnect() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(400);
  }
  Serial.println("\nWiFi OK " + WiFi.localIP().toString());
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "TEMP?") {
    // هنا بديل للسينسور الحقيقي (للتجربة فقط)
    char buf[64];
    snprintf(buf, sizeof(buf), "%.2f C", lastTemp);
    mqtt.publish(TOPIC_TEMP, buf, true);
  } else if (cmd == "DOOR?") {
    mqtt.publish(TOPIC_DOOR, doorOpen ? "Door Open" : "Door Closed", true);
  } else if (cmd == "STATUS?") {
    String s = "OK IP=" + WiFi.localIP().toString() + " RSSI=" + String(WiFi.RSSI());
    mqtt.publish(TOPIC_STATUS, s.c_str(), false);
  } else if (cmd == "REBOOT") {
    mqtt.publish(TOPIC_STATUS, "Rebooting...", false);
    delay(200);
    ESP.restart();
  }
}

void onMqtt(char* topic, byte* payload, unsigned int length) {
  String t = String(topic);
  String p;
  p.reserve(length + 1);
  for (unsigned int i = 0; i < length; i++) p += (char)payload[i];

  if (t == TOPIC_CMD) {
    handleCommand(p);
  }
}

void mqttReconnect() {
  while (!mqtt.connected()) {
    Serial.print("Connecting MQTT ... ");
    if (MQTT_USER[0] != '\0' || MQTT_PASS[0] != '\0') {
      if (mqtt.connect(DEVICE_TAG, MQTT_USER, MQTT_PASS)) {
        Serial.println("ok");
      } else {
        Serial.println("fail, rc=" + String(mqtt.state()));
        delay(1000);
      }
    } else {
      if (mqtt.connect(DEVICE_TAG)) {
        Serial.println("ok");
      } else {
        Serial.println("fail, rc=" + String(mqtt.state()));
        delay(1000);
      }
    }
  }

  // اشترك في توبك الأوامر بعد الاتصال
  mqtt.subscribe(TOPIC_CMD, 1);
  mqtt.publish(TOPIC_STATUS, "✅ " DEVICE_TAG " connected & subscribed to cmd");
}

void setup() {
  Serial.begin(115200);
  wifiConnect();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqtt);
  mqttReconnect();

  // إشارة بدء
  mqtt.publish(TOPIC_STATUS, "✅ " DEVICE_TAG " booted");
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) wifiConnect();
  if (!mqtt.connected()) mqttReconnect();
  mqtt.loop();

  // جزء تجريبي للنشر الدوري كما في كودك الحالي
  unsigned long now = millis();
  if (now - lastPublish > 5000) {
    lastPublish = now;

    // حرارة وهمية للتجارب
    lastTemp += 0.1;
    if (lastTemp > 6.0) lastTemp = 4.0;

    char buf[64];
    snprintf(buf, sizeof(buf), "%.2f C", lastTemp);
    mqtt.publish(TOPIC_TEMP, buf, true);

    // حالة باب تتبدل للتجارب
    doorOpen = !doorOpen;
    mqtt.publish(TOPIC_DOOR, doorOpen ? "Door Open" : "Door Closed", true);

    mqtt.publish(TOPIC_STATUS, "Heartbeat");
  }
}
