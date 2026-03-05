#pragma once
#include <lwip/sockets.h>
#include <lwip/netdb.h>
#include <cstring>

class UDPStream {
 public:
  void setup(const char* host, uint16_t port) {
    sock_ = ::lwip_socket(AF_INET, SOCK_DGRAM, 0);
    if (sock_ < 0) return;
    memset(&dest_, 0, sizeof(dest_));
    dest_.sin_family = AF_INET;
    dest_.sin_port = htons(port);
    inet_pton(AF_INET, host, &dest_.sin_addr);
    ready_ = true;
  }

  void send(const uint8_t* data, size_t len) {
    if (!ready_) return;
    ::lwip_sendto(sock_, data, len, 0,
                  (struct sockaddr*)&dest_, sizeof(dest_));
  }

  void send_marker(const char* marker) {
    if (!ready_) return;
    ::lwip_sendto(sock_, marker, strlen(marker), 0,
                  (struct sockaddr*)&dest_, sizeof(dest_));
  }

 private:
  int sock_ = -1;
  struct sockaddr_in dest_{};
  bool ready_ = false;
};

static UDPStream udp_stream;
