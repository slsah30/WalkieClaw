---
name: WalkieClaw
description: Control your WalkieClaw ESP32 voice assistant — switch WiFi, send notifications, set timers/reminders, and check device status. Requires the WalkieClaw bridge running on your network or VPS.
version: 1.0.0
homepage: https://github.com/slsah30/WalkieClaw
metadata:
  openclaw:
    requires:
      env:
        - WALKIECLAW_BRIDGE_URL
        - WALKIECLAW_API_KEY
      bins:
        - curl
    primaryEnv: WALKIECLAW_API_KEY
    emoji: "\U0001F99E"
---

# WalkieClaw Voice Assistant Control

Control your [WalkieClaw](https://github.com/slsah30/WalkieClaw) ESP32 voice terminal through OpenClaw. WalkieClaw is a tiny AI walkie-talkie built on the AIPI Lite (ESP32-S3) — push a button, talk, get a spoken AI response.

This skill lets your OpenClaw agent manage the device: switch WiFi networks by voice, send spoken notifications, set timers and reminders, and check bridge status.

## Setup

Set these environment variables:

- `WALKIECLAW_BRIDGE_URL` — Your bridge URL (e.g. `http://localhost:8080` or `http://your-vps:8080`)
- `WALKIECLAW_API_KEY` — The API key configured in your bridge `.env` file

## Commands

### Switch WiFi Network

When the user asks to connect, switch, or change the WiFi on their WalkieClaw device, extract the SSID and password and run:

```bash
curl -s -X POST "${WALKIECLAW_BRIDGE_URL}/api/connect_wifi" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${WALKIECLAW_API_KEY}" \
  -d '{"ssid": "NETWORK_NAME", "password": "NETWORK_PASSWORD"}'
```

The command is queued and delivered to the ESP32 within 30 seconds (on its next health poll). Tell the user it's been queued and to wait a moment.

If no password is given, send an empty string.

### Send a Push Notification

When the user wants to send a spoken message or alert to the WalkieClaw device:

```bash
curl -s -X POST "${WALKIECLAW_BRIDGE_URL}/api/notify" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${WALKIECLAW_API_KEY}" \
  -d '{"text": "MESSAGE_TO_SPEAK"}'
```

The bridge generates TTS audio immediately and queues it. The ESP32 picks it up within 30 seconds, displays the text, and plays the audio through the speaker.

### Set a Timer or Reminder

When the user asks to set a timer, reminder, or alarm — like "remind me in 5 minutes to check the oven" or "set a 30 second timer" — extract the delay and message and run:

```bash
curl -s -X POST "${WALKIECLAW_BRIDGE_URL}/api/timer" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${WALKIECLAW_API_KEY}" \
  -d '{"text": "MESSAGE_TO_SPEAK", "delay_seconds": SECONDS}'
```

Convert natural language time to seconds: "5 minutes" = 300, "1 hour" = 3600, "30 seconds" = 30.

If the user doesn't specify a message, use the context: "set a 5 minute timer" → `"text": "Your 5 minute timer is up!"`.

The timer runs on the bridge. When it fires, the device speaks the message through its speaker.

To optionally target a specific device: add `"device": "DEVICE_IP"` to the JSON body.

### List Active Timers

```bash
curl -s "${WALKIECLAW_BRIDGE_URL}/api/timers" \
  -H "X-API-Key: ${WALKIECLAW_API_KEY}"
```

Returns JSON with active timers, their remaining seconds, and IDs.

### Cancel a Timer

```bash
curl -s -X DELETE "${WALKIECLAW_BRIDGE_URL}/api/timer/TIMER_ID" \
  -H "X-API-Key: ${WALKIECLAW_API_KEY}"
```

### Check Bridge Status

To check if the bridge is running and healthy:

```bash
curl -s "${WALKIECLAW_BRIDGE_URL}/health" \
  -H "X-API-Key: ${WALKIECLAW_API_KEY}"
```

Returns JSON with `status`, `listening`, `processing`, `speaking`, and `poll_status` fields.

## Important Notes

- Commands are delivered via HTTP polling, so there's up to a 30-second delay
- The bridge must be running and reachable for any commands to work
- After a WiFi switch, the device briefly disconnects before reconnecting to the new network
- Push notifications queue up if the device is busy — they'll play in order
- Timers are stored in memory — they are lost if the bridge restarts
- Timer delivery has up to a 30-second delay (device health poll interval)
- For hardware setup, flashing, and bridge installation, see the [WalkieClaw GitHub repo](https://github.com/slsah30/WalkieClaw)
