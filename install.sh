#!/usr/bin/env bash

# Install dependencies and get computer ready for use
sudo apt update
sudo apt full-upgrade

# Install the picturec repository
cd /
git clone https://github.com/MazinLab/picturec.git /picturec/

# Install the different configuration necessities for picturec
sudo cp etc/redis/redis.conf /etc/redis/
sudo cp etc/systemd/system/* /etc/systemd/system/
sudo cp etc/udev/rules.d/* /etc/udev/rules.d/

# Load the udev rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# Start redis server
sudo systemctl enable /etc/systemd/system/redis.service
sudo systemctl start redis.service

# Start hemtduino
sudo systemctl enable /etc/systemd/system/hemtduino.service
sudo systemctl start hemtduino.service

#Start currentduino
sudo systemctl enable /etc/systemd/system/currentduino.service
sudo systemctl start currentduino.service