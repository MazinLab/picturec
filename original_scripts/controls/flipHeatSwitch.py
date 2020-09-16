import serial
import time

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

    if serialPort.inWaiting() > 0 and messageComplete is False:
        x = serialPort.read().decode("utf-8")  # decode needed for Python3

        if dataStarted is True:
            if x != endMarker:
                dataBuf = dataBuf + x
            else:
                dataStarted = False
                messageComplete = True
        elif x == startMarker:
            dataBuf = ''
            dataStarted = True

    if messageComplete is True:
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

    setupSerial(9600, "COM7")
    prevTime = time.time()
    prevTime1 = int(prevTime)
    count = 0
    lastCmd = "o"

    start = time.time()
    while True:
        # check for a reply
        arduinoReply = recvLikeArduino()
        if not (arduinoReply == 'XXX'):
            biasInfo = arduinoReply
            # print(f"Pin {biasInfo[0]}: {biasInfo[1]} V. Pin {biasInfo[2]}: {biasInfo[3]} V. Pin {biasInfo[4]}: {biasInfo[5]} V.")
            now = time.time()
            print(f"Time {int(now)} - {biasInfo}")

            # send a message at intervals
        if int(time.time() - prevTime1) == 1:
            print(int(time.time()))
            prevTime1 = int(time.time())
        if time.time() - prevTime > 15.0:
            if lastCmd == "o":
                sendToArduino("c")
                lastCmd = "c"
            elif lastCmd == "c":
                sendToArduino("o")
                lastCmd = "o"
            else: pass
            prevTime = time.time()
            count += 1