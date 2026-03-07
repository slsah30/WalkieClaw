import socket
import os

LOCAL_HOST = "0.0.0.0"
LOCAL_PORT = int(os.environ.get("RELAY_LOCAL_PORT", 12345))
VPS_HOST = os.environ.get("RELAY_VPS_HOST", "<YOUR_VPS_TAILSCALE_IP>")
VPS_PORT = int(os.environ.get("RELAY_VPS_PORT", 12345))

def main():
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
            sock.sendto(data, (VPS_HOST, VPS_PORT))

            if data == b"START":
                # Send ESP32's real IP to bridge so it can connect ESPHome API
                sock.sendto(f"ESP_IP:{addr[0]}".encode(), (VPS_HOST, VPS_PORT))
                print(f"[{addr[0]}] Recording started -> VPS")
            elif data == b"STOP":
                print(f"[{addr[0]}] Recording stopped -> VPS")

if __name__ == "__main__":
    main()