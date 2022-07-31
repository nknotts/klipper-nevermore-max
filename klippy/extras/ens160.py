# Support for the ENS160 Air Quality Sensor
#
# Copyright (C) 2022 Nathan Knotts <nknotts@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import csv
import time
import struct
import threading
import queue

from . import bus


class ENS160:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[1]
        self.reactor = self.printer.get_reactor()
        self.i2c = bus.MCU_I2C_from_config(
            config, default_addr=ENS160_I2CADDR_DEFAULT, default_speed=400000)
        self.report_time = config.getfloat(
            "report_time", default=1., minval=1.)
        self.sample_timer = self.reactor.register_timer(self.sample_ens160)
        self.syslog_time = config.getfloat(
            "syslog_time", default=self.reactor.NEVER)
        self.syslog_last_updated = self.reactor.NOW
        self.csv_filename = config.get("csv_filename", default=None)
        self.csv_log_queue = queue.Queue()
        self.eCO2 = self.TVOC = None
        self.ens160 = None
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
        self.printer.add_object("ens160 " + self.name, self)
        self.printer.register_event_handler(
            "klippy:connect", self.handle_connect)

        if not self.temperature_sensor_name and not self.temperature_initial:
            raise config.error(
                "ens160 {}: must specify either 'temperature_sensor'"
                " or 'temperature_initial'".format(self.name))

        if not self.humidity_sensor_name and not self.humidity_initial:
            raise config.error(
                "ens160 {}: must specify either 'humidity_sensor'"
                " or 'humidity_initial'".format(self.name))

    def handle_connect(self):
        self.ens160 = DFRobot_ENS160(self.i2c, self.reactor)

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

    def sample_ens160(self, eventtime):
        measured_time = self.reactor.monotonic()

        # update ens160 temp/humidity to aid with measurement compensation
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
            # require both temperature/humidity to update ens160 compensation
            if tempC and humidity:
                self.ens160.set_temp_and_hum(tempC, humidity)

            # perform measurement
            status, aqi, self.TVOC, self.eCO2 = self.ens160.air_quality()
        except Exception as err:
            logging.exception(
                "ens160 {}: Error reading data - {}".format(self.name, err))
            self.eCO2 = self.TVOC = .0
            return self.reactor.NEVER

        # log measurement to syslog
        now = self.reactor.monotonic()
        if self.syslog_time > 0:
            if now - self.syslog_last_updated > self.syslog_time:
                logging.info("ens160 {}: measured - AQI: {}, eCO2: {}, TVOC: {}"
                             .format(self.name, aqi, self.eCO2, self.TVOC))
                self.syslog_last_updated = now

        # log measurement to csv
        if self.csv_filename:
            self.csv_log_queue.put_nowait({
                "monotonic": now,
                "status": status,
                "aqi": aqi,
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


def csv_logger(filename, data_queue):
    with open(filename, 'a') as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(['unix_time', 'monotonic_time', 'Status', 'AQI',
                           'ECO2', 'TVOC', "TemperatureC", "HumidityRH"])

        while True:
            item = data_queue.get()
            csvwriter.writerow(
                [time.time(), item['monotonic'], item['status'], item['aqi'],
                 item['eco2'], item['tvoc'], item['temperature'],
                 item['humidity']])
            csvfile.flush()
            data_queue.task_done()


# The DFRobot_ENS160 class is heavily inspired/taken from

'''!
  @file  DFRobot_ENS160.py
  @brief  Define infrastructure of DFRobot_ENS160 class
  @details  This is a Digital Metal-Oxide Multi-Gas Sensor. It can be controlled by I2C and SPI port.
  @n        Detection of a variety of gases, such as volatile organic compounds (VOCs), including ethanol,
  @n        toluene, as well as hydrogen and nitrogen dioxide, has superior selectivity and accuracy.
  @copyright  Copyright (c) 2010 DFRobot Co.Ltd (http://www.dfrobot.com)
  @license  The MIT License (MIT)
  @author  [qsjhyy](yihuan.huang@dfrobot.com)
  @version  V1.0
  @date  2021-10-28
  @url  https://github.com/DFRobot/DFRobot_ENS160
'''


ENS160_I2CADDR_DEFAULT = 0x53

ENS160_PART_ID = 0x0160

# ENS160 register address

# This 2-byte register contains the part number in little endian of the ENS160.
ENS160_PART_ID_REG = 0x00
# This 1-byte register sets the Operating Mode of the ENS160.
ENS160_OPMODE_REG = 0x10
# This 1-byte register configures the action of the INTn pin.
ENS160_CONFIG_REG = 0x11
# This 1-byte register allows some additional commands to be executed on the ENS160.
ENS160_COMMAND_REG = 0x12
# This 2-byte register allows the host system to write ambient temperature data to ENS160 for compensation.
ENS160_TEMP_IN_REG = 0x13
# This 2-byte register allows the host system to write relative humidity data to ENS160 for compensation.
ENS160_RH_IN_REG = 0x15
# This 1-byte register indicates the current STATUS of the ENS160.
ENS160_DATA_STATUS_REG = 0x20
# This 1-byte register reports the calculated Air Quality Index according to the UBA.
ENS160_DATA_AQI_REG = 0x21
# This 2-byte register reports the calculated TVOC concentration in ppb.
ENS160_DATA_TVOC_REG = 0x22
# This 2-byte register reports the calculated equivalent CO2-concentration in ppm, based on the detected VOCs and hydrogen.
ENS160_DATA_ECO2_REG = 0x24
# This 2-byte register reports the calculated ethanol concentration in ppb.
ENS160_DATA_ETOH_REG = 0x22
# This 2-byte register reports the temperature used in its calculations (taken from TEMP_IN, if supplied).
ENS160_DATA_T_REG = 0x30
# This 2-byte register reports the relative humidity used in its calculations (taken from RH_IN if supplied).
ENS160_DATA_RH_REG = 0x32
# This 1-byte register reports the calculated checksum of the previous DATA_ read transaction (of n-bytes).
ENS160_DATA_MISR_REG = 0x38
# This 8-byte register is used by several functions for the Host System to pass data to the ENS160.
ENS160_GPR_WRITE_REG = 0x40
# This 8-byte register is used by several functions for the ENS160 to pass data to the Host System.
ENS160_GPR_READ_REG = 0x48

# OPMODE(Address 0x10) register mode
ENS160_SLEEP_MODE = 0x00  # DEEP SLEEP mode (low power standby).
ENS160_IDLE_MODE = 0x01  # IDLE mode (low-power).
ENS160_STANDARD_MODE = 0x02  # STANDARD Gas Sensing Modes.


class DFRobot_ENS160:
    def __init__(self, i2c, reactor):
        self._i2c = i2c
        self._reactor = reactor

        # 100ms delay to wake up
        self._reactor.pause(self._reactor.monotonic() + 0.1)

        part_id = self.part_id()
        if part_id != ENS160_PART_ID:
            raise RuntimeError(
                "Expected ENS160 part id: 0x{:04X}, got 0x{:04X}"
                .format(ENS160_PART_ID, part_id))

        self.set_PWR_mode(ENS160_STANDARD_MODE)
        self.set_INT_mode(0x02)

    def part_id(self):
        params = self._i2c.i2c_read([ENS160_PART_ID_REG], 2)
        return struct.unpack('<H', params['response'])[0]

    def air_quality(self):
        params = self._i2c.i2c_read([ENS160_DATA_STATUS_REG], 6)
        # status, aqi, tvoc, eco2
        return struct.unpack('<BBHH', params['response'])

    def set_PWR_mode(self, mode):
        self._i2c.i2c_write([ENS160_OPMODE_REG, mode])
        self._reactor.pause(self._reactor.monotonic() + 0.02)

    def set_INT_mode(self, mode):
        self._i2c.i2c_write([ENS160_CONFIG_REG, mode])
        self._reactor.pause(self._reactor.monotonic() + 0.02)

    def set_temp_and_hum(self, temperature_C, humidity_rh):
        temp = int((temperature_C + 273.15) * 64)
        rh = int(humidity_rh * 512)
        self._i2c.i2c_write(struct.pack("<BHH", ENS160_TEMP_IN_REG, temp, rh))


def load_config_prefix(config):
    return ENS160(config)
