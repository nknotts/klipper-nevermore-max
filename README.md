# Klipper Nevermore Max

Klipper modules for various nevermore max air quality sensors

## Klipper Plugin

### Sample Config

Add to `printer.cfg`

```
[temperature_sensor chamber]
sensor_type: BME280
i2c_bus: i2c0e
i2c_address: 119

[sgp30 chamber]
i2c_bus: i2c0e
temperature_sensor: bme280 chamber
csv_filename: /tmp/sgp30_log.csv

[temperature_sensor exhaust]
sensor_type: AHT21
i2c_bus: i2c0e

[ens160 exhaust]
i2c_bus: i2c0e
temperature_sensor: aht21 exhaust
csv_filename: /tmp/ens160_log.csv
```

#### Installation

Clone this repo and run the `install_klipper.sh` script. Example:

```bash
cd ~
git clone https://github.com/nknotts/klipper-nevermore-max.git
./klipper-nevermore-max/install_klipper.sh
```

It is safe to execute the install script multiple times.

More on this in the [Moonraker Update Manager](#moonraker-update-manager) section.

### Moonraker Update Manager

It is possible to keep this extension up to date via Moonraker's
[update manager](https://github.com/Arksine/moonraker/blob/master/docs/configuration.md#update_manager)
by adding the following configuration block to the `moonraker.conf` of your printer:

```text
[update_manager klipper-nevermore-max]
type: git_repo
origin: https://github.com/nknotts/klipper-nevermore-max.git
path: /home/pi/klipper-nevermore-max
install_script: install_klipper.sh
managed_services: klipper
primary_branch: main
```

This requires this repository to be cloned into your home directory (e.g. /home/pi):

```bash
git clone https://github.com/nknotts/klipper-nevermore-max.git
```

The install script assumes that Klipper is also installed in your home directory under
"klipper": `${HOME}/klipper`.

>:point_up: **NOTE:** If your Moonraker is not on a recent version, you may get an error
> with the "managed_services" line!


## Credits

* [VORON](https://vorondesign.com/) - great open source 3D printer hardware design and community
* [Klipper](https://github.com/Klipper3d/klipper) - great open source 3D printer firmware
* [Klipper Z Calibration](https://github.com/protoloft/klipper_z_calibration) - basis for install scripts
