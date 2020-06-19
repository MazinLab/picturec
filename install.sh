#!/usr/bin/env bash
# Stuff will go here first
# Assuming a fresh install with none of the computer set up, redis and redistimeseries installation must be included

# Add an actual copy of the picture-c repository!

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