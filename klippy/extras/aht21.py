# AHT21 i2c based temperature sensors support
#
# Copyright (C) 2022 Nathan Knotts <nknotts@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
from . import bus


class AHT21:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.reactor = self.printer.get_reactor()
        self.i2c = bus.MCU_I2C_from_config(
            config, default_addr=AHTX0_I2CADDR_DEFAULT, default_speed=400000)
        self.report_time = config.getfloat(
            "report_time", default=1.0, minval=1.0)
        self.sample_timer = self.reactor.register_timer(self._sample_aht21)
        self.aht21 = None

        self.temp = self.min_temp = self.max_temp = self.humidity = 0.
        self.printer.add_object("aht21 " + self.name, self)
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)

    def handle_connect(self):
        self.aht21 = AHTx0(self.i2c, self.reactor)
        self.reactor.update_timer(self.sample_timer, self.reactor.NOW)

    def setup_minmax(self, min_temp, max_temp):
        self.min_temp = min_temp
        self.max_temp = max_temp

    def setup_callback(self, cb):
        self._callback = cb

    def get_report_time_delta(self):
        return self.report_time

    def _sample_aht21(self, eventtime):
        measured_time = self.reactor.monotonic()

        try:
            self.temp, self.humidity = self.aht21.read_data()
        except Exception as err:
            logging.exception("aht21 {}: Error reading data - {}"
                              .format(self.name, err))
            self.temp = self.humidity = .0
            return self.reactor.NEVER

        if self.temp < self.min_temp or self.temp > self.max_temp:
            self.printer.invoke_shutdown(
                "aht21 %s: temperature %0.1f outside range of %0.1f:%.01f"
                % (self.name, self.temp, self.min_temp, self.max_temp))

        print_time = self.i2c.get_mcu().estimated_print_time(measured_time)
        self._callback(print_time, self.temp)
        return measured_time + self.report_time

    def get_status(self, eventtime):
        return {
            'temperature': round(self.temp, 2),
            'humidity': self.humidity,
        }


# heavily copied/modified from
# https://github.com/adafruit/Adafruit_CircuitPython_AHTx0/blob/ba27d29721083f77be379bb13886a60ed203a472/adafruit_ahtx0.py

AHTX0_I2CADDR_DEFAULT = 0x38  # Default I2C address
AHTX0_CMD_CALIBRATE = 0xE1  # Calibration command
AHTX0_CMD_TRIGGER = 0xAC  # Trigger reading command
AHTX0_CMD_SOFTRESET = 0xBA  # Soft reset command
AHTX0_STATUS_BUSY = 0x80  # Status bit for busy
AHTX0_STATUS_CALIBRATED = 0x08  # Status bit for calibrated


class AHTx0:
    def __init__(self, i2c, reactor):
        self._reactor = reactor
        self._i2c = i2c

        # 20ms delay to wake up
        self._reactor.pause(self._reactor.monotonic() + 0.02)
        self.reset()
        if not self.calibrate():
            raise RuntimeError("Could not calibrate")

    def reset(self):
        """Perform a soft-reset of the AHT"""
        self._i2c.i2c_write([AHTX0_CMD_SOFTRESET])
        # 20ms delay to wake up
        self._reactor.pause(self._reactor.monotonic() + 0.02)

    def calibrate(self):
        """Ask the sensor to self-calibrate.
           Returns True on success, False otherwise"""
        self._i2c.i2c_write([AHTX0_CMD_CALIBRATE, 0x08, 0x00])
        while self.status & AHTX0_STATUS_BUSY:
            self._reactor.pause(self._reactor.monotonic() + 0.01)
        if not self.status & AHTX0_STATUS_CALIBRATED:
            return False
        return True

    @property
    def status(self):
        """The status byte initially returned from the sensor,
           see datasheet for details"""
        params = self._i2c.i2c_read([], 1)
        buf = bytearray(params['response'])
        return buf[0]

    def read_data(self):
        """Internal function for triggering the AHT to read temp/humidity"""
        self._i2c.i2c_write([AHTX0_CMD_TRIGGER, 0x33, 0x00])

        while self.status & AHTX0_STATUS_BUSY:
            self._reactor.pause(self._reactor.monotonic() + 0.08)

        params = self._i2c.i2c_read([], 6)
        buf = bytearray(params['response'])

        humidity = ((buf[1] << 12) | (buf[2] << 4) | (buf[3] >> 4))
        humidity = (humidity * 100) / 0x100000
        temp = ((buf[3] & 0xF) << 16) | (buf[4] << 8) | buf[5]
        temp = ((temp * 200.0) / 0x100000) - 50

        return temp, humidity


def load_config(config):
    # Register sensor
    pheaters = config.get_printer().lookup_object("heaters")
    pheaters.add_sensor_factory("AHT21", AHT21)
