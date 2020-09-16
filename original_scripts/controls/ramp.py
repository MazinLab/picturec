"""
TODO: Rewrite ramp loop
"""

import serial
import time
import numpy as np


def setupSerial(baudRate, serialPortName):
    global serialPort

    serialPort = serial.Serial(port=serialPortName, baudrate=baudRate, timeout=0)
    print("Serial port " + serialPortName + " opened  Baudrate " + str(baudRate))

    serialPort.write(b"xyz\r\n")

    serialPort.write(b"*IDN?\r\n")
    time.sleep(.1)
    module = serialPort.readline().decode('ascii').rstrip('\r\n')
    print(f"Connected to {module}")

    for i in range(5):
        serialPort.reset_input_buffer()
        time.sleep(.1)
        serialPort.reset_output_buffer()
        time.sleep(.1)

    print("Closing...")
    time.sleep(.2)
    serialPort.close()

    print("Opening...")
    time.sleep(.2)
    serialPort.open()

    if module.split(",")[1] == 'SIM900':
        serialPort.write(b"xyz\r\n")

        print("Connecting to sub-module")
        time.sleep(.2)
        serialPort.write(b'CONN 5,"xyz"\r\n')
        time.sleep(.2)
        serialPort.write(b'*IDN?\r\n')
        time.sleep(.2)
        submodule = serialPort.readline().decode('ascii').rstrip('\r\n')
        submodule = submodule.split(",")[1]
        if submodule == 'SIM960':
            print(f"Connected to sub-module {submodule}")
        else:
            print("Failed to connect!")
    elif module.split(",")[1] == 'SIM960':
        print("Connected to SIM960")


def calculateInitialOffsetV():
    offsetVals = []
    serialPort.write(b'MOUT 0\r\n')
    time.sleep(3)
    for i in range(5):
        serialPort.readline()
        time.sleep(.1)

    print("Starting offset calibration")
    for i in range(10):
        serialPort.write(b'OMON?\r\n')
        time.sleep(0.15)
        oV = serialPort.readline().decode('ascii').rstrip('\r\n')
        print(oV)
        offsetVals.append(float(oV))
    offsetVals = np.array(offsetVals)
    offset = round(np.average(offsetVals), 3)
    if offset < 0:
        offset = 0

    print(f"Offset = {offset} V")

    return offset


def stepUp(startV, offset, stepSize):
    stepSuccessful = False
    originalV = round(startV, 3)
    while stepSuccessful is False:
        targetV = round(startV + stepSize, 3)
        mOutCmd = round(targetV - offset, 3)

        print(f"Set V_out from {originalV} to {targetV} with {offset} V offset. Commanding M_out to {mOutCmd}.")
        cmdStr = "MOUT "+str(mOutCmd)+"\r\n"

        commandSent = False
        while commandSent is False:
            serialPort.write(cmdStr.encode('ascii'))
            time.sleep(0.1)
            serialPort.write(b"MOUT?\r\n")
            time.sleep(0.1)
            checkMoutVal = round(float(serialPort.readline().decode('ascii').rstrip('\r\n')), 3)

            if mOutCmd == checkMoutVal:
                print(f"M_out successfully sent to {mOutCmd} V.")
                commandSent = True
            else:
                print("Failed to execute command")

        serialPort.write(b"OMON?\r\n")
        time.sleep(0.25)
        checkVoutVal = round(float(serialPort.readline().decode('ascii').rstrip('\r\n')), 3)
        endV = checkVoutVal
        print(f"V_out is {checkVoutVal} V.")

        if abs(checkVoutVal - targetV) > 0.01:
            offset = round(checkVoutVal - checkMoutVal, 3)
        else:
            stepSuccessful = True

    return[originalV, endV, offset, stepSize]

def stepDown(startV, offset, stepSize):
    stepSuccessful = False
    originalV = round(startV, 3)
    while stepSuccessful is False:
        targetV = round(startV - stepSize, 3)
        mOutCmd = round(targetV - offset, 3)

        print(f"Set V_out from {originalV} to {targetV} with {offset} V offset. Commanding M_out to {mOutCmd}.")
        cmdStr = "MOUT " + str(mOutCmd) + "\r\n"

        commandSent = False
        while commandSent is False:
            serialPort.write(cmdStr.encode('ascii'))
            time.sleep(0.1)
            serialPort.write(b"MOUT?\r\n")
            time.sleep(0.1)
            checkMoutVal = round(float(serialPort.readline().decode('ascii').rstrip('\r\n')), 3)

            if mOutCmd == checkMoutVal:
                print(f"M_out successfully sent to {mOutCmd} V.")
                commandSent = True
            else:
                print("Failed to execute command")

        serialPort.write(b"OMON?\r\n")
        time.sleep(0.1)
        checkVoutVal = round(float(serialPort.readline().decode('ascii').rstrip('\r\n')), 3)
        endV = checkVoutVal
        print(f"V_out is {checkVoutVal} V.")

        if abs(checkVoutVal - targetV) > 0.01:
            offset = round(checkVoutVal - checkMoutVal, 3)
        else:
            stepSuccessful = True

    return [originalV, endV, offset, stepSize]


def soakStep(soakV, offset):
    stepSuccessful = False
    originalV = round(soakV, 3)

    while stepSuccessful is False:
        targetV = originalV

        print(f"Soaking at {soakV}")

        serialPort.write(b"OMON?\r\n")
        time.sleep(0.1)
        checkVoutVal = round(float(serialPort.readline().decode('ascii').rstrip('\r\n')), 3)
        endV = checkVoutVal
        print(f"V_out is {checkVoutVal} V.")

        commandSent = True
        vDrifted = False
        if abs(checkVoutVal - targetV) > 0.01:
            offset = checkVoutVal - targetV
            vDrifted = True
            commandSent = False
        else:
            stepSuccessful = True

        while commandSent is False and vDrifted is True:
            targetMout = round(targetV - offset, 3)
            cmdStr = "MOUT " + str(targetMout) + "\r\n"

            serialPort.write(cmdStr.encode('ascii'))
            time.sleep(0.1)
            serialPort.write(b"MOUT?\r\n")
            time.sleep(0.1)
            checkMoutVal = round(float(serialPort.readline().decode('ascii').rstrip('\r\n')), 3)

            if targetMout == checkMoutVal:
                print(f"M_out successfully sent to {targetMout} V.")
                commandSent = True
            else:
                print("Failed to execute command")

            serialPort.write(b"OMON?\r\n")
            time.sleep(0.1)
            checkVoutVal = round(float(serialPort.readline().decode('ascii').rstrip('\r\n')), 3)
            endV = checkVoutVal
            print(f"V_out is {checkVoutVal} V.")

            if abs(checkVoutVal - targetV) > .01:
                offset = round(checkVoutVal - checkMoutVal, 3)
            else:
                vDrifted = False

    return [originalV, endV, offset, 0]


if __name__ == "__main__":
    print("Starting")

    # Connect to serial port (configured for Noah's laptop, will change when moved to Linux computer)
    setupSerial(9600, "COM3")

    # Calculate any offset in voltage between what is commanded and what the SIM960 outputs
    vOffset = calculateInitialOffsetV()

    # Set the 'true' output to 0 V.
    print("Setting output to 0.000 V initially")
    cmd1 = "MOUT "+str(0-vOffset)+"\r\n"
    serialPort.write(cmd1.encode('ascii'))
    time.sleep(1)
    serialPort.write(b"OMON?\r\n")
    time.sleep(.1)
    oMonStart = round(float(serialPort.readline().decode('ascii').rstrip('\r\n')), 3)
    print(f"Output is currently: {oMonStart} V\n")
    time.sleep(1)

    # Initialize arrays for data to be stored. Set the starting ramp mode to 'up'
    count = 0
    stepDelta = 0.001
    vMin = 0
    vMax = 1

    if vMin < 0:
        vMin = 0
    if vMax > 9.5:
        vMax = 9.5

    soakTime = 120
    mode = 'up'
    prevTime = time.time()
    startTime = int(prevTime)
    times = [int(prevTime)]
    outputVals = [oMonStart]
    setVals = [0]
    offsets = [vOffset]
    steps = [0]
    stepNumber = 0

    # Start the ramping loop
    print(f"------------ Starting loop at {int(time.time())} ------------\n")
    while True:
        now = time.time()
        if now - prevTime > 1:
            stepNumber += 1
            steps.append(stepNumber)
            if (int(now)-startTime)%60 == 0:
                print(f"*************TIME ELAPSED IS {int((now-startTime)/60)} MINUTES ***************")
            print(stepNumber)
            print(mode)
            if setVals[-1] < vMin:
                setVals[-1] = vMin
            elif setVals[-1] > vMax:
                setVals[-1] = vMax
            if mode == 'up':
                oldV, newV, offset, stepSize = stepUp(setVals[-1], offsets[-1], stepDelta)
                setVals.append(round(oldV + stepDelta, 3))
                outputVals.append(round(newV, 3))
                offsets.append(round(offset, 3))
                times.append(now)
            elif mode == 'down':
                oldV, newV, offset, stepSize = stepDown(setVals[-1], offsets[-1], stepDelta)
                setVals.append(round(oldV - stepDelta, 3))
                outputVals.append(round(newV, 3))
                offsets.append(round(offset, 3))
                times.append(now)
            elif mode == 'soak':
                oldV, newV, offset, stepSize = soakStep(setVals[-1], offsets[-1])
                setVals.append(round(oldV, 3))
                outputVals.append(round(newV, 3))
                offsets.append(round(offset, 3))
                times.append(now)
            else:
                break

            if setVals[-1] == vMax and not all(o == vMax for o in setVals[-soakTime:]):
                mode = 'soak'
            elif setVals[-1] == vMin and not all(o == vMin for o in setVals[-soakTime:]):
                mode = 'soak'
            elif all(o == vMax for o in setVals[-soakTime:]):
                mode = 'down'
            elif all(o == vMin for o in setVals[-soakTime:]) and len(setVals) > 10:
                break
            else:
                pass

            prevTime = time.time()
            print("\n")


    times = np.array(times)
    setVals = np.array(setVals)
    outputVals = np.array(outputVals)

    data = np.transpose(np.array([times, setVals, outputVals, steps]))
    np.savetxt('ramp_data.txt', data, fmt="%14.8f")

    print("Closing serial connection")
    serialPort.write(b"xyz\r\n")
    serialPort.close()
