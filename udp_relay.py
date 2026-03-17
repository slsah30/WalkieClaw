"""
UDP relay for WalkieClaw voice bridge.

Sits between the ESP32 and the VPS bridge, forwarding UDP audio packets
in both directions. Useful when the ESP32 can't reach the VPS directly
(e.g., apartment WiFi blocking outbound UDP).

Usage:
    RELAY_VPS_HOST=100.x.x.x python udp_relay.py

Environment variables:
    RELAY_VPS_HOST  - VPS IP address (required, no default)
    RELAY_VPS_PORT  - VPS UDP port (default: 12345)
    RELAY_LOCAL_PORT - Local listen port (default: 12345)
"""

import socket
import os
import sys

LOCAL_HOST = "0.0.0.0"
LOCAL_PORT = int(os.environ.get("RELAY_LOCAL_PORT", 12345))
VPS_HOST = os.environ.get("RELAY_VPS_HOST", "<YOUR_VPS_TAILSCALE_IP>")
VPS_PORT = int(os.environ.get("RELAY_VPS_PORT", 12345))

def main():
    # Validate VPS_HOST before attempting to use it
    if VPS_HOST.startswith("<") or VPS_HOST == "":
        print("ERROR: RELAY_VPS_HOST is not set or is still a placeholder.", file=sys.stderr)
        print("Set it to your VPS IP address, e.g.:", file=sys.stderr)
        print("  RELAY_VPS_HOST=100.x.x.x python udp_relay.py", file=sys.stderr)
        sys.exit(1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((LOCAL_HOST, LOCAL_PORT))
    print(f"UDP relay listening on {LOCAL_HOST}:{LOCAL_PORT}")
    print(f"Forwarding to {VPS_HOST}:{VPS_PORT}")

    esp_addr = None

    while True:
        data, addr = sock.recvfrom(4096)

        if addr[0] == VPS_HOST:
            if esp_addr:
                sock.sendto(data, esp_addr)
        else:
            esp_addr = addr
            try:
                sock.sendto(data, (VPS_HOST, VPS_PORT))
            except OSError as e:
                print(f"ERROR: Failed to send to {VPS_HOST}:{VPS_PORT}: {e}", file=sys.stderr)
                print("Check that RELAY_VPS_HOST is a valid, reachable IP address.", file=sys.stderr)
                continue

            if data.startswith(b"START"):
                # Send ESP32's real IP to bridge so it can connect ESPHome API
                sock.sendto(f"ESP_IP:{addr[0]}".encode(), (VPS_HOST, VPS_PORT))
                print(f"[{addr[0]}] Recording started -> VPS")
            elif data == b"STOP":
                print(f"[{addr[0]}] Recording stopped -> VPS")

if __name__ == "__main__":
    main()
