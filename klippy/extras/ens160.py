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
import datetime

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
        self.csv_basename = config.get("csv_basename", default=None)
        self.csv_log_queue = queue.Queue()
        self.eCO2 = self.TVOC = None
        self.ens160 = None
        self.temperature_sensor = None
        self.temperature_sensor_name = config.get(
            "temperature_sensor", default=None)
        self.temperature_key = config.get(
            "temperature_key", default="temperature")
        self.temperature_initial = config.getfloat(
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

        gcode = self.printer.lookup_object('gcode')
        gcode.register_mux_command("AIR_QUALITY_CSV_LOGGING_START",
                                   "NAME",
                                   self.name,
                                   self.cmd_CSV_LOGGING_START,
                                   desc="Start air quality csv logging")
        gcode.register_mux_command("AIR_QUALITY_CSV_LOGGING_STOP",
                                   "NAME",
                                   self.name,
                                   self.cmd_CSV_LOGGING_STOP,
                                   desc="Stop air quality csv logging")

    def cmd_CSV_LOGGING_START(self, gcmd):
        if self.csv_basename:
            self.csv_log_queue.put_nowait({
                "type": "start",
                "gcmd": gcmd
            })
        else:
            gcmd.respond_info("ens160 {}: csv_basename not specified"
                              .format(self.name))

    def cmd_CSV_LOGGING_STOP(self, gcmd):
        if self.csv_basename:
            self.csv_log_queue.put_nowait({
                "type": "stop",
                "gcmd": gcmd
            })
        else:
            gcmd.respond_info("ens160 {}: csv_basename not specified"
                              .format(self.name))

    def handle_connect(self):
        self.ens160 = DFRobot_ENS160(self.i2c, self.reactor)

        if self.temperature_sensor_name:
            self.temperature_sensor = self.printer.lookup_object(
                self.temperature_sensor_name)

        if self.humidity_sensor_name:
            self.humidity_sensor = self.printer.lookup_object(
                self.humidity_sensor_name)

        if self.csv_basename:
            threading.Thread(target=csv_logger, args=(
                self.name, self.csv_basename,
                self.reactor, self.csv_log_queue)).start()

        self.reactor.update_timer(self.sample_timer, self.reactor.NOW)

    def sample_ens160(self, eventtime):
        measured_time = self.reactor.monotonic()

        # update ens160 temp/humidity to aid with measurement compensation
        temperature_status = self.temperature_sensor.get_status(eventtime) \
            if self.temperature_sensor \
            else {self.temperature_key: self.temperature_initial}
        tempC = temperature_status[self.temperature_key]
        humidity_status = self.humidity_sensor.get_status(eventtime) \
            if self.humidity_sensor \
            else {self.humidity_key: self.humidity_initial}
        humidity = humidity_status[self.humidity_key]

        try:
            # require both temperature/humidity to update ens160 compensation
            if tempC and humidity:
                self.ens160.set_temp_and_hum(tempC, humidity)

            # perform measurement
            status, aqi, self.TVOC, self.eCO2 = self.ens160.air_quality()
            hydrogen, acetone, carbon_monoxide, toluene = self.ens160.raw()
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
        if self.csv_basename:
            self.csv_log_queue.put_nowait({
                "type": "update",
                "monotonic": now,
                "status": status,
                "aqi": aqi,
                "eco2": self.eCO2,
                "tvoc": self.TVOC,
                "hydrogenRaw": hydrogen,
                "acetoneRaw": acetone,
                "carbonMonoxideRaw": carbon_monoxide,
                "tolueneRaw": toluene,
                "temperature": temperature_status,
                "humidity": humidity_status
            })

        # schedule next loop
        return measured_time + self.report_time

    def get_status(self, eventtime):
        return {
            'eco2': self.eCO2,
            'tvoc': self.TVOC,
        }


def csv_logger(name, basename, reactor, data_queue):
    try:
        def gcmd_result(gcmd, val):
            reactor.register_async_callback((lambda e: gcmd.respond_info(val)))

        csvfile = None
        csvwriter = None
        while True:
            item = data_queue.get()
            item_type = item['type']
            if item_type == "start":
                if not csvfile:
                    filename = basename + datetime.datetime.now().strftime(
                        "_%Y-%m-%d_%H-%M-%S.csv")
                    csvfile = open(filename, 'w')
                    gcmd_result(item['gcmd'],
                                "ens160 {}: csv logging started '{}'"
                                .format(name, filename))
                else:
                    gcmd_result(item['gcmd'],
                                "ens160 {}: csv logging already started"
                                .format(name))
            elif item_type == "stop":
                if csvfile:
                    csvfile.close()
                    csvfile = None
                    csvwriter = None
                    gcmd_result(item['gcmd'],
                                "ens160 {}: csv logging stopped".format(name))
                else:
                    gcmd_result(item['gcmd'],
                                "ens160 {}: csv logging already stopped"
                                .format(name))
            elif item_type == "update" and csvfile:
                t = item['temperature']
                h = item['humidity']

                if not csvwriter:
                    csvwriter = csv.writer(csvfile)
                    row = ['unix_time', 'monotonic_time', 'AQI', 'ECO2', 'TVOC',
                           'hydrogen_raw', 'acetone_raw', 'carbon_monoxide_raw',
                           'toluene_raw']
                    row.extend("temperature_{}".format(x) for x in sorted(t))
                    row.extend("humidity_{}".format(x) for x in sorted(h))
                    csvwriter.writerow(row)

                row = [time.time(), item['monotonic'], item['aqi'],
                       item['eco2'], item['tvoc'], item['hydrogenRaw'],
                       item['acetoneRaw'], item['carbonMonoxideRaw'],
                       item['tolueneRaw']]
                row.extend(t[k] for k in sorted(t))
                row.extend(h[k] for k in sorted(h))
                csvwriter.writerow(row)
                csvfile.flush()

            data_queue.task_done()
    except Exception as err:
        logging.exception("ens160 {}: csv error - {}".format(name, err))


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

    def raw(self):
        params = self._i2c.i2c_read([ENS160_GPR_READ_REG], 8)
        # hydrogen, acetone, carbon monoxide, toluene
        vals = struct.unpack('<HHHH', params['response'])
        return list(int(2.0**(x/2048.0)) for x in vals)


def load_config_prefix(config):
    return ENS160(config)
