"""
LICENSE LGPL <http://www.gnu.org/licenses/lgpl.html>
        (c) Nedim Jackman 2008 (c) Pierrick Koch 2016 (c) Janek Bober 2023
"""

import time
import logging
import collections
import bluetooth
import csv
from datetime import datetime
import os



# Save data to:
directory = './RawData'


# WiiboardSampling Parameters
#Sampling time
Minutes = 20
Seconds = 0


N_SAMPLES = 100*(Seconds + 60* Minutes) #Roughly 10ms between samples 
SESSION_TIME = Seconds + 60* Minutes #Session time in seconds
N_LOOP = 0
T_SLEEP = 2
BATTERY_MAX = 200.0
TOP_RIGHT = 0
BOTTOM_RIGHT = 1
TOP_LEFT = 2
BOTTOM_LEFT = 3
BLUETOOTH_NAME = "Nintendo RVL-WBC-01"

# Wiiboard Parameters
CONTINUOUS_REPORTING = b'\x04' #Read reporting mode
COMMAND_REPORTING = b'\x12' #Write reporting mode

COMMAND_LIGHT = b'\x11' #Write light mode

COMMAND_READ_REGISTER = b'\x17' #Setting register read location
COMMAND_REGISTER = b'\x16' #Setting register write location

COMMAND_REQUEST_STATUS = b'\x15' #Request board status
INPUT_STATUS = b'\x20' #Read board status
INPUT_READ_DATA = b'\x21' #Read calibration data
EXTENSION_8BYTES = b'\x32' #Specify 8 bytes of data
BUTTON_DOWN_MASK = 0x08 #Check if button is pressed
LED1_MASK = 0x10 #Check if LED is on






# initialize the logger
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()  # or RotatingFileHandler
handler.setFormatter(logging.Formatter('[%(asctime)s][%(name)s][%(levelname)s] %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)  # or DEBUG


#b2i = lambda b: int.from_bytes(b, "big")
b2i = lambda b: b if isinstance(b, int) else int.from_bytes(b, "big")


#Search for Wiiboard
def discover(duration=10, prefix=BLUETOOTH_NAME):
    logger.info("Scan Bluetooth devices for %i seconds...", duration)
    devices = bluetooth.discover_devices(duration=duration, lookup_names=True)
    logger.debug("Found devices: %s", str(devices))
    return [address for address, name in devices if name.startswith(prefix)]






#Wiiboard actions

class Wiiboard:
    def __init__(self, address=None, csv_writer=None):
        #logger.debug("Entered _init_ method")
        self.csv_writer = csv_writer
        self.controlsocket = bluetooth.BluetoothSocket(bluetooth.L2CAP)
        self.receivesocket = bluetooth.BluetoothSocket(bluetooth.L2CAP)
        self.calibration = [[1e4] * 4] * 3
        self.calibration_requested = False
        self.light_state = False
        self.button_down = False
        self.battery = 0.0
        self.running = True
        if address is not None:
            self.connect(address)

    #Connect to Wiiboard
    def connect(self, address):
        #logger.debug("Entered connect method")
        logger.info("Connecting to %s", address)
        self.controlsocket.connect((address, 0x11))
        self.receivesocket.connect((address, 0x13))
        #logger.debug("Sending mass calibration request")
        # Set read from this particular wiiboard register
        self.send(COMMAND_READ_REGISTER, b"\x04\xA4\x00\x24\x00\x18")
        self.calibration_requested = True
        logger.info("Wait for calibration")
        #logger.debug("Connect to the balance extension, to read mass data")
        # Set write to this particular wiiboard register
        self.send(COMMAND_REGISTER, b"\x04\xA4\x00\x40\x00")
        #logger.debug("Request status")
        self.status
        self.light(0)
        #logger.debug("Exited connect method")
    
    #Prepare board to recieve data
    def send(self, *data):
        #logger.debug("Entered send method")
        self.controlsocket.send(b'\x52' + b''.join(data))
        #logger.debug("Exited send method")

    # Setting reporting mode to continuous
    def reporting(self, mode=CONTINUOUS_REPORTING, extension=EXTENSION_8BYTES):
        #logger.debug("Entered reporting method")
        self.send(COMMAND_REPORTING, mode, extension)

    # Switch on LED if on_off is True
    def light(self, on_off=True):
        #logger.debug("Entered light method")
        self.send(COMMAND_LIGHT, b'\x10' if on_off else b'\x00')

    # Request board status
    def status(self):
        #logger.debug("Entered status method")
        self.send(COMMAND_REQUEST_STATUS, b'\x00')


    #Weight in kg after calibration for specified corner
    def calc_mass(self, raw, pos):
        #logger.debug("Entered calc_mass method")
        # Calculates the Kilogram weight reading from raw data at position pos
        # calibration[0] is calibration values for 0kg
        # calibration[1] is calibration values for 17kg
        # calibration[2] is calibration values for 34kg
        if raw < self.calibration[0][pos]:
            return 0.0 #Below 1kg is not possible?
        elif raw < self.calibration[1][pos]: 
            return 17 * ((raw - self.calibration[0][pos]) /
                        float((self.calibration[1][pos] -
                                self.calibration[0][pos])))
        else:  # if raw >= self.calibration[1][pos]: 
            return 17 + 17 * ((raw - self.calibration[1][pos]) /
                            float((self.calibration[2][pos] -
                                    self.calibration[1][pos])))
    
    #Indicate if button is pressed
    def check_button(self, state):
        #logger.debug("Entered check_button method")
        if state == BUTTON_DOWN_MASK:
            if not self.button_down:
                self.button_down = True
                self.on_pressed()
        elif self.button_down:
            self.button_down = False
            self.on_released()

    #Converting weights to kg
    def get_mass(self, data):
        #logger.debug("Entered get_mass method")
        mass_dict = {
            'top_right': self.calc_mass(b2i(data[0:2]), TOP_RIGHT),
            'bottom_right': self.calc_mass(b2i(data[2:4]), BOTTOM_RIGHT),
            'top_left': self.calc_mass(b2i(data[4:6]), TOP_LEFT),
            'bottom_left': self.calc_mass(b2i(data[6:8]), BOTTOM_LEFT),
        }
        #print(b2i(data[0:2]))
        #print(b2i(data[2:4]))
        #print(b2i(data[4:6]))
        #print(b2i(data[6:8]))
        TR = mass_dict['top_right']
        BR = mass_dict['bottom_right']
        TL = mass_dict['top_left']
        BL = mass_dict['bottom_left']
        self.csv_writer.writerow([TR, BR, TL, BL])
        
        
        return mass_dict


    def loop(self):
        #logger.debug("Entered loop method")
        while self.running and self.receivesocket:
            data = self.receivesocket.recv(25)
            #logger.debug("socket.recv(25): %r", data)
            if len(data) < 2:
                continue
            input_type = data[1]
            #Checking communication is status
            if input_type == ord(INPUT_STATUS):
                self.battery = b2i(data[7:9]) / BATTERY_MAX
                # 0x12: on, 0x02: off/blink
                self.light_state = data[4] & LED1_MASK == LED1_MASK
                self.on_status()

             #Checking communication is factory calibration data
             #self.calibration = [[5944, 3314, 12024, 4221], [7688, 4994, 13794, 5967], [9449, 6683, 15565, 7716]] from factory
            elif input_type == ord(INPUT_READ_DATA):
                #logger.debug("Got calibration data")
                if self.calibration_requested:
                    #logger.debug("self.cal true")
                    length = b2i(data[4]) // 16 + 1 #Finding length encoded in first 4 bits of 5th byte (1-16)
                    data = data[7:7 + length] #Data is now Data[7] to Data[7+length]
                    cal = lambda d: [b2i(d[j:j + 2]) for j in [0, 2, 4, 6]] #Lambda function to convert 8 bytes to 4 16bit unsigned integers
                    #logger.debug("cal calculated")
                    
                #Creating 3x4 nested list of calibration data.
                if length == 16:  # First packet of calibration data (16 bytes)
                    self.calibration = [cal(data[0:8]), cal(data[8:16]), [1e4] * 4] #Split data into 2 lists of 4 16bit unsigned integers (+extra list with fixed values)
                elif length < 16:  # Second packet of calibration data inserted into 3rd list
                    self.calibration[2] = cal(data[0:8])
                    self.calibration_requested = False
                    self.on_calibrated()
                    #Checks if data is 8byte extension data.      
            elif input_type == ord(EXTENSION_8BYTES):
                self.check_button(b2i(data[2:4]))  # Check button press
                self.on_mass(self.get_mass(data[4:12])) # Goes to on_mass method in WiiboardSampling

    #Read battery level
    def on_status(self):  
        #logger.debug("Entered on_status method")
        self.reporting()  # Must set the reporting type after every status report
        logger.info("Status: battery: %.2f%% light: %s", self.battery * 100.0,
                    'on' if self.light_state else 'off')
        self.light(1)

    #Print factory calibration
    def on_calibrated(self):
        #logger.debug("Entered on_calibrated method")
        logger.info("Board calibrated: %s", str(self.calibration))
        print("\n \n MEASUREMENT IN PROGRESS")
        self.light(1)
    
    #Doesnt enter on_mass method???????
    def on_mass(self, mass):
        #logger.debug("Entered on_mass method")
        logger.debug("New mass data: %s", str(mass))

    #Indicate button is pressed
    def on_pressed(self):
        #logger.debug("Entered on_pressed method")
        logger.info("Button pressed")

    #Indicate button is released
    def on_released(self):
        #logger.debug("Entered on_released method")
        logger.info("Button released")


    #Shutdown
    def close(self):
        csv_file.close()
        #logger.debug("Entered close method")
        self.running = False
        if self.receivesocket:
            self.receivesocket.close()
        if self.controlsocket:
            self.controlsocket.close()

    def __del__(self):
        #logger.debug("Entered __del__ method")
        self.close()

    #### with statement ####
    def __enter__(self):
        #logger.debug("Entered __enter__ method")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        #logger.debug("Entered __exit__ method")
        self.close()
        return not exc_type  # re-raise exception if any





# Processing Nsamples

class WiiboardSampling(Wiiboard):
    def __init__(self, address=None, nsamples=N_SAMPLES):
        #logger.debug("Entered __initsample__ method")
        super().__init__(address)
        #Storing most recent samples up to nsamples
        self.samples = collections.deque([], nsamples)
        #logger.debug("Exited __initsample__ method")

    def on_mass(self, mass):
        #logger.debug("Entered on_masssample method")
        self.samples.append(mass)
        self.on_sample()

    def on_sample(self):
        logger.debug("Entered on_samplesample method")
        #time.sleep(0.01)






# Print sample data


class WiiboardPrint(WiiboardSampling):
    def __init__(self, address=None, nsamples=N_SAMPLES):
        #logger.debug("Entered _init__print method")
        super().__init__(address, nsamples)
        self.nloop = 0
        #logger.debug("Exited _init__print method")

#Print sample data
    def on_sample(self):
        #logger.debug("Entered on_sampleprint method")
        #If Nsamples are reached, print the time and average mass
        if len(self.samples) == N_SAMPLES:
            samples = [sum(sample.values()) for sample in self.samples]
            print(f"\n\n SESSION OF LENGTH {Minutes} MINUTES AND {Seconds} SECONDS HAS BEEN COMPLETED \n ")
            self.samples.clear() #Clear samples
            self.status() # Stop the board from publishing mass data
            self.nloop += 1 #Increment loop counter
            if self.nloop > N_LOOP:
                return self.close()
            self.light(0)
            #Wait T_SLEEP seconds before starting again
            time.sleep(T_SLEEP)





# Main


if __name__ == '__main__':
    import sys
    if '-d' in sys.argv:
        logger.setLevel(logging.DEBUG)
        sys.argv.remove('-d')
    if len(sys.argv) > 1:
        address = sys.argv[1]
    else:
        wiiboards = discover()
        logger.info("Found wiiboards: %s", str(wiiboards))
        if not wiiboards:
            raise Exception("Press the red sync button on the board")
        address = wiiboards[0]
    
    # Open CSV file
    current_datetime = datetime.now()
    date_string = current_datetime.strftime("%Y-%m-%d@%H-%M")
    filename = f"mass {date_string}.csv"
    file_path = os.path.join(directory, filename)
    with open(file_path, "w") as csv_file:
        csv_writer = csv.writer(csv_file)
        with WiiboardPrint(address) as wiiprint:
            wiiprint.csv_writer = csv_writer
            wiiprint.loop()
