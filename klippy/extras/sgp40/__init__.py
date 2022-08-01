# Support for the SGP40 Air Quality Sensor
#
# Copyright (C) 2022 Nathan Knotts <nknotts@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import csv
import time
import threading
import queue
import adafruit_sgp40

from .. import bus


class SGP40:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[1]
        self.reactor = self.printer.get_reactor()
        self.i2c = bus.MCU_I2C_from_config(
            config, default_addr=0x59, default_speed=400000)
        self.report_time = config.getfloat(
            "report_time", default=1., minval=1.)
        self.sample_timer = self.reactor.register_timer(self.sample_sgp40)
        self.syslog_time = config.getfloat(
            "syslog_time", default=self.reactor.NEVER)
        self.syslog_last_updated = self.reactor.NOW
        self.csv_filename = config.get("csv_filename", default=None)
        self.csv_log_queue = queue.Queue()
        self.air_quality = None
        self.sgp40 = None
        self.temperature_sensor = None
        self.temperature_sensor_name = config.get(
            "temperature_sensor", default=None)
        self.temperature_key = config.get(
            "temperature_key", default="temperature")
        self.temperature_initial = config.get(
            "temperature_initial", default=None)  # units deg C
        self.temperature_last = None
        self.humidity_sensor = None
        self.humidity_sensor_name = config.get(
            "humidity_sensor", default=self.temperature_sensor_name)
        self.humidity_key = config.get("humidity_key", default="humidity")
        self.humidity_initial = config.getfloat(
            "humidity_initial", default=None)  # units RH %
        self.humidity_last = None
        self.printer.add_object("sgp40 " + self.name, self)
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect)

        if not self.temperature_sensor_name and not self.temperature_initial:
            raise config.error(
                "sgp40 {}: must specify either 'temperature_sensor'"
                " or 'temperature_initial'".format(self.name))

        if not self.humidity_sensor_name and not self.humidity_initial:
            raise config.error(
                "sgp40 {}: must specify either 'humidity_sensor'"
                " or 'humidity_initial'".format(self.name))

    def handle_connect(self):
        self.sgp40 = adafruit_sgp40.Adafruit_SGP40(self.i2c, self.reactor)

        if self.temperature_sensor_name:
            self.temperature_sensor = self.printer.lookup_object(
                self.temperature_sensor_name)

        if self.humidity_sensor_name:
            self.humidity_sensor = self.printer.lookup_object(
                self.humidity_sensor_name)

        if self.csv_filename:
            threading.Thread(target=csv_logger, args=(
                self.csv_filename, self.csv_log_queue)).start()

        self.reactor.update_timer(self.sample_timer, self.reactor.NOW)

    def sample_sgp40(self, eventtime):
        measured_time = self.reactor.monotonic()

        # update sgp40 temp/humidity to aid with measurement compensation
        if self.temperature_sensor:
            temperature_status = self.temperature_sensor.get_status(eventtime)
            tempC = temperature_status[self.temperature_key]
        else:
            tempC = self.temperature_initial
        if self.humidity_sensor:
            humidity_status = self.humidity_sensor.get_status(eventtime)
            humidity = humidity_status[self.humidity_key]
        else:
            humidity = self.humidity_initial

        try:
            if tempC and humidity:
                self.air_quality = self.sgp40.measure_index(tempC, humidity)
            else:
                # default temp/humidity
                self.air_quality = self.sgp40.measure_index()

        except Exception as err:
            logging.exception(
                "sgp40 {}: Error reading data - {}".format(self.name, err))
            self.air_quality = .0
            return self.reactor.NEVER

        # log measurement to syslog
        now = self.reactor.monotonic()
        if self.syslog_time > 0:
            if now - self.syslog_last_updated > self.syslog_time:
                logging.info("sgp40 {}: measured - Air Quality: {}"
                             .format(self.name, self.air_quality))
                self.syslog_last_updated = now

        # log measurement to csv
        if self.csv_filename:
            self.csv_log_queue.put_nowait({
                "monotonic": now,
                "air_quality": self.air_quality,
                "temperature": tempC,
                "humidity": humidity
            })

        # schedule next loop
        return measured_time + self.report_time

    def get_status(self, eventtime):
        return {
            'air_quality': self.air_quality,
        }


def csv_logger(filename, data_queue):
    with open(filename, 'a') as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(['unix_time', 'monotonic_time', 'AirQuality',
                            "TemperatureC", "HumidityRH"])

        while True:
            item = data_queue.get()
            csvwriter.writerow(
                [time.time(), item['monotonic'], item['air_quality'],
                 item['temperature'], item['humidity']])
            csvfile.flush()
            data_queue.task_done()


def load_config_prefix(config):
    return SGP40(config)
