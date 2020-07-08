#!/usr/bin/env bash

# Install dependencies and get computer ready for use
sudo apt update
sudo apt full-upgrade
# Consider installing python/anaconda?
sudo pip install redis
sudo pip install redistimeseries
sudo pip install pyserial

# Make sure all necessary repositories are installed (mkidcore/readout/pipeline)
#  if they become necessary

# Install the picturec repository
cd /
git clone https://github.com/MazinLab/picturec.git /picturec
# pip install picturec and make it pip installable correctly so import statements work

# Install the different configuration necessities for picturec
cd /picturec
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

# sudo reboot