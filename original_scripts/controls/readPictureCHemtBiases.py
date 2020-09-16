"""
TODO: Combine readRackTemps.py and readHemtBiases.py
"""

import matplotlib.pyplot as plt
import serial
import time
import numpy as np

startMarker = '<'
endMarker = '>'
dataStarted = False
dataBuf = ""
messageComplete = False


# ========================
# ========================
# the functions

def setupSerial(baudRate, serialPortName):
    global serialPort

    serialPort = serial.Serial(port=serialPortName, baudrate=baudRate, timeout=0, rtscts=True)

    print("Serial port " + serialPortName + " opened  Baudrate " + str(baudRate))

    waitForArduino()


# ========================

def sendToArduino(stringToSend):
    # this adds the start- and end-markers before sending
    global startMarker, endMarker, serialPort

    stringWithMarkers = (startMarker)
    stringWithMarkers += stringToSend
    stringWithMarkers += (endMarker)

    serialPort.write(stringWithMarkers.encode('utf-8'))  # encode needed for Python3


# ==================

def recvLikeArduino():
    global startMarker, endMarker, serialPort, dataStarted, dataBuf, messageComplete

    if serialPort.inWaiting() > 0 and not messageComplete:
        x = serialPort.read().decode("utf-8")  # decode needed for Python3

        if dataStarted == True:
            if x != endMarker:
                dataBuf = dataBuf + x
            else:
                dataStarted = False
                messageComplete = True
        elif x == startMarker:
            dataBuf = ''
            dataStarted = True

    if (messageComplete == True):
        messageComplete = False
        return dataBuf
    else:
        return "XXX"


# ==================

def waitForArduino():
    # wait until the Arduino sends 'Arduino is ready' - allows time for Arduino reset
    # it also ensures that any bytes left over from a previous message are discarded

    print("Waiting for Arduino to reset")

    msg = ""
    while msg.find("Arduino is ready") == -1:
        msg = recvLikeArduino()
        if not (msg == 'XXX'):
            print(msg)


if __name__ == "__main__":

    setupSerial(9600, "COM6")
    hemt_num = 5
    prevTime = time.time()
    count = 0

    plt.ion()
    fig, ax = plt.subplots(3, 1, sharex=True)
    x = []
    y1 = []  # Vg
    y2 = []  # Id
    y3 = []  # Vd
    ax[0].set_ylabel("Vg")
    ax[0].set_xlabel("Time elapsed (s)")
    ax[0].plot(x, y1)
    ax[0].set_ylabel("Id")
    ax[1].plot(x, y2)
    ax[0].set_ylabel("Vd")
    ax[2].plot(x, y3)

    start = time.time()
    while True:
        # check for a reply
        arduinoReply = recvLikeArduino()
        if not (arduinoReply == 'XXX'):
            biasInfo = arduinoReply.split(" ")[:-1]
            print(f"Pin {biasInfo[0]}: {biasInfo[1]} V. Pin {biasInfo[2]}: {biasInfo[3]} V. Pin {biasInfo[4]}: {biasInfo[5]} V.")
            now = time.time()
            elapsed = now-start
            x.append(elapsed)
            y1.append(float(biasInfo[1]))
            y2.append(float(biasInfo[3]))
            y3.append(float(biasInfo[5]))

            x = x[-50:]
            y1 = y1[-50:]
            y2 = y2[-50:]
            y3 = y3[-50:]
            ax[0].cla()
            ax[0].autoscale_view()
            ax[0].plot(x, y1)
            ax[1].cla()
            ax[1].autoscale_view()
            ax[1].plot(x, y2)
            ax[2].cla()
            ax[2].autoscale_view()
            ax[2].plot(x, y3)
            plt.pause(1)

            # send a message at intervals
        if time.time() - prevTime > 1.0:
            sendToArduino(str(hemt_num))
            prevTime = time.time()
            count += 1

    plt.ioff()
    plt.show()
