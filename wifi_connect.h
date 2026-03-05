#pragma once
#include <esp_wifi.h>
#include <cstring>

static void connect_to_wifi(const std::string &ssid, const std::string &password) {
    wifi_config_t wifi_cfg;
    memset(&wifi_cfg, 0, sizeof(wifi_cfg));
    strncpy((char*)wifi_cfg.sta.ssid, ssid.c_str(), sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char*)wifi_cfg.sta.password, password.c_str(), sizeof(wifi_cfg.sta.password) - 1);
    wifi_cfg.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;

    esp_wifi_disconnect();
    esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
    esp_wifi_connect();

    ESP_LOGW("wifi_connect", "Connecting to new SSID: %s", ssid.c_str());
}
