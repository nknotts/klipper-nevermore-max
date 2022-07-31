#!/bin/bash

# Adopted from https://github.com/protoloft/klipper_z_calibration/blob/master/install.sh

KLIPPER_PATH="${HOME}/klipper"

# Step 1: Verify Klipper has been installed
check_klipper()
{
    if [ "$EUID" -eq 0 ]; then
        echo "This script must not run as root"
        exit -1
    fi

    if [ "$(sudo systemctl list-units --full -all -t service --no-legend | grep -F "klipper.service")" ]; then
        echo "Klipper service found!"
    else
        echo "Klipper service not found, please install Klipper first"
        exit -1
    fi

}

# Step 2: link extension to Klipper
link_extension()
{
    echo "Linking extension to Klipper..."
    ln -sf "${SRCDIR}/klippy/extras/aht21.py"  "${KLIPPER_PATH}/klippy/extras/"
    ln -sf "${SRCDIR}/klippy/extras/ens160.py" "${KLIPPER_PATH}/klippy/extras/"
    ln -sf "${SRCDIR}/klippy/extras/sgp30.py"  "${KLIPPER_PATH}/klippy/extras/"

    if grep -q "aht21" "${KLIPPER_PATH}/klippy/extras/temperature_sensors.cfg"; then
        echo "temperature_sensors.cfg already set"
    else
        echo "updating temperature_sensors.cfg"
        printf "\n[aht21]\n" >> "${KLIPPER_PATH}/klippy/extras/temperature_sensors.cfg"
    fi
}

# Step 3: restarting Klipper
restart_klipper()
{
    echo "Restarting Klipper..."
    sudo systemctl restart klipper
}


# Force script to exit if an error occurs
set -e

# Find SRCDIR from the pathname of this script
SRCDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/ && pwd )"

# Parse command line arguments
while getopts "k:" arg; do
    case $arg in
        k) KLIPPER_PATH=$OPTARG;;
    esac
done

# Run steps
check_klipper
link_extension
restart_klipper
