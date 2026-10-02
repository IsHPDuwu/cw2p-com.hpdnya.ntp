import time
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal, Slot
from ClassWidgets.SDK import CW2Plugin
from ntp_config import NtpConfig
from ntp_client import query
from offset_policy import OffsetLease, rounded_offset

# Keep unparented workers alive across unload, including when DNS is slow.
_workers = set()


def retire(thread):
    _workers.discard(thread)
    thread.deleteLater()


class QueryThread(QThread):
    result = Signal(int, object, str)

    def __init__(self, generation, settings):
        super().__init__()
        self.generation, self.settings = generation, settings

    def run(self):
        try:
            sample = query(self.settings, self.isInterruptionRequested)
            self.result.emit(self.generation, sample, "")
        except Exception as error:
            self.result.emit(self.generation, None, str(error))


class Plugin(CW2Plugin):
    changed = Signal()

    def __init__(self, api):
        super().__init__(api)
        self.config = NtpConfig()
        self.lease = OffsetLease(api.globalconfig, self.config, api.config.save)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.sync)
        self.watch = QTimer(self)
        self.watch.setInterval(5000)
        self.watch.timeout.connect(self.tick)
        self.active = False
        self.generation = 0
        self.worker = None
        self.sample = None
        self.failures = 0
        self.status = "未启用"
        self.success_time = ""
        self.clock = (time.time(), time.monotonic())
        self.page = str(Path(__file__).parent / "qml" / "Settings.qml")

    def on_load(self):
        super().on_load()
        self.api.config.register_plugin_model(self.pid, self.config)
        self.api.ui.register_settings_page(qml_path=self.page, title="NTP 课表校时", icon="ic_fluent_clock_20_regular")
        self.api.globalconfig.configs.configChanged.connect(self.tick)
        self.watch.start()
        if self.config.enabled:
            self.enable(True)

    def invalidate(self):
        self.generation += 1
        self.timer.stop()
        if self.worker:
            self.worker.requestInterruption()

    def on_unload(self):
        self.active = False
        self.invalidate()
        self.watch.stop()
        self.api.globalconfig.configs.configChanged.disconnect(self.tick)
        self.lease.release()
        self.api.ui.unregister_settings_page(self.page)
        from src.core.plugin.bridge import PluginBackendBridge
        if PluginBackendBridge._registry.get(self.pid) is self:
            PluginBackendBridge._registry.pop(self.pid, None)
        if self.worker:
            self.worker.result.disconnect(self.finished_query)
            self.worker.finished.disconnect(self.finished_thread)
            self.worker = None

    @Slot(bool)
    def enable(self, enabled):
        if self.lease.busy:
            return
        self.active = False
        self.invalidate()
        try:
            if enabled:
                self.lease.acquire()
                self.active = True
                self.failures = 0
                self.status = "等待同步"
                self.timer.start(2000)
            else:
                restored = self.lease.release()
                self.status = "已关闭（已恢复原偏移）" if restored else "已关闭（保留当前偏移）"
            self.config.enabled = enabled
            self.api.config.save()
        except Exception as error:
            self.status = str(error)
        self.changed.emit()

    @Slot()
    def tick(self):
        if self.lease.busy:
            return
        try:
            self.lease.check()
        except Exception as error:
            self.active = False
            self.invalidate()
            self.status = str(error)
            self.changed.emit()
        now = (time.time(), time.monotonic())
        wall_delta, mono_delta = now[0] - self.clock[0], now[1] - self.clock[1]
        self.clock = now
        if self.active and (abs(wall_delta - mono_delta) > 1 or mono_delta > 20):
            self.sample = None
            self.invalidate()
            self.timer.start(100)

    @Slot()
    def sync(self):
        if not self.active:
            self.status = "请先启用偏移接管"
            self.changed.emit()
            return
        if self.worker is not None:
            self.timer.start(1000)
            return
        self.timer.stop()
        self.generation += 1
        thread = QueryThread(self.generation, self.config.model_copy(deep=True))
        self.worker = thread
        _workers.add(thread)
        thread.result.connect(self.finished_query)
        thread.finished.connect(self.finished_thread)
        thread.finished.connect(lambda t=thread: retire(t))
        self.status = "正在后台查询…"
        self.changed.emit()
        thread.start()

    @Slot()
    def finished_thread(self):
        if self.sender() is self.worker:
            self.worker = None

    @Slot(int, object, str)
    def finished_query(self, generation, sample, error):
        if not self.active or generation != self.generation:
            return
        try:
            if error:
                raise RuntimeError(error)
            if time.monotonic() - sample.measured_at > self.config.interval_minutes * 60:
                raise RuntimeError("查询结果已过期")
            self.lease.write(rounded_offset(sample.offset, self.config.compensation))
            self.sample = sample
            self.failures = 0
            self.success_time = time.strftime("%Y-%m-%d %H:%M:%S")
            self.status = "同步成功"
            delay = self.config.interval_minutes * 60
        except Exception as exception:
            self.failures += 1
            self.status = f"同步失败，保留当前偏移：{exception}"
            delay = (60, 300, 900)[self.failures - 1] if self.failures <= 3 else self.config.interval_minutes * 60
        self.timer.start(delay * 1000)
        self.changed.emit()

    @Slot(result="QVariantMap")
    def snapshot(self):
        return {**self.config.model_dump(), "active": self.active, "status": self.status,
                "success": self.success_time, "source": self.sample.server if self.sample else "—",
                "offset_ms": self.sample.offset * 1000 if self.sample else 0,
                "delay_ms": self.sample.delay * 1000 if self.sample else 0,
                "final": self.lease.value()}

    @Slot(str, str, result=bool)
    def updateSetting(self, key, text):
        converters = {"servers": lambda x: x.split(","), "interval_minutes": int,
                      "timeout": float, "max_delay": float, "max_offset": float, "compensation": float}
        try:
            if key not in converters:
                raise ValueError("未知设置项")
            values = self.config.model_dump()
            values[key] = converters[key](text)
            validated = NtpConfig.model_validate(values)
            setattr(self.config, key, getattr(validated, key))
            self.api.config.save()
            self.invalidate()
            if self.active:
                if key == "compensation" and self.sample and time.monotonic() - self.sample.measured_at < self.config.interval_minutes * 60:
                    self.lease.write(rounded_offset(self.sample.offset, self.config.compensation))
                    self.timer.start(self.config.interval_minutes * 60000)
                else:
                    self.sample = None
                    self.timer.start(100)
            self.changed.emit()
            return True
        except Exception as error:
            self.status = str(error)
            self.changed.emit()
            return False