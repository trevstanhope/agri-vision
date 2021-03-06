"""
Agri-Vision
Precision Agriculture and Soil Sensing Group (PASS)
McGill University, Department of Bioresource Engineering

IDEAS:
- Rotation compensation --> take Hough Line of plants to estimate row angle
"""

__author__ = 'Tsevor Stanhope'
__version__ = '2.01'

## Libraries
import cv2, cv
import serial
import pymongo
from bson import json_util
from pymongo import MongoClient
import json
import numpy as np
from matplotlib import pyplot as plt
import thread
import gps
import time 
import sys
from datetime import datetime
import ast
import os

## Constants
try:
    CONFIG_FILE = '%s' % sys.argv[1]
except Exception as err:
    settings = open('settings.cfg').read()
    CONFIG_FILE = settings.rstrip()

def pretty_print(task, msg, *args):
    date = datetime.strftime(datetime.now(), "%Y-%m-%d %H:%M:%S.%f")
    
    print "%s\t%s\t%s" % (date, task, msg)

## Class
class AgriVision:
    def __init__(self, config_file):

        # Load Config
        pretty_print("CONFIG", "Loading %s" % config_file)
        self.config = json.loads(open(config_file).read())
        for key in self.config:
            try:
                getattr(self, key)
            except AttributeError as error:
                setattr(self, key, self.config[key])
        
        # Initializers
        self.init_log() # it's best to run the log first to catch all events
        self.init_cameras()
        self.init_controller()
        self.init_pid()
        self.init_db()
        self.init_gps()
        if self.DISPLAY_ON: self.init_display()
        
    # Initialize Cameras
    def init_cameras(self):
        
        # Setting variables
        pretty_print('CAM', 'Initializing CV Variables')
        if self.CAMERA_ROTATED:
            self.CAMERA_HEIGHT, self.CAMERA_WIDTH = self.CAMERA_WIDTH, self.CAMERA_HEIGHT # flip dimensions if rotated
        self.CAMERA_CENTER = self.CAMERA_WIDTH / 2
        if self.VERBOSE:
            pretty_print('CAM', 'Camera Width: %d px' % self.CAMERA_WIDTH)
            pretty_print('CAM', 'Camera Height: %d px' % self.CAMERA_HEIGHT)
            pretty_print('CAM', 'Camera Center: %d px' % self.CAMERA_CENTER)
            pretty_print('CAM', 'Camera Depth: %d cm' % self.CAMERA_DEPTH)
            pretty_print('CAM', 'Camera FOV: %f rad' % self.CAMERA_FOV)
        if self.VERBOSE: 
            pretty_print('INIT', 'Image Center: %d px' % self.CAMERA_CENTER)
        self.GROUND_WIDTH = 2 * self.CAMERA_DEPTH * np.tan(self.CAMERA_FOV / 2.0)
        pretty_print('CAM', 'Ground Width: %d cm' % self.GROUND_WIDTH)
        pretty_print('CAM', 'Error Tolerance: +/- %d cm' % self.ERROR_TOLERANCE)
        self.PIXEL_PER_CM = self.CAMERA_WIDTH / self.GROUND_WIDTH
        pretty_print('CAM', 'Pixel-per-cm: %d px/cm' % self.PIXEL_PER_CM)
        self.PIXEL_RANGE = int(self.PIXEL_PER_CM * self.ERROR_TOLERANCE) 
        pretty_print('CAM', 'Pixel Range: +/- %d px' % self.PIXEL_RANGE)
        self.PIXEL_MIN = self.CAMERA_CENTER - self.PIXEL_RANGE
        self.PIXEL_MAX = self.CAMERA_CENTER + self.PIXEL_RANGE 
        
        # Set Thresholds     
        self.threshold_min = np.array([self.HUE_MIN, self.SAT_MIN, self.VAL_MIN], np.uint8)
        self.threshold_max = np.array([self.HUE_MAX, self.SAT_MAX, self.VAL_MAX], np.uint8)
        
        # Attempt to set each camera index/name
        pretty_print('CAM', 'Initializing Cameras')
        self.cameras = []
        self.images = []
        for i in range(self.CAMERAS):
            try:
                if self.VERBOSE: pretty_print('CAM', 'Attaching Camera #%d' % i)
                cam = cv2.VideoCapture(i)
		cam.set(cv.CV_CAP_PROP_SATURATION, self.CAMERA_SATURATION)
		cam.set(cv.CV_CAP_PROP_BRIGHTNESS, self.CAMERA_BRIGHTNESS)
		cam.set(cv.CV_CAP_PROP_CONTRAST, self.CAMERA_CONTRAST)
		cam.set(cv.CV_CAP_PROP_FPS, self.CAMERA_FPS)
                if not self.CAMERA_ROTATED:
                    cam.set(cv.CV_CAP_PROP_FRAME_WIDTH, self.CAMERA_WIDTH)
                    cam.set(cv.CV_CAP_PROP_FRAME_HEIGHT, self.CAMERA_HEIGHT)
                else:
                    cam.set(cv.CV_CAP_PROP_FRAME_WIDTH, self.CAMERA_HEIGHT)
                    cam.set(cv.CV_CAP_PROP_FRAME_HEIGHT, self.CAMERA_WIDTH)
                self.cameras.append(cam)
		self.images.append(np.zeros((self.CAMERA_HEIGHT, self.CAMERA_WIDTH, 3), np.uint8))
                if self.VERBOSE: pretty_print('CAM', 'Camera #%d OK' % i)
            except Exception as error:
                pretty_print('CAM', 'ERROR: %s' % str(error))
    
    # Initialize Database
    def init_db(self):
        self.LOG_NAME = datetime.strftime(datetime.now(), self.LOG_FORMAT)
        self.MONGO_NAME = datetime.strftime(datetime.now(), self.MONGO_FORMAT)
        if self.VERBOSE: pretty_print('DB', 'Initializing MongoDB')
        if self.VERBOSE: pretty_print('DB', 'Connecting to MongoDB: %s' % self.MONGO_NAME)
        if self.VERBOSE: pretty_print('DB', 'New session: %s' % self.LOG_NAME)

        try:
            self.client = MongoClient()
            self.database = self.client[self.MONGO_NAME]
            self.collection = self.database[self.LOG_NAME]
            if self.VERBOSE: pretty_print('DB', 'Setup OK')
        except Exception as error:
            pretty_print('DB', 'ERROR: %s' % str(error))
    
    # Initialize PID Controller
    def init_pid(self):
        if self.VERBOSE: pretty_print('PID', 'Initialing Electro-Hydraulics')
        if self.VERBOSE: pretty_print('PID', 'PWM Minimum: %d' % self.PWM_MIN)
        if self.VERBOSE: pretty_print('PID', 'PWM Maximum: %d' % self.PWM_MAX)
        self.CENTER_PWM = int(self.PWM_MIN + self.PWM_MAX / 2.0)
        if self.VERBOSE: pretty_print('PID', 'PWM Center: %d' % self.CENTER_PWM)
        try:
            if self.VERBOSE: pretty_print('PID', 'Default Number of Averages: %d' % self.NUM_AVERAGES)
            self.offset_history = [self.CAMERA_CENTER] * self.NUM_AVERAGES
            if self.VERBOSE: pretty_print('PID', 'Setup OK')
        except Exception as error:
            pretty_print('PID', 'ERROR: %s' % str(error))
        self.average = 0
        self.estimated = 0
        self.pwm = 0
    
    # Initialize Log
    def init_log(self):
        if self.VERBOSE: pretty_print('LOG', 'Initializing Log')
        self.LOG_NAME = datetime.strftime(datetime.now(), self.LOG_FORMAT)
        if self.VERBOSE: pretty_print('LOG', 'New log file: %s' % self.LOG_NAME)
        try:
            self.log = open('logs/' + self.LOG_NAME + '.csv', 'w')
            self.log.write(','.join(['time', 'lat', 'long', 'speed', 'cam0', 'cam1', 'estimate', 'average', 'pwm','\n']))
            if self.VERBOSE: pretty_print('LOG', 'Setup OK')
        except Exception as error:
            pretty_print('ERROR', str(error))
            
    # Initialize Controller
    def init_controller(self):
        if self.VERBOSE: pretty_print('CTRL', 'Initializing controller ...')
        try:
            if self.VERBOSE: pretty_print('CTRL', 'Device: %s' % str(self.SERIAL_DEVICE))
            if self.VERBOSE: pretty_print('CTRL', 'Baud Rate: %s' % str(self.SERIAL_BAUD))
            self.controller = serial.Serial(self.SERIAL_DEVICE, self.SERIAL_BAUD)
            pretty_print('CTRL', 'Setup OK')
        except Exception as error:
            pretty_print('CTRL', 'ERROR: %s' % str(error))
        
    # Initialize GPS
    def init_gps(self):
        if self.VERBOSE: pretty_print('GPS', 'Initializing GPS ...')
        self.latitude = 0
        self.longitude = 0
        self.speed = 0
        try:
            if self.VERBOSE: pretty_print('GPS', 'Enabing GPS ...')
            self.gpsd = gps.gps()
            self.gpsd.stream(gps.WATCH_ENABLE)
            thread.start_new_thread(self.update_gps, ())
        except Exception as err:
            pretty_print('GPS', 'WARNING: GPS not available! %s' % str(err))
    
    # Display
    def init_display(self):
        if self.VERBOSE: pretty_print('INIT', 'Initializing Display')
        try:
            self.updating = False
            if self.DISPLAY_ON:
                thread.start_new_thread(self.update_display, ())
        except Exception as error:
            pretty_print('DISP', 'ERROR: %s' % str(error))

    ## Rotate image
    def rotate_image(self, bgr):
        bgr = cv2.transpose(bgr)
        return bgr

    def capture_images(self):
        a = time.time()
        pretty_print('CAM', 'Capturing Images ...')
        images = []
        for i in range(self.CAMERAS):
            pretty_print('CAM', 'Attempting on Camera #%d' % i)
            try:
                (s, bgr) = self.cameras[i].read()
                if s and (self.images[i] is not None):
                    if self.CAMERA_ROTATED: bgr = self.rotate_image(bgr)
                    if np.all(bgr==self.images[i]):
                        images.append(None)
                        pretty_print('CAM', 'ERROR: Frozen frame')
                    else:
                        pretty_print('CAM', 'Capture successful: %s' % str(bgr.shape))
                        images.append(bgr)
                else:
                    pretty_print('CAM', 'ERROR: Capture failed')
                    self.images[i] = np.zeros((self.CAMERA_HEIGHT, self.CAMERA_WIDTH, 3), np.uint8)
            except KeyboardInterrupt:
                raise KeyboardInterrupt
            except:
                images.append(None)
        b = time.time()
        pretty_print('CAM', '... %.2f ms' % ((b - a) * 1000))
        return images

        
    ## Plant Segmentation Filter
    """
    1. RBG --> HSV
    2. Set minimum saturation equal to the mean saturation
    3. Set minimum value equal to the mean value
    4. Take hues within range from green-yellow to green-blue
    """
    def plant_filter(self, images):
        pretty_print('BPPD', 'Filtering for plants ...')
        a = time.time()
        masks = []
        for bgr in images:
            if bgr is not None:
                try:
                    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
                    self.threshold_min[1] = np.percentile(hsv[:,:,1], 100 * self.SAT_MIN / 255.0) # overwrite the saturation minima
                    self.threshold_min[2] = np.percentile(hsv[:,:,2], 100 * self.VAL_MIN / 255.0) # overwrite the value minima
		    self.threshold_max[1] = 255
		    self.threshold_max[2] = np.percentile(hsv[:,:,2], 100 * self.VAL_MAX / 255.0)
                    mask = cv2.inRange(hsv, self.threshold_min, self.threshold_max)
                    masks.append(mask)
                    if self.VERBOSE: pretty_print('BPPD', 'Mask Number #%d was successful' % len(masks))                    
                except Exception as error:
                    pretty_print('BPPD', str(error))
            else:
                if self.VERBOSE: pretty_print('BPPD', 'Mask Number #%d is blank' % len(masks))
                masks.append(None)
        b = time.time()
        if self.VERBOSE: pretty_print('BPPD', '... %.2f ms' % ((b - a) * 1000))
        return masks
        
    ## Find Plants
    """
    1. Calculates the column summation of the mask
    2. Calculates the 95th percentile threshold of the column sum array
    3. Finds indicies which are greater than or equal to the threshold
    4. Finds the median of this array of indices
    5. Repeat for each mask
    """
    def find_offset(self, masks):
        a = time.time()
        offsets = []
	sums = []
        for mask in masks:
            if mask is not None:
                try:
                    column_sum = mask.sum(axis=0) # vertical summation
                    threshold = np.percentile(column_sum, self.THRESHOLD_PERCENTILE)
                    probable = np.nonzero(column_sum >= threshold) # returns 1 length tuble
                    if self.DEBUG:
                        fig = plt.figure()
                        plt.plot(range(self.CAMERA_WIDTH), column_sum)
                        plt.show()
                        time.sleep(0.1)
                        plt.close(fig)
                    num_probable = len(probable[0])
		    if (num_probable == self.CAMERA_WIDTH * self.THRESHOLD_PERCENTILE / 100.0):
			print "WARNING"
			time.sleep(1)
                    best = int(np.median(probable[0]))
		    sum = column_sum[best]
		    centroid = best - self.CAMERA_CENTER
                    offsets.append(centroid)
		    sums.append(sum)
                except Exception as error:
                    pretty_print('OFF', '%s' % str(error))
        if self.VERBOSE: pretty_print('OFF', 'Detected offsets: %s' % str(offsets))
        b = time.time()
        if self.VERBOSE: pretty_print('OFF', '... %.2f ms' % ((b - a) * 1000))
        return offsets, sums
        
    ## Best Guess for row based on multiple offsets from indices
    """
    1. If outside bounds, default to edges
    2. If inside, use mean of detected indices from both cameras
    1. Takes the current assumed offset and number of averages
    2. Calculate weights of previous offset
    3. Estimate the weighted position of the crop row (in pixels)
    """
    def estimate_row(self, indices, sums):
        a = time.time()
        if self.VERBOSE: pretty_print('ROW', 'Smoothing offset estimation ...')
        try:
	    indices = np.array(indices)
	    sums = np.array(sums)
            est =  indices[np.argmax(sums)]
        except Exception as error:
            pretty_print('ROW', 'ERROR: %s' % str(error))
            est = self.CAMERA_CENTER
        self.offset_history.append(est)
        while len(self.offset_history) > self.NUM_AVERAGES:
            self.offset_history.pop(0)
        avg = int(np.mean(self.offset_history)) #!TODO
        diff = est - avg #!TODO can be a little more clever e.g. np.gradient, np.convolve
        if self.VERBOSE:
            pretty_print('ROW', 'Est = %.2f' % est)
            pretty_print('ROW', 'Avg = %.2f' % avg)
            pretty_print('ROW', 'Diff.= %.2f' % diff)
        b = time.time()
        if self.VERBOSE: pretty_print('ROW', '... %.2f ms' % ((b - a) * 1000))
        return est, avg, diff
         
    ## Control Hydraulics
    """
    Calculates the PID output for the PWM controller
    Arguments: est, avg, diff
    Requires: PWM_MAX, PWM_MIN, CENTER_PWM
    Returns: PWM
    """
    def calculate_output(self, estimate, average, diff):
        a = time.time()
        if self.VERBOSE: pretty_print('PID', 'Calculating PID Output ...')
        try:
            p = estimate * self.P_COEF
            i = average * self.I_COEF
            d = diff  * self.D_COEF
            if self.VERBOSE: pretty_print('PID', "P = %.1f" % p)
            if self.VERBOSE: pretty_print('PID', "I = %.1f" % i)
            if self.VERBOSE: pretty_print('PID', "D = %.1f" % d)            
            pwm = int(p + i + d + self.CENTER_PWM) # offset to zero
            if pwm > self.PWM_MAX: pwm = self.PWM_MAX
            elif pwm < self.PWM_MIN: pwm = self.PWM_MIN
            volts = round((pwm * (self.MAX_VOLTAGE - self.MIN_VOLTAGE) / (self.PWM_MAX - self.PWM_MIN) + self.MIN_VOLTAGE), 2)
            if pwm > self.PWM_MAX:
                pwm = self.PWM_MAX
            elif pwm < self.PWM_MIN:
                pwm = self.PWM_MIN
            if self.VERBOSE: pretty_print('PID', 'PWM = %d (%.2f V)' % (pwm, volts))
        except Exception as error:
            pretty_print('PID', 'ERROR: %s' % str(error))
            pwm = self.CENTER_PWM
        b = time.time()
        if self.VERBOSE: pretty_print('PID', '... %.2f ms' % ((b - a) * 1000))
        return pwm, volts

    ## Control Hydraulics
    """
    1. Get PWM response corresponding to average offset
    2. Send PWM response over serial to controller
    """
    def set_controller(self, pwm):
        a = time.time()
        if self.VERBOSE: pretty_print('CTRL', 'Setting controller state ...')
        try:            
            try:
                assert self.controller is not None
                self.controller.write(str(pwm) + '\n') # Write to PWM adaptor
                if self.VERBOSE: pretty_print('CTRL', 'Wrote successfully')
	    except Exception as error:
                pretty_print('CTRL', 'ERROR: %s' % str(error))
        except Exception as error:
            pretty_print('CTRL', 'ERROR: %s' % str(error))
        b = time.time()
        if self.VERBOSE: pretty_print('CTRL', '... %.2f ms' % ((b - a) * 1000))
    
    ## Log to Mongo
    """
    1. Log results to the database
    2. Returns Doc ID
    """
    def log_db(self, sample):
        if self.VERBOSE: pretty_print('DB', 'Logging to Database ...')
        try:          
            assert self.collection is not None
            doc_id = self.collection.insert(sample)
            if self.VERBOSE: pretty_print('DB', 'Doc ID: %s' % str(doc_id))
        except Exception as error:
            pretty_print('DB', 'ERROR: %s' % str(error))
        return doc_id
    
    ## Log to File
    """
    1. Open new text file
    2. For each document in session, print parameters to file
    """
    def log_file(self, sample):
        if self.VERBOSE: pretty_print('LOG', 'Logging to File')
        try:
            assert self.log is not None
            time = str(sample['time'])
            latitude = str(sample['lat'])
            longitude = str(sample['long'])
            speed = str(sample['speed'])
            estimate = str(sample['estimate'])
            average = str(sample['average'])
            pwm = str(sample['pwm'])
            self.log.write(','.join([time, latitude, longitude, speed, estimate, average, pwm,'\n']))
        except Exception as error:
            pretty_print('LOG', 'ERROR: %s' % str(error))
                
    ## Update the Display
    """
    0. Check for concurrent update process
    1. Draw lines on RGB images
    2. Draw lines on ABP masks
    3. Output GUI display
    """
    def update_display(self):
        a = time.time()
        if self.updating:
            return # if the display is already updating, wait and exit (puts less harm on the CPU)
        else:
            self.updating = True
            if self.VERBOSE: pretty_print('DISP', 'Displaying Images ...')
            try:
                pwm = self.pwm
                average = self.average + self.CAMERA_CENTER
                estimated = self.estimated  + self.CAMERA_CENTER
                masks = self.masks
                images = self.images
                volts = self.volts
                output_images = []
                distance = round((average - self.CAMERA_CENTER) / float(self.PIXEL_PER_CM), 1)
                if self.VERBOSE: pretty_print('DISP', 'Offset Distance: %d' % distance)
                for i in xrange(self.CAMERAS):
                    try:
                        if self.VERBOSE: pretty_print('DISP', 'Image #%d' % (i+1))
                        img = images[i]
                        mask = masks[i]
                        if img is None: img = np.zeros((self.CAMERA_HEIGHT, self.CAMERA_WIDTH, 3), np.uint8)
                        if mask is None: mask = np.zeros((self.CAMERA_HEIGHT, self.CAMERA_WIDTH), np.uint8)
                        (h, w, d) = img.shape
                        if self.VERBOSE: pretty_print('DISP', 'Mask shape: %s' % str(mask.shape))
                        if self.VERBOSE: pretty_print('DISP', 'Img shape: %s' % str(img.shape))
                        if self.HIGHLIGHT:
                            img = np.dstack((mask, mask, mask))
                            img[:, self.PIXEL_MIN, 2] =  255
                            img[:, self.PIXEL_MAX, 2] =  255
                            img[:, self.CAMERA_CENTER, 1] =  255
                            img[:, average, 0] = 255
                            if self.VERBOSE: pretty_print('DISP', 'Highlighted detected plants')
                        else:
                            cv2.line(img, (self.PIXEL_MIN, 0), (self.PIXEL_MIN, self.CAMERA_HEIGHT), (0,0,255), 1)
                            cv2.line(img, (self.PIXEL_MAX, 0), (self.PIXEL_MAX, self.CAMERA_HEIGHT), (0,0,255), 1)
                            cv2.line(img, (average, 0), (average, self.CAMERA_HEIGHT), (0,255,0), 2)
                            cv2.line(img, (self.CAMERA_CENTER, 0), (self.CAMERA_CENTER, self.CAMERA_HEIGHT), (255,255,255), 1)
                        output_images.append(img)
                    except Exception as error:
                        pretty_print('DISP', 'ERROR: %s' % str(error))
                if self.VERBOSE: pretty_print('DISP', 'Stacking images ...')
                output_small = np.hstack(output_images)
                pad = np.zeros((self.CAMERA_HEIGHT * 0.1, self.CAMERAS * self.CAMERA_WIDTH, 3), np.uint8) # add blank space
                output_padded = np.vstack([output_small, pad])
                if self.VERBOSE: pretty_print('DISP', 'Padded image')
                output_large = cv2.resize(output_padded, (self.DISPLAY_WIDTH, self.DISPLAY_HEIGHT))

                # Offset Distance
                if average - self.CAMERA_CENTER >= 0:
                    distance_str = str("+%2.1f cm" % distance)
                elif average - self.CAMERA_CENTER< 0:
                    distance_str = str("%2.1f cm" % distance)
                cv2.putText(output_large, distance_str, (int(self.DISPLAY_WIDTH * 0.01), int(self.DISPLAY_WIDTH * 0.74)), cv2.FONT_HERSHEY_SIMPLEX, 2, (255,255,255), 4)
                
                # Output Voltage
                volts_str = str("%2.1f V" % volts)
                cv2.putText(output_large, volts_str, (int(self.DISPLAY_WIDTH * 0.82), int(self.DISPLAY_WIDTH * 0.74)), cv2.FONT_HERSHEY_SIMPLEX, 2, (255,255,255), 4)
                
                # Arrow
                if average - self.CAMERA_CENTER >= 0:
                    p = (int(self.DISPLAY_WIDTH * 0.45), int(self.DISPLAY_WIDTH * 0.72))
                    q = (int(self.DISPLAY_WIDTH * 0.55), int(self.DISPLAY_WIDTH * 0.72))
                elif average - self.CAMERA_CENTER< 0:
                    p = (int(self.DISPLAY_WIDTH * 0.55), int(self.DISPLAY_WIDTH * 0.72))
                    q = (int(self.DISPLAY_WIDTH * 0.45), int(self.DISPLAY_WIDTH * 0.72))
                color = (255,255,255)
                thickness = 8
                line_type = 8
                shift = 0
                arrow_magnitude=20
                cv2.line(output_large, p, q, color, thickness, line_type, shift) # draw arrow tail
                angle = np.arctan2(p[1]-q[1], p[0]-q[0])
                p = (int(q[0] + arrow_magnitude * np.cos(angle + np.pi/4)), # starting point of first line of arrow head 
                int(q[1] + arrow_magnitude * np.sin(angle + np.pi/4)))
                cv2.line(output_large, p, q, color, thickness, line_type, shift) # draw first half of arrow head
                p = (int(q[0] + arrow_magnitude * np.cos(angle - np.pi/4)), # starting point of second line of arrow head 
                int(q[1] + arrow_magnitude * np.sin(angle - np.pi/4)))
                cv2.line(output_large, p, q, color, thickness, line_type, shift) # draw second half of arrow head
                
                # Draw GUI
                cv2.namedWindow('Agri-Vision', cv2.WINDOW_NORMAL)
                if self.FULLSCREEN: cv2.setWindowProperty('Agri-Vision', cv2.WND_PROP_FULLSCREEN, cv2.cv.CV_WINDOW_FULLSCREEN)
                if self.VERBOSE: pretty_print('DISP', 'Output shape: %s' % str(output_large.shape))
                cv2.imshow('Agri-Vision', output_large)
                if cv2.waitKey(5) == 0:
                    pass
            except Exception as error:
                pretty_print('DISP', str(error))
            self.updating = False
        b = time.time()
        if self.VERBOSE: pretty_print('DISP', '... %.2f ms' % ((b - a) * 1000))
                    
    ## Update GPS
    """
    1. Get the most recent GPS data
    2. Set global variables for lat, long and speed
    """
    def update_gps(self):  
        while True:
            time.sleep(1) # GPS update time
            self.gpsd.next()
            self.latitude = self.gpsd.fix.latitude
            self.longitude = self.gpsd.fix.longitude
            self.speed = self.gpsd.fix.speed
            pretty_print('GPS', '%d N %d E' % (self.latitude, self.longitude))
    
    ## Close
    """
    Function to shutdown application safely
    1. Close windows
    2. Disable controller
    3. Release capture interfaces 
    """
    def close(self):
        if self.VERBOSE: pretty_print('SYSTEM', 'Shutting Down ...')
        time.sleep(1)
        try:
            if self.VERBOSE: pretty_print('CTRL', 'Closing Controller ...')
            self.controller.close() ## Disable controller
            time.sleep(0.5)
        except Exception as error:
            pretty_print('CTRL', 'ERROR: %s' % str(error))
        for i in range(len(self.cameras)):
            try:
                if self.VERBOSE: pretty_print('CAM', 'Closing Camera #%d ...' % i)
                self.cameras[i].release() ## Disable cameras
                time.sleep(0.5)
            except Exception as error:
                pretty_print('CAM', 'ERROR: %s' % str(error))
        cv2.destroyAllWindows() ## Close windows
        
    ## Run  
    """
    Function for Run-time loop
    1. Get initial time
    2. Capture images
    3. Generate mask filter for plant matter
    4. Calculate indices of rows
    5. Estimate row from both images
    6. Get number of averages
    7. Calculate moving average
    8. Send PWM response to controller
    9. Throttle to desired frequency
    10. Log results to DB
    11. Display results
    """     
    def run(self):
        while True:
            try:
                images = self.capture_images()
                masks = self.plant_filter(images)
                offsets, sums = self.find_offset(masks)
                (est, avg, diff) = self.estimate_row(offsets, sums)
                pwm, volts = self.calculate_output(est, avg, diff)
                err = self.set_controller(pwm)
                sample = {
                    'offsets' : offsets, 
                    'estimated' : est,
                    'average' : avg,
                    'differential' : diff,
                    'pwm': pwm,
                    'time' : datetime.strftime(datetime.now(), self.TIME_FORMAT),
                    'long' : self.longitude,
                    'lat' : self.latitude,
                    'speed' : self.speed,
                }
                self.pwm = pwm
                self.images = images
                self.masks = masks
                self.average = avg
                self.estimated = est
                self.volts = volts
                if self.MONGO_ON: doc_id = self.log_db(sample)
                if self.LOGFILE_ON: self.log_file(sample)
                if self.DISPLAY_ON:
                    try:
                        os.environ['DISPLAY']
                        thread.start_new_thread(self.update_display, ())
                    except Exception as error:
                        pretty_print('SYS', 'ERROR: %s' % str(error))
            except KeyboardInterrupt as error:
                self.close()    
                break
            except UnboundLocalError as error:
                pass

## Main
if __name__ == '__main__':
    session = AgriVision(CONFIG_FILE)
    session.run()
