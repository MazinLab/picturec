# PICTURE-C Control Architecture
Picture-C is run by a suite of systemd daemons that interact with each other via a redis database. Most individual 
daemons are *agents*. Hardware devices are accessed and controlled by a single agent. E.g. the Sim960 by the 
sim960Agent. 

At startup agents pull their settings from the settings: namespace, barring a few hardcoded fundamental settings that
are fundamentally and immutably tied to the hardware design. Once running these values are updated by the relevant 
agent should they change and may be read by other programs on Pic-C to ascertain the current state. When changed the 
change is also broadcast via a pubsub channel of the same name. 

Requests to change settings may be made by publishing to command:setting_key, e.g. to request enabling the hemts:
 `settings:device::hemtduino:hemts-enabled: False` one would publish `command:device:hemtduino:hemts-enabled: True`.
Individual agents decide which keys they expose as commands and some keys are not set directly. For instance over a 
cooldown cycle the heatswitch will change state as will numerous `settings:device:sim960:...: ` keys. These changes are 
triggered at the appropriate time by merely publishing, for example,  `command:cooldown: now`. 

Agents may pull their settings from redis at arbitrary times (e.g. if a device has an upset and an agent crashes it
may use redis to come back up). Extreme care and understanding must be used if editing the redis database directly via
redis-commander. While agents endeavor to provide clear lists at the top of their files of which commands are listened 
for and when settings may be pulled from redis the latter is not a guarantee as multi-threaded operation and upsets 
could cause novel behavior.    

In addition to the `settings:` and `command:` namespaces, the `status:` namespace is used by agents to store 
status information. The values here, be they normal or timeseries, may go stale in the event that the authoritative 
agent is offline. Updates to status values are also published under a corresponding pubsub channel of the same name. 

Finally, the `tcs:` `readout:` and `site:` namespaces are reserved for future use. 


## Installation
git clone https://github.com/MazinLab/picturec.git

./install.sh

systemctl reboot

NOTE: add pip install information here