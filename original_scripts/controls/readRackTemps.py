import serial
import csv
import time

#file = raw_input('Save File As: ')
saveFile = open('temperature data', 'w')

#serialport = raw_input('Enter Port: ')
# Here I used (can be found from the Arduino serial monitor by Tools > Port >)
serialport = '/dev/cu.usbmodem14101' #Change this according to the computer

print("Connecting to....", serialport)

arduino = serial.Serial(serialport, 9600)

print ("Arduino detected")

while True:
    time.sleep(.01)
    data = str(arduino.readline())
    saveFile.write(data + '\r\n')
    print(data)

# Try running sudo python3 if error with "no module named serial"
# If error shows up with "serial port busy", it might be because the serial monitor on the arduino interface is open. Close it and the code should be able to run.
