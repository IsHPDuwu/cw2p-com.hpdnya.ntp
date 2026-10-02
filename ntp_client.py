"""ntplib owns packet encoding/decoding and offset/delay calculation."""
import math
import socket
import time
from dataclasses import dataclass
from types import FunctionType, SimpleNamespace

import ntplib

ERA = 2 ** 32


@dataclass(frozen=True)
class Measurement:
    server: str
    offset: float
    delay: float
    measured_at: float


class CheckedSocket:
    """Transport guard: exact endpoint/origin association and a total receive deadline."""
    def __init__(self, *args):
        self.socket = socket.socket(*args)
        self.timeout = 3

    def settimeout(self, value):
        self.timeout = value
        self.socket.settimeout(value)

    def sendto(self, packet, endpoint):
        self.origin, self.endpoint = packet[40:48], endpoint
        self.deadline = time.monotonic() + self.timeout
        return self.socket.sendto(packet, endpoint)

    def recvfrom(self, size):
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout()
            self.socket.settimeout(remaining)
            packet, endpoint = self.socket.recvfrom(size)
            if endpoint == self.endpoint and len(packet) >= 48 and packet[24:32] == self.origin:
                return packet, endpoint

    def close(self):
        self.socket.close()


def request(host, timeout, port=123):
    # Isolate ntplib's socket dependency per invocation: no process-wide monkeypatch.
    namespace = dict(ntplib.NTPClient.request.__globals__)
    namespace["socket"] = SimpleNamespace(getaddrinfo=socket.getaddrinfo, socket=CheckedSocket,
                                         SOCK_DGRAM=socket.SOCK_DGRAM, timeout=socket.timeout)
    guarded = FunctionType(ntplib.NTPClient.request.__code__, namespace)
    return guarded(ntplib.NTPClient(), host, version=4, port=port, timeout=timeout)


def validate(stats, start_wall, end_wall, max_delay, max_offset):
    if stats.leap == 3 or stats.mode != 4 or stats.version not in (3, 4) or not 1 <= stats.stratum <= 15:
        raise ValueError("NTP 未同步、KoD 或响应模式无效")
    if not all(math.isfinite(x) and x > 0 for x in (stats.orig_timestamp, stats.recv_timestamp, stats.tx_timestamp, stats.dest_timestamp)):
        raise ValueError("NTP 时间戳无效")
    # Unfold each 32-bit wire timestamp into the era nearest our destination.
    for name in ("orig_timestamp", "recv_timestamp", "tx_timestamp"):
        value = getattr(stats, name)
        setattr(stats, name, value + round((stats.dest_timestamp - value) / ERA) * ERA)
    if not start_wall - 0.001 <= stats.orig_time <= end_wall + 0.001:
        raise ValueError("请求时间戳不在本次查询范围内")
    if stats.tx_timestamp < stats.recv_timestamp:
        raise ValueError("服务器时间戳倒序")
    if not math.isfinite(stats.delay) or not 0 <= stats.delay <= max_delay:
        raise ValueError("往返延迟超过安全限制")
    if not math.isfinite(stats.offset) or abs(stats.offset) > max_offset:
        raise ValueError("时差超过安全限制")
    return stats.offset, stats.delay


def query(settings, cancelled, requester=request, wall=time.time, monotonic=time.monotonic):
    errors = []
    for server in settings.servers:
        samples = []
        for _ in range(3):
            if cancelled():
                raise RuntimeError("查询已取消")
            w0, m0 = wall(), monotonic()
            try:
                stats = requester(server, settings.timeout)
                w1, m1 = wall(), monotonic()
                if abs((w1 - w0) - (m1 - m0)) > 0.25:
                    raise ValueError("查询期间系统时钟跳变")
                theta, delay = validate(stats, w0, w1, settings.max_delay, settings.max_offset)
                samples.append(Measurement(server, theta, delay, m1))
            except Exception as error:
                errors.append(f"{server}: {error}")
        if cancelled():
            raise RuntimeError("查询已取消")
        if samples:
            return min(samples, key=lambda sample: sample.delay)
    raise RuntimeError("；".join(errors))