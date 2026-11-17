"""A stand-in for the ESP32 bridge's control link (bridge/main/link.c; the PC side is
bridge.Link): signed UDP on 127.0.0.1, enough of it to test the HID services' bridge path
without the hardware. It records what it was sent; it has no Bluetooth or iPhone.

    with FakeBridge(key) as fake:
        ... Link({"ip": "127.0.0.1", "key": key.hex()}) ...
        fake.reports  # [(report_id, payload bytes)], fake.taps  # [(x, y, ms)]
"""
import hashlib
import hmac
import json
import os
import socket
import struct
import threading

from phoneuse.bridge import PORT

HELLO, REPORT, STATUS, CONNECT, TAP, WANT, DROP = 0x01, 0x02, 0x03, 0x04, 0x0C, 0x0E, 0x0F


class FakeBridge:
    def __init__(self, key, phone="Test iPhone", connected=True, fw="0.4.3"):
        self.key = key
        self.phone, self.connected, self.fw = phone, connected, fw
        self.session = struct.unpack("<I", os.urandom(4))[0] | 1
        self.reports, self.taps, self.wants, self.commands = [], [], [], []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", PORT))
        self.sock.settimeout(0.2)
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.running = False
        self.thread.join(1)
        self.sock.close()

    def _sign(self, type_, session, seq, body):
        head = b"PU\x01" + bytes([type_]) + struct.pack("<II", session, seq)
        return head + body + hmac.new(self.key, head + body, hashlib.sha256).digest()[:8]

    def _serve(self):
        while self.running:
            try:
                b, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(b) < 20 or not hmac.compare_digest(b[-8:], hmac.new(self.key, b[:-8], hashlib.sha256).digest()[:8]):
                continue  # not signed with the key: the real bridge ignores it too
            type_, seq, body = b[3], struct.unpack_from("<I", b, 8)[0], b[12:-8]
            self.commands.append(type_)
            answer = self._answer(type_, body)
            if answer is not None:
                session = self.session if type_ != HELLO else self.session
                self.sock.sendto(self._sign(type_ | 0x80, session, 0 if type_ == HELLO else seq, answer), addr)

    def _answer(self, type_, body):
        if type_ == HELLO:
            return body[:8]  # the nonce back; the session is in the header
        if type_ == REPORT:
            mode, report_id, payload = body[0], body[1], bytes(body[2:])
            self.reports.append((report_id, payload))
            if mode == 3:  # a hover kept as the newest: no answer
                return None
            return bytes([0 if self.connected else 2, 0])  # sent / no phone, nothing waiting
        if type_ == STATUS:
            return json.dumps({"fw": self.fw, "connected": self.connected, "phone": self.phone,
                               "online": True, "ssid": "Test Wi-Fi", "hosts": 1}).encode()
        if type_ == TAP:
            x, y, ms = struct.unpack("<HHH", body[:6])
            self.taps.append((x, y, ms))
            return bytes([0])
        if type_ == WANT:
            self.wants.append(bool(body[:1] == b"\x01"))
            return bytes([1 if self.connected else 0])
        return b""  # CONNECT, DROP and the rest: answered, nothing to say
