# Support for the SGP30 Air Quality Sensor
#
# Copyright (C) 2022 Nathan Knotts <nknotts@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from math import exp
import logging
import configparser
import csv
import time
import datetime
import threading
import queue
import os.path

from . import bus


class SGP30:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[1]
        self.reactor = self.printer.get_reactor()
        self.i2c = bus.MCU_I2C_from_config(
            config, default_addr=SGP30_DEFAULT_I2C_ADDR, default_speed=400000)
        self.report_time = config.getfloat(
            "report_time", default=1., minval=1.)
        self.sample_timer = self.reactor.register_timer(self.sample_sgp30)
        self.syslog_time = config.getfloat(
            "syslog_time", default=self.reactor.NEVER)
        self.syslog_last_updated = self.reactor.NOW
        self.csv_filename = config.get("csv_filename", default=None)
        self.csv_log_queue = queue.Queue()
        self.mcu = self.i2c.get_mcu()
        self.eCO2 = self.TVOC = None
        self.sgp30 = None
        cfgname = self.printer.get_start_args()['config_file']
        self.baseline_timer = None
        self.baseline_filename = os.path.join(
            os.path.dirname(cfgname), "sgp30_baseline.cfg")
        self.baseline_update_request = False
        self.baseline_eco2 = None
        self.baseline_tvoc = None
        self.baseline_last_updated = None
        self.baseline_initial_measurement_time = config.getfloat(
            "baseline_initial_measurement_time", default=12.0*3600.)
        self.baseline_measurement_time = config.getfloat(
            "baseline_measurement_time", default=3600.)
        self.baseline_stale_time = config.getfloat(
            "baseline_stale_time", default=7.0*24.0*3600.)
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
        self.printer.add_object("sgp30 " + self.name, self)
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect)

        if not self.temperature_sensor_name and not self.temperature_initial:
            raise config.error(
                "sgp30 {}: must specify either 'temperature_sensor'"
                " or 'temperature_initial'".format(self.name))

        if not self.humidity_sensor_name and not self.humidity_initial:
            raise config.error(
                "sgp30 {}: must specify either 'humidity_sensor'"
                " or 'humidity_initial'".format(self.name))

    def handle_connect(self):
        try:
            self.baseline_last_updated, self.baseline_eco2, self.baseline_tvoc \
                = read_baseline(self.baseline_filename, self.name)
        except Exception as err:
            logging.error(
                "sgp30 {}: Failed to read baseline - {}".format(self.name, err))

        self.sgp30 = Adafruit_SGP30(self.i2c, self.reactor)
        logging.info("sgp30 {}: Serial {}".format(
            self.name, self.sgp30.serial))

        if self.temperature_sensor_name:
            self.temperature_sensor = self.printer.lookup_object(
                self.temperature_sensor_name)

        if self.humidity_sensor_name:
            self.humidity_sensor = self.printer.lookup_object(
                self.humidity_sensor_name)

        # https://cdn-learn.adafruit.com/downloads/pdf/adafruit-sgp30-gas-tvoc-eco2-mox-sensor.pdf
        # See section: Baseline Set & Get
        now = datetime.datetime.utcnow()
        if not self.baseline_last_updated or \
                now - self.baseline_last_updated > datetime.timedelta(
                    seconds=self.baseline_stale_time):

            baseline_initial_delay = self.baseline_initial_measurement_time
            logging.warning(
                "sgp30 {}: no previous baseline, sampling for {:.2f} hours"
                .format(self.name,
                        self.baseline_initial_measurement_time/3600.0))
        else:
            baseline_initial_delay = self.baseline_measurement_time
            self.sgp30.set_iaq_baseline(self.baseline_eco2, self.baseline_tvoc)
            logging.info(
                "sgp30 {}: set initial baseline - eCO2: {}, TVOC: {}"
                .format(self.name, self.baseline_eco2, self.baseline_tvoc))

        if baseline_initial_delay > 0.0:
            self.baseline_timer = self.reactor.register_timer(
                self.update_baseline)
            self.reactor.update_timer(
                self.baseline_timer,
                self.reactor.monotonic()+baseline_initial_delay)
        else:
            logging.warning(
                "sgp30 {}: disabling baseline updates".format(self.name))

        if self.csv_filename:
            threading.Thread(target=csv_logger, args=(
                self.csv_filename, self.csv_log_queue)).start()

        self.reactor.update_timer(self.sample_timer, self.reactor.NOW)

    def update_baseline(self, eventtime):
        measured_time = self.reactor.monotonic()
        # ensure all i2c calls to the sgp30 occur within a single
        # function/coroutine - set a flag to update baseline
        # the next time sample_sgp30 is called
        self.baseline_update_request = True
        return measured_time + self.baseline_measurement_time

    def sample_sgp30(self, eventtime):
        measured_time = self.reactor.monotonic()

        # persist baseline to disk - useful on power cycles
        if self.baseline_update_request:
            try:
                self.baseline_eco2, self.baseline_tvoc = \
                    self.sgp30.get_iaq_baseline()
                self.baseline_last_updated = datetime.datetime.utcnow()
                self.baseline_update_request = False

                # put disk io in background thread
                # do not want a slow disk to cause print hiccups
                threading.Thread(target=write_baseline,
                                 args=(self.baseline_filename,
                                       self.name,
                                       self.baseline_last_updated,
                                       self.baseline_eco2,
                                       self.baseline_tvoc)).start()
                logging.info(
                    "sgp30 {}: read baseline - eCO2: {}, TVOC: {}"
                    .format(self.name, self.baseline_eco2, self.baseline_tvoc))
            except Exception as err:
                logging.exception("sgp30 {}: Error reading baseline - {}"
                                  .format(self.name, err))

        # update sgp30 temperature/humidity to aid with measurement compensation
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
            # require both temperature/humidity to update sgp30 compensation
            if tempC and humidity:
                self.sgp30.set_iaq_relative_humidity(tempC, humidity)

            # perform measurement
            self.eCO2, self.TVOC = self.sgp30.iaq_measure()
        except Exception as err:
            logging.exception("sgp30 {}: Error reading data - {}"
                              .format(self.name, err))
            self.eCO2 = self.TVOC = .0
            return self.reactor.NEVER

        # log measurement to syslog
        now = self.reactor.monotonic()
        if self.syslog_time > 0:
            if now - self.syslog_last_updated > self.syslog_time:
                logging.info("sgp30 {}: measured - eCO2: {}, TVOC: {}"
                             .format(self.name, self.eCO2, self.TVOC))
                self.syslog_last_updated = now

        # log measurement to csv
        if self.csv_filename:
            self.csv_log_queue.put_nowait({
                "monotonic": now,
                "eco2": self.eCO2,
                "tvoc": self.TVOC,
                "temperature": tempC,
                "humidity": humidity
            })

        # schedule next loop
        return measured_time + self.report_time

    def get_status(self, eventtime):
        return {
            'eco2': self.eCO2,
            'tvoc': self.TVOC,
        }


def read_baseline(filename, section):
    cfg = configparser.ConfigParser()
    cfg.readfp(open(filename))
    last_updated = datetime.datetime.strptime(
        cfg.get(section, "last_updated"), "%Y-%m-%dT%H:%M:%S.%fZ")
    logging.info(last_updated)
    eco2 = cfg.getint(section, "eco2")
    tvoc = cfg.getint(section, "tvoc")
    return last_updated, eco2, tvoc


def write_baseline(filename, section, last_updated, eco2, tvoc):
    try:
        cfg = configparser.ConfigParser()
        try:
            cfg.readfp(open(filename))
        except Exception as err:
            logging.warning("Failed to read sgp30 config: {}".format(err))

        if not cfg.has_section(section):
            cfg.add_section(section)

        cfg.set(section, "last_updated", last_updated.isoformat() + "Z")
        cfg.set(section, "eco2", eco2)
        cfg.set(section, "tvoc", tvoc)

        with open(filename, 'w') as f:
            cfg.write(f)
    except Exception as err:
        logging.error("Failed to write: {}".format(err))


def csv_logger(filename, data_queue):
    logging.info("LETS LOG: {}".format(filename))

    with open(filename, 'a') as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(['unix_time', 'monotonic_time',
                           'ECO2', 'TVOC', "temperature", "humidity"])

        while True:
            item = data_queue.get()
            csvwriter.writerow(
                [time.time(), item['monotonic'], item['eco2'], item['tvoc'],
                 item['temperature'], item['humidity']])
            csvfile.flush()
            data_queue.task_done()


# The Adafruit_SGP30 class is heavily inspired/taken from
# https://github.com/adafruit/Adafruit_CircuitPython_SGP30/blob/2af0803268efeb41ca6cf68ee6058847b3a16c40/adafruit_sgp30.py
SGP30_DEFAULT_I2C_ADDR = 0x58
_SGP30_FEATURESETS = (0x0020, 0x0022)

_SGP30_CRC8_POLYNOMIAL = 0x31
_SGP30_CRC8_INIT = 0xFF
_SGP30_WORD_LEN = 2


class Adafruit_SGP30:
    def __init__(self, i2c, reactor):
        """Initialize the sensor, get serial, and verify a proper SGP30"""
        self._i2c = i2c
        self._reactor = reactor

        # get unique serial, its 48 bits so we store in an array
        self.serial = self._i2c_read_words_from_cmd([0x36, 0x82], 0.01, 3)
        # get featureset
        featureset = self._i2c_read_words_from_cmd([0x20, 0x2F], 0.01, 1)
        if featureset[0] not in _SGP30_FEATURESETS:
            raise RuntimeError("SGP30 Not detected")
        self.iaq_init()

    @property
    # pylint: disable=invalid-name
    def TVOC(self):
        """Total Volatile Organic Compound in parts per billion."""
        return self.iaq_measure()[1]

    @property
    # pylint: disable=invalid-name
    def baseline_TVOC(self):
        """Total Volatile Organic Compound baseline value"""
        return self.get_iaq_baseline()[1]

    @property
    # pylint: disable=invalid-name
    def eCO2(self):
        """Carbon Dioxide Equivalent in parts per million"""
        return self.iaq_measure()[0]

    @property
    # pylint: disable=invalid-name
    def baseline_eCO2(self):
        """Carbon Dioxide Equivalent baseline value"""
        return self.get_iaq_baseline()[0]

    @property
    # pylint: disable=invalid-name
    def Ethanol(self):
        """Ethanol Raw Signal in ticks"""
        return self.raw_measure()[1]

    @property
    # pylint: disable=invalid-name
    def H2(self):
        """H2 Raw Signal in ticks"""
        return self.raw_measure()[0]

    def iaq_init(self):
        """Initialize the IAQ algorithm"""
        # name, command, signals, delay
        self._run_profile(["iaq_init", [0x20, 0x03], 0, 0.01])

    def iaq_measure(self):
        """Measure the eCO2 and TVOC"""
        # name, command, signals, delay
        return self._run_profile(["iaq_measure", [0x20, 0x08], 2, 0.05])

    def raw_measure(self):
        """Measure H2 and Ethanol (Raw Signals)"""
        # name, command, signals, delay
        return self._run_profile(["raw_measure", [0x20, 0x50], 2, 0.025])

    def get_iaq_baseline(self):
        """Retreive the IAQ algorithm baseline for eCO2 and TVOC"""
        # name, command, signals, delay
        return self._run_profile(["iaq_get_baseline", [0x20, 0x15], 2, 0.01])

    def set_iaq_baseline(self, eCO2, TVOC):  # pylint: disable=invalid-name
        """Set previously recorded IAQ algorithm baseline for eCO2 and TVOC"""
        if eCO2 == 0 and TVOC == 0:
            raise RuntimeError("Invalid baseline")
        buffer = []
        for value in [TVOC, eCO2]:
            arr = [value >> 8, value & 0xFF]
            arr.append(self._generate_crc(arr))
            buffer += arr
        self._run_profile(["iaq_set_baseline", [0x20, 0x1E] + buffer, 0, 0.01])

    def set_iaq_humidity(self, gramsPM3):  # pylint: disable=invalid-name
        """Set the humidity in g/m3 for eCO2 and TVOC compensation algorithm"""
        tmp = int(gramsPM3 * 256)
        buffer = []
        for value in [tmp]:
            arr = [value >> 8, value & 0xFF]
            arr.append(self._generate_crc(arr))
            buffer += arr
        self._run_profile(["iaq_set_humidity", [0x20, 0x61] + buffer, 0, 0.01])

    def set_iaq_relative_humidity(self, celsius, relative_humidity):
        """
        Set the humidity in g/m3 for eCo2 and TVOC compensation algorithm.
        The absolute humidity is calculated from the temperature (Celsius)
        and relative humidity (as a percentage).
        """
        numerator = ((relative_humidity / 100) * 6.112) * exp(
            (17.62 * celsius) / (243.12 + celsius)
        )
        denominator = 273.15 + celsius

        humidity_grams_pm3 = 216.7 * (numerator / denominator)
        self.set_iaq_humidity(humidity_grams_pm3)

    # Low level command functions

    def _run_profile(self, profile):
        """Run an SGP 'profile' which is a named command set"""
        # pylint: disable=unused-variable
        name, command, signals, delay = profile
        # pylint: enable=unused-variable

        # print("\trunning profile: %s, command %s, %d, delay %0.02f" %
        #   (name, ["0x%02x" % i for i in command], signals, delay))
        return self._i2c_read_words_from_cmd(command, delay, signals)

    def _i2c_read_words_from_cmd(self, command, delay, reply_size):
        """Run an SGP command query, get a reply and CRC results if necessary"""
        self._i2c.i2c_write(command)
        t0 = self._reactor.monotonic()
        self._reactor.pause(self._reactor.monotonic() + delay)
        dt = self._reactor.monotonic() - t0
        if not reply_size:
            return None
        params = self._i2c.i2c_read([], reply_size * (_SGP30_WORD_LEN + 1))
        crc_result = bytearray(params['response'])

        result = []
        for i in range(reply_size):
            word = [crc_result[3 * i], crc_result[3 * i + 1]]
            crc = crc_result[3 * i + 2]
            if self._generate_crc(word) != crc:
                raise RuntimeError("CRC Error")
            result.append(word[0] << 8 | word[1])
        return result

    # pylint: disable=no-self-use
    def _generate_crc(self, data):
        """8-bit CRC algorithm for checking data"""
        crc = _SGP30_CRC8_INIT
        # calculates 8-Bit checksum with given polynomial
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ _SGP30_CRC8_POLYNOMIAL
                else:
                    crc <<= 1
        return crc & 0xFF


def load_config_prefix(config):
    return SGP30(config)
