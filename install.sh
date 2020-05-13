# Stuff will go here first

sudo cp etc/redis/redis.conf /etc/redis/
sudo cp etc/systemd/system/* /etc/systemd/system/
sudo cp etc/udev/rules.d/* /etc/udev/rules.d/

# Load the udev rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# Start redis server
sudo systemctl enable /etc/systemd/system/redis.service
sudo systemctl start redis.service