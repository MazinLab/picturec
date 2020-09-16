"""
TODO: Make a function for the ramp
TODO: Keep the offset updating continuously for smoother ramping (see if this effect occurs for bigger steps)
TODO: Add check
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

def step(currentV, offset, mode, stepSize):
    stepSuccessful = False
    print(mode)
    while stepSuccessful is False:
        if mode == 'up':
            targetV = round(currentV + stepSize, 3)
            targetMout = round(targetV - offset, 3)

            print(f"Set V_out from {currentV} to {targetV} with {offset} V offset. Commanding M_out to {targetMout} in {mode} mode.")
            cmdStr = "MOUT "+str(targetMout)+"\r\n"

            commandSent = False
            while commandSent is False:
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
            print(f"V_out is {checkVoutVal} V.")

            if abs(checkVoutVal - targetV) > .002:
                offset = checkVoutVal - checkMoutVal
            else:
                stepSuccessful = True

    return [currentV, checkVoutVal, offset]


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
    times = []
    outputVals = []
    setVals = []
    mode = 'up'
    prevTime = time.time()

    # Start the ramping loop
    print(f"------------ Starting loop at {int(time.time())} ------------\n")
    while True:
        now = time.time()
        if now - prevTime > 1:
            if count == 0:
                vOut = 0
                offset = vOffset
            else:
                vOut = round(oldV + stepDelta, 3)
                offset = round(newOffset, 3)

            oldV, newV, newOffset = step(vOut, offset, mode, stepDelta)

            times.append(now)
            outputVals.append(newV)
            setVals.append(oldV+stepDelta)
            if setVals[-1] == .1 and not all(o == .1 for o in setVals[-60:]):
                mode = 'soak'
            if all(o == .1 for o in setVals[-60:]):
                mode = 'down'
            elif all(o == 0 for o in setVals[-60:]) and len(setVals) > 10:
                break

            # print(mode+"\n")
            print(f"{count}\n")

            prevTime = time.time()
            if mode == 'up':
                count += 1
            elif mode == 'down':
                count -= 1
            elif mode == 'soak':
                pass
            else:
                break

    times = np.array(times)
    setVals = np.array(setVals)
    outputVals = np.array(outputVals)

    data = np.transpose(np.array([times, setVals, outputVals]))
    np.savetxt('ramp_data.txt', data, fmt="%14.8f")

    print("Closing serial connection")
    serialPort.write(b"xyz\r\n")
    serialPort.close()
