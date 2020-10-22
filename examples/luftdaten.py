#!/usr/bin/env python3

import requests
import ST7735
import time
from bme280 import BME280
from pms5003 import PMS5003, ReadTimeoutError
from subprocess import PIPE, Popen, check_output
from PIL import Image, ImageDraw, ImageFont
from fonts.ttf import RobotoMedium as UserFont
from datetime import date

def get_filename_datetime():
    # Use current date to get a text file name.
    return "file-" + str(date.today()) + ".txt"

try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559
    ltr559 = LTR559()
except ImportError:
    import ltr559
from enviroplus import gas
from enviroplus.noise import Noise

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus

import json

gas.enable_adc()
gas.set_adc_gain(4.096)

print("""luftdaten.py - Reads temperature, pressure, humidity,
PM2.5, and PM10 from Enviro plus and sends data to Luftdaten,
the citizen science air quality project.

Note: you'll need to register with Luftdaten at:
https://meine.luftdaten.info/ and enter your Raspberry Pi
serial number that's displayed on the Enviro plus LCD along
with the other details before the data appears on the
Luftdaten map.

Press Ctrl+C to exit!

""")

bus = SMBus(1)

# Create BME280 instance
bme280 = BME280(i2c_dev=bus)

# Create LCD instance
disp = ST7735.ST7735(
    port=0,
    cs=1,
    dc=9,
    backlight=12,
    rotation=270,
    spi_speed_hz=10000000
)

# Initialize display
disp.begin()

# Create PMS5003 instance
pms5003 = PMS5003()


# Read values from BME280 and PMS5003 and return as dict
def read_values():
    values = {}
    cpu_temp = get_cpu_temperature()
    raw_temp = bme280.get_temperature()
    comp_temp = raw_temp - ((cpu_temp - raw_temp) / comp_factor)
    lux = ltr559.get_lux()
    proximity = ltr559.get_proximity()
    readings = gas.read_all()
    oxidising = readings.oxidising
    nh3 = readings.nh3
    reducing = readings.reducing
    adc = readings.adc
    noise = Noise()
    amps = noise.get_amplitudes_at_frequency_ranges([
        (100, 200),
        (500, 600),
        (1000, 1200)
    ])
    amps = [n * 32 for n in amps]
    
    values["temperature"] = "{:.2f}".format(comp_temp)
    values["cpu_temp"] = "{:.2f}".format(cpu_temp)
    values["pressure"] = "{:.2f}".format(bme280.get_pressure() * 100)
    values["humidity"] = "{:.2f}".format(bme280.get_humidity())
    values["lux"] = "{:05.02f}".format(lux)
    values["proximity"] = "{:05.02f}".format(proximity)
    values["nh3"] = "{:05.02f}".format(nh3)
    values["oxidising"] = "{:05.02f}".format(oxidising)
    values["reducing"] = "{:05.02f}".format(reducing)
    values["adc"] = "{:05.02f}".format(adc)
    values["amp_100_200"] = "{:05.02f}".format(amps[0])
    values["amp_500_600"] = "{:05.02f}".format(amps[1])
    values["amp_1000_1200"] = "{:05.02f}".format(amps[2])
    try:
        pm_values = pms5003.read()
        values["P2"] = str(pm_values.pm_ug_per_m3(2.5))
        values["P1"] = str(pm_values.pm_ug_per_m3(10))
    except ReadTimeoutError:
        pms5003.reset()
        pm_values = pms5003.read()
        values["P2"] = str(pm_values.pm_ug_per_m3(2.5))
        values["P1"] = str(pm_values.pm_ug_per_m3(10))
    return values


# Get CPU temperature to use for compensation
def get_cpu_temperature():
    process = Popen(['vcgencmd', 'measure_temp'], stdout=PIPE, universal_newlines=True)
    output, _error = process.communicate()
    return float(output[output.index('=') + 1:output.rindex("'")])


# Get Raspberry Pi serial number to use as ID
def get_serial_number():
    with open('/proc/cpuinfo', 'r') as f:
        for line in f:
            if line[0:6] == 'Serial':
                return line.split(":")[1].strip()


# Check for Wi-Fi connection
def check_wifi():
    if check_output(['hostname', '-I']):
        return True
    else:
        return False


# Display Raspberry Pi serial and Wi-Fi status on LCD
def display_status():
    wifi_status = "connected" if check_wifi() else "disconnected"
    text_colour = (255, 255, 255)
    back_colour = (0, 170, 170) if check_wifi() else (85, 15, 15)
    id = get_serial_number()
    message = "{}\nWi-Fi: {}".format(id, wifi_status)
    img = Image.new('RGB', (WIDTH, HEIGHT), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    size_x, size_y = draw.textsize(message, font)
    x = (WIDTH - size_x) / 2
    y = (HEIGHT / 2) - (size_y / 2)
    draw.rectangle((0, 0, 160, 80), back_colour)
    draw.text((x, y), message, font=font, fill=text_colour)
    disp.display(img)

def log_values(values):
    json_data = json.dumps(values)
    fname = get_filename_datetime()
    path = "/home/pi/enviroplus-python/logs/" + fname
    with open(path, "a+") as f:
        # Write data to file.
        f.write(json_data)
        f.write("\n")

def send_to_luftdaten(values, id):
    pm_values = dict(i for i in values.items() if i[0].startswith("P"))
    temp_values = {'pressure': values["pressure"], 'temperature' : values['temperature'], 'humidity' : values['humidity']}
    pm_values_json = [{"value_type": key, "value": val} for key, val in pm_values.items()]
    temp_values_json = [{"value_type": key, "value": val} for key, val in temp_values.items()]
    try:
        resp_1 = requests.post(
            "https://api.luftdaten.info/v1/push-sensor-data/",
            json={
                "software_version": "enviro-plus 0.0.1",
                "sensordatavalues": pm_values_json
            },
            headers={
                "X-PIN": "1",
                "X-Sensor": id,
                "Content-Type": "application/json",
                "cache-control": "no-cache"
            },
            timeout = 30
        )

        resp_2 = requests.post(
            "https://api.luftdaten.info/v1/push-sensor-data/",
            json={
                "software_version": "enviro-plus 0.0.1",
                "sensordatavalues": temp_values_json
            },
            headers={
                "X-PIN": "11",
                "X-Sensor": id,
                "Content-Type": "application/json",
                "cache-control": "no-cache"
            },
            timeout = 30
        )

        if resp_1.ok and resp_2.ok:
            return True
        else:
            return False
    except:
        return False


# Compensation factor for temperature
comp_factor = 2.25

# Raspberry Pi ID to send to Luftdaten
id = "raspi-" + get_serial_number()

# Width and height to calculate text position
WIDTH = disp.width
HEIGHT = disp.height

# Text settings
font_size = 16
font = ImageFont.truetype(UserFont, font_size)

# Display Raspberry Pi serial and Wi-Fi status
print("Raspberry Pi serial: {}".format(get_serial_number()))
print("Wi-Fi: {}\n".format("connected" if check_wifi() else "disconnected"))

time_since_update = 0
update_time = time.time()

# Main loop to read data, display, and send to Luftdaten
while True:
    try:
        time_since_update = time.time() - update_time
        values = read_values()
        log_values(values)
        # print(values)
        if time_since_update > 120 and check_wifi() :
            resp = send_to_luftdaten(values, id)
            update_time = time.time()
            print("Response: {}\n".format("ok luftdaten" if resp else "failed luftdaten"))
        # display_status()
    except Exception as e:
        print(e)
