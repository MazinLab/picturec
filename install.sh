#!/usr/bin/env bash

# TODO: Before anything: (1) make sure anaconda is installed and (2) an environment is created

# Install dependencies and get computer ready for use
sudo apt update
sudo apt full-upgrade
# TODO: Consider installing python/anaconda? Should we assume it's already installed?
conda install python  # Decide if there's a specific version we want to use (or need to use). Also figure out what is just there from the .yml file
conda install ipython
conda install redis
conda install pyserial
pip install redistimeseries

# Make sure all necessary repositories are installed (mkidcore/readout/pipeline)
#  if they become necessary

# TODO: Decide where the picturec repository will live and what user will be running the programs (probably kids)
#  this will be important because it tells us what commands we will need to run as(/not as) sudo and what ownership the
#  repo needs (chown -R kids /picturec or something like that)

# Install the picturec repository
cd /
git clone https://github.com/MazinLab/picturec.git /picturec
# pip install picturec and make it pip installable correctly so import statements work

# Install the different configuration necessities for picturec
cd /picturec
pip install -e /picturec
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