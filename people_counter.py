from tracker.centroidtracker import CentroidTracker
from tracker.trackableobject import TrackableObject
from imutils.video import VideoStream
from itertools import zip_longest
from utils.api import Api
from utils.mailer import Mailer
from imutils.video import FPS
from utils import thread
import numpy as np
import argparse
import datetime
import schedule
import logging
import imutils
import time
import dlib
import json
import csv
import cv2

# execution start time
start_time = time.time()
# setup logger
logging.basicConfig(level=logging.INFO, format="[INFO] %(message)s")
logger = logging.getLogger(__name__)
# initiate features config.
with open("utils/config.json", "r") as file:
    config = json.load(file)


def parse_arguments():
    # function to parse the arguments
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--prototxt", required=False, default="detector/MobileNetSSD_deploy.prototxt",
                    help="path to Caffe 'deploy' prototxt file")
    ap.add_argument("-m", "--model", required=False, default="detector/MobileNetSSD_deploy.caffemodel",
                    help="path to Caffe pre-trained model")
    ap.add_argument("-i", "--input", type=str,
                    help="path to optional input video file")
    ap.add_argument("-o", "--output", type=str,
                    help="path to optional output video file")
    # confidence default 0.4
    ap.add_argument("-c", "--confidence", type=float, default=0.4,
                    help="minimum probability to filter weak detections")
    ap.add_argument("-s", "--skip-frames", type=int, default=30,
                    help="# of skip frames between detections")
    args = vars(ap.parse_args())
    return args


def send_mail():
    """ function to send the email alerts """
    Mailer().send(config["Email_Receive"])


def log_data(move_in, in_time, move_out, out_time):
    """ function to log the counting data """
    data = [move_in, in_time, move_out, out_time]
    # transpose the data to align the columns properly
    export_data = zip_longest(*data, fillvalue='')

    with open('utils/data/logs/counting_data.csv', 'w', newline='') as myfile:
        wr = csv.writer(myfile, quoting=csv.QUOTE_ALL)
        if myfile.tell() == 0:  # check if header rows are already existing
            wr.writerow(("Move In", "In Time", "Move Out", "Out Time"))
            wr.writerows(export_data)


def post_api(total, total_down, total_up, delta):
    """ function to post an API call """
    Api().post(total, total_down, total_up, delta)


def people_counter():
    """ main function for people_counter.py """
    args = parse_arguments()
    # initialize the list of class labels MobileNet SSD was trained to detect
    CLASSES = ["background", "aeroplane", "bicycle", "bird", "boat",
               "bottle", "bus", "car", "cat", "chair", "cow", "diningtable",
               "dog", "horse", "motorbike", "person", "pottedplant", "sheep",
               "sofa", "train", "tvmonitor"]

    # instantiate api_time as None, only initialize if API config is set to true
    api_time = None

    # load our serialized model from disk
    net = cv2.dnn.readNetFromCaffe(args["prototxt"], args["model"])

    # if a video path was not supplied, grab a reference to the ip camera
    if not args.get("input", False):
        logger.info("Starting the live stream..")
        vs = VideoStream(config["VideoStream_Url"]).start()
        time.sleep(2.0)

    # otherwise, grab a reference to the video file
    else:
        logger.info("Starting the video..")
        vs = cv2.VideoCapture(args["input"])

    # initialize the video writer (we'll instantiate later if need be)
    writer = None

    # initialize the frame dimensions (we'll set them as soon as we read
    # the first frame from the video)
    W = None
    H = None

    # instantiate our centroid tracker, then initialize a list to store
    # each of our dlib correlation trackers, followed by a dictionary to
    # map each unique object ID to a TrackableObject
    ct = CentroidTracker(maxDisappeared=40, maxDistance=50)
    trackers = []
    trackable_objects = {}

    # initialize the total number of frames processed thus far, along
    # with the total number of objects that have moved either up or down
    total_frames = 0
    total_down = 0
    total_up = 0
    delta = 0
    total = 0
    # initialize empty lists to store the counting data
    # move_out = []
    # move_in = []
    # out_time = []
    # in_time = []

    # start the frames per second throughput estimator
    fps = FPS().start()

    if config["Thread"]:
        vs = thread.ThreadingClass(config["VideoStream_Url"])
    if config["Api"]:
        # set api_time only if API config is true
        api_time = time.time()

    # loop over frames from the video stream
    while True:
        # grab the next frame and handle if we are reading from either
        # VideoCapture or VideoStream
        frame = vs.read()
        frame = frame[1] if args.get("input", False) else frame

        # if we are viewing a video, and we did not grab a frame then we
        # have reached the end of the video
        if args["input"] is not None and frame is None:
            break

        # resize the frame to have a maximum width of 500 pixels (the
        # fewer data we have, the faster we can process it), then convert
        # the frame from BGR to RGB for dlib
        frame = imutils.resize(frame, width=500)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # if the frame dimensions are empty, set them
        if W is None or H is None:
            (H, W) = frame.shape[:2]

        # if we are supposed to be writing a video to disk, initialize
        # the writer
        # if args["output"] is not None and writer is None:
        #     fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        #     writer = cv2.VideoWriter(args["output"], fourcc, 30,
        #                              (W, H), True)

        # initialize the current status along with our list of bounding
        # box rectangles returned by either (1) our object detector or
        # (2) the correlation trackers
        status = "Waiting"
        rects = []

        # check to see if we should run a more computationally expensive
        # object detection method to aid our tracker
        if total_frames % args["skip_frames"] == 0:
            # set the status and initialize our new set of object trackers
            status = "Detecting"
            trackers = []

            # convert the frame to a blob and pass the blob through the
            # network and obtain the detections
            blob = cv2.dnn.blobFromImage(frame, 0.007843, (W, H), 127.5)
            net.setInput(blob)
            detections = net.forward()

            # loop over the detections
            for i in np.arange(0, detections.shape[2]):
                # extract the confidence (i.e., probability) associated
                # with the prediction
                confidence = detections[0, 0, i, 2]

                # extract the index of the class label from the
                # detections list
                idx = int(detections[0, 0, i, 1])

                # filter out weak detections by requiring a minimum
                # confidence and if the class label is not a person, ignore it
                if confidence < args["confidence"] or CLASSES[idx] != "person":
                    continue

                # compute the (x, y)-coordinates of the bounding box for the
                # object
                box = detections[0, 0, i, 3:7] * np.array([W, H, W, H])
                (start_x, start_y, end_x, end_y) = box.astype("int")

                # construct a dlib rectangle object from the bounding
                # box coordinates and then start the dlib correlation
                # tracker
                tracker = dlib.correlation_tracker()
                rect = dlib.rectangle(start_x, start_y, end_x, end_y)
                tracker.start_track(rgb, rect)

                # add the tracker to our list of trackers so we can
                # utilize it during skip frames
                trackers.append(tracker)

        # otherwise, we should utilize our object *trackers* rather than
        # object *detectors* to obtain a higher frame processing throughput
        else:
            # loop over the trackers
            for tracker in trackers:
                # set the status of our system to be 'tracking' rather
                # than 'waiting' or 'detecting'
                status = "Tracking"

                # update the tracker and grab the updated position
                tracker.update(rgb)
                pos = tracker.get_position()

                # unpack the position object
                start_x = int(pos.left())
                start_y = int(pos.top())
                end_x = int(pos.right())
                end_y = int(pos.bottom())

                # add the bounding box coordinates to the rectangles list
                rects.append((start_x, start_y, end_x, end_y))

        # draw a horizontal line in the center of the frame -- once an
        # object crosses this line we will determine whether they were
        # moving 'up' or 'down'
        cv2.line(frame, (0, H // 2), (W, H // 2), (0, 0, 0), 1)
        # cv2.putText(frame, "-Prediction border - Entrance-", (10, H - ((i * 20) + 200)),
        #             cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # use the centroid tracker to associate the (1) old object
        # centroids with (2) the newly computed object centroids
        objects = ct.update(rects)

        # loop over the tracked objects
        for (objectID, centroid) in objects.items():
            # check to see if a trackable object exists for the current
            # object ID
            to = trackable_objects.get(objectID, None)

            # if there is no existing trackable object, create one
            if to is None:
                to = TrackableObject(objectID, centroid)
                to.initialPositionUp = centroid[1] < H // 2

            # otherwise, there is a trackable object, so we can utilize it
            # to determine direction
            else:
                # the difference between the y-coordinate of the *current*
                # centroid and the mean of *previous* centroids will tell
                # us in which direction the object is moving (negative for
                # 'up' and positive for 'down')
                y = [c[1] for c in to.centroids]
                direction = centroid[1] - np.mean(y)
                to.centroids.append(centroid)

                # if the direction is negative (indicating the object
                # is moving up) AND the centroid is above the center
                # line, count the object
                if direction < 0 and centroid[1] < H // 2 and not to.initialPositionUp:
                    total_up += 1
                    delta -= 1
                    date_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    # move_out.append(total_up)
                    # out_time.append(date_time)
                    logger.info(f"{date_time}: EXIT - count: {total_up}, delta: {delta}, dir: {direction}, H: {H}, "
                                f"centroid: {centroid[1]}, pos: {to.initialPositionUp}")
                    to.initialPositionUp = not to.initialPositionUp

                # if the direction is positive (indicating the object
                # is moving down) AND the centroid is below the
                # center line, count the object
                elif direction > 0 and centroid[1] > H // 2 and to.initialPositionUp:
                    total_down += 1
                    delta += 1
                    date_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    # move_in.append(total_down)
                    # in_time.append(date_time)
                    logger.info(f"{date_time}: ENTER - count: {total_down}, delta: {delta},  dir: {direction}, H: {H}, "
                                f"centroid: {centroid[1]}, pos: {to.initialPositionUp}")
                    to.initialPositionUp = not to.initialPositionUp

                # compute the sum of total people inside
                total = total_down - total_up
                # logger.info("Total people inside:", total)

            # store the trackable object in our dictionary
            trackable_objects[objectID] = to

            # draw both the ID of the object and the centroid of the
            # object on the output frame
            text = "ID {}".format(objectID)
            cv2.putText(frame, text, (centroid[0] - 10, centroid[1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            cv2.circle(frame, (centroid[0], centroid[1]), 4, (255, 255, 255), -1)

        # construct a tuple of information we will be displaying on the frame
        info_status = [
            ("Exit", total_up),
            ("Enter", total_down),
            ("Delta", delta),
        ]

        info_total = [
            ("Total people inside", total),
        ]

        # display the output
        for (i, (k, v)) in enumerate(info_status):
            text = "{}: {}".format(k, v)
            cv2.putText(frame, text, (10, H - ((i * 20) + 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

        for (i, (k, v)) in enumerate(info_total):
            text = "{}: {}".format(k, v)
            cv2.putText(frame, text, (265, H - ((i * 20) + 60)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # initiate a simple log to save the counting data
        # if config["Log"]:
        #     log_data(move_in, in_time, move_out, out_time)

        # Initiate an API call if set Api_Interval is exceeded
        if config["Api"]:
            now = time.time()
            num_seconds = (now - api_time)

            # if the API interval is exceeded, send an API POST request
            if num_seconds > config["Api_Interval"]:
                post_api(total, total_down, total_up, delta)
                # set api_time to current time to refresh the interval
                api_time = time.time()
                # reset delta counter
                delta = 0

        # check to see if we should write the frame to disk
        # if writer is not None:
        #     writer.write(frame)

        # show the output frame
        cv2.imshow("Real-Time Monitoring/Analysis Window", frame)
        key = cv2.waitKey(1) & 0xFF
        # if the `q` key was pressed, break from the loop
        if key == ord("q"):
            break
        # increment the total number of frames processed thus far and
        # then update the FPS counter
        total_frames += 1
        fps.update()

        # initiate the timer
        if config["Timer"]:
            # automatic timer to stop the live stream (set to 8 hours/28800s)
            end_time = time.time()
            num_seconds = (end_time - start_time)
            if num_seconds > 28800:
                break

    # stop the timer and display FPS information
    fps.stop()
    logger.info("Elapsed time: {:.2f}".format(fps.elapsed()))
    logger.info("Approx. FPS: {:.2f}".format(fps.fps()))

    # release the camera device/resource (issue 15)
    if config["Thread"]:
        vs.release()

    # close any open windows
    cv2.destroyAllWindows()


# initiate the scheduler
if config["Scheduler"]:
    # runs at every day (09:00 am)
    schedule.every().day.at("09:00").do(people_counter)
    while True:
        schedule.run_pending()
else:
    people_counter()
