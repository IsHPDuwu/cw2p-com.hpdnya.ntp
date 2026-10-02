import math

KEY = "schedule.time_offset"


def rounded_offset(theta, compensation):
    value = theta + compensation
    if not math.isfinite(value):
        raise ValueError("偏移不是有限数值")
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


class OffsetLease:
    """Only called on the Qt main thread; guard synchronous configChanged reentry."""
    def __init__(self, globalconfig, config, save):
        self.api, self.config, self.save = globalconfig, config, save
        self.held = False
        self.busy = False
        self.expected = None

    def value(self):
        return self.api.configs.schedule.time_offset

    def parent_locked(self):
        return self.api.is_locked("schedule")

    def acquire(self):
        if self.held:
            self.check()
            return
        if self.parent_locked() or self.api.is_locked(KEY):
            raise RuntimeError("偏移已被外部锁定，未接管")
        self.busy = True
        try:
            c = self.config
            current = self.value()
            if not (c.restore_pending and c.last_written == current):
                c.original = current
                c.last_written = None
            c.restore_pending = True
            self.save()
            self.expected = current
            self.api.lock(KEY)
            self.held = True
        finally:
            self.busy = False

    def check(self):
        if self.held and not self.busy:
            if self.parent_locked() or not self.api.is_locked(KEY) or self.value() != self.expected:
                raise RuntimeError("检测到外部修改或锁变化，请关闭后重新启用")

    def write(self, value):
        if self.busy:
            return
        self.check()
        if not self.held:
            raise RuntimeError("尚未接管偏移")
        if self.value() == value:
            return
        self.busy = True
        previous = self.config.last_written
        try:
            # Persist recovery journal BEFORE changing the host value.
            self.config.last_written = value
            self.save()
            self.api.unlock(KEY)
            try:
                self.api.configs.set(KEY, value)
                if self.value() != value:
                    raise RuntimeError("偏移写入未通过读回验证")
                self.expected = value
            finally:
                self.api.lock(KEY)
        except Exception:
            if self.value() != value:
                self.config.last_written = previous
                self.save()
            raise
        finally:
            self.busy = False

    def release(self):
        if not self.held or self.busy:
            return False
        self.busy = True
        restored = False
        try:
            self.api.unlock(KEY)
            self.held = False
            c = self.config
            if not self.parent_locked() and c.last_written is not None and self.value() == c.last_written:
                self.api.configs.set(KEY, c.original)
                restored = self.value() == c.original
            # Keep journal only if an externally blocked restore is still applicable.
            c.restore_pending = bool(not restored and c.last_written is not None and self.value() == c.last_written)
            self.save()
            return restored
        finally:
            self.busy = False