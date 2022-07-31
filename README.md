# Klipper Nevermore Max

Klipper modules for various nevermore max air quality sensors

## Klipper Plugin

### Sample Config

#### Manual Installation

Clone this repo and run the `install_klipper.sh` script. Example:

```bash
cd /home/pi
git clone https://github.com/nknotts/klipper-nevermore-max.git
./nevermore-max-controller/install_klipper.sh
```

It's safe to execute the install script multiple times.

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
