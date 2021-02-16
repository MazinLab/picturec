#!/usr/bin/env bash

# This script assumes user accounts are setup as follows
#  mazinlab
#  root
#  TODO

#Clone this repo
# git clone https://github.com/MazinLab/picturec.git /home/mazinlab/picturec

# Install anaconda and create the operating environment by running
# conda env create -f conda.yml

# Install dependencies and get computer ready for use

# Make sure all necessary repositories are installed (mkidcore/readout/pipeline)
#  if they become necessary



# Install the different configuration necessities for picturec
cd /home/mazinlab/picturec
sudo cp etc/redis/redis.conf /etc/redis/
sudo cp etc/systemd/system/* /etc/systemd/system/
sudo cp etc/udev/rules.d/* /etc/udev/rules.d/
sudo cp etc/modules /etc/ # For the lakeshore240 driver

# Install the picturec repository
conda activate picc
pip install -e /home/mazinlab/picturec

# Load the udev rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# Start redis server
sudo systemctl enable redis.service
sudo systemctl start redis.service

# Start instrument software
# Start hemtduino
sudo systemctl enable picc.service
sudo systemctl start picc.service

# Start currentduino
sudo systemctl enable /etc/systemd/system/currentduino.service
sudo systemctl start currentduino.service

# Start sim921
sudo systemctl enable /etc/systemd/system/sim921.service
sudo systemctl start sim921.service

# Start lakeshore240
sudo systemctl enable /etc/systemd/system/lakeshore240.service
sudo systemctl start lakeshore240.service

# Start sim960
sudo systemctl enable /etc/systemd/system/sim960.service
sudo systemctl start sim960.service
# sudo reboot