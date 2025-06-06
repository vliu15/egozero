import base64
import pickle
import time
from threading import Event

import blosc as bl
import cv2
import numpy as np
import zmq
from record3d import Record3DStream

INTERNET_HOST = "10.19.143.251"


class DemoApp:
    def __init__(self):
        self.event = Event()
        self.session = None
        self.DEVICE_TYPE__TRUEDEPTH = 0
        self.DEVICE_TYPE__LIDAR = 1

    def on_new_frame(self):
        """
        This method is called from non-main thread, therefore cannot be used for presenting UI.
        """
        self.event.set()  # Notify the main thread to stop waiting and process new frame.

    def on_stream_stopped(self):
        print("Stream stopped")

    def connect_to_device(self, dev_idx):
        print("Searching for devices")
        devs = Record3DStream.get_connected_devices()
        print("{} device(s) found".format(len(devs)))
        for dev in devs:
            print("\tID: {}\n\tUDID: {}\n".format(dev.product_id, dev.udid))

        if len(devs) <= dev_idx:
            raise RuntimeError(
                "Cannot connect to device #{}, try different index.".format(dev_idx)
            )

        dev = devs[dev_idx]
        self.session = Record3DStream()
        self.session.on_new_frame = self.on_new_frame
        self.session.on_stream_stopped = self.on_stream_stopped
        self.session.connect(dev)  # Initiate connection and start capturing

    def get_intrinsic_mat_from_coeffs(self, coeffs):
        return np.array(
            [[coeffs.fx, 0, coeffs.tx], [0, coeffs.fy, coeffs.ty], [0, 0, 1]]
        )

    def start_processing_stream(self):
        context = zmq.Context()
        socket = context.socket(zmq.PUB)
        socket.bind(f"tcp://{INTERNET_HOST}:10011")
        depth_socket = context.socket(zmq.PUB)
        depth_socket.bind(f"tcp://{INTERNET_HOST}:11011")

        while True:
            self.event.wait()  # Wait for new frame to arrive

            # Copy the newly arrived RGBD frame
            depth = self.session.get_depth_frame()
            rgb = self.session.get_rgb_frame()
            confidence = self.session.get_confidence_frame()
            intrinsic_mat = self.get_intrinsic_mat_from_coeffs(
                self.session.get_intrinsic_mat()
            )
            camera_pose = (
                self.session.get_camera_pose()
            )  # Quaternion + world position (accessible via camera_pose.[qx|qy|qz|qw|tx|ty|tz])

            print(intrinsic_mat)

            # You can now e.g. create point cloud by projecting the depth map using the intrinsic matrix.

            # Postprocess it
            if self.session.get_device_type() == self.DEVICE_TYPE__TRUEDEPTH:
                depth = cv2.flip(depth, 1)
                rgb = cv2.flip(rgb, 1)

            rgb = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

            _, buffer = cv2.imencode(".jpg", rgb)
            base64_data = base64.b64encode(buffer).decode("utf-8")
            blosc_depth = bl.pack_array(
                depth * 1000, cname="zstd", clevel=1, shuffle=bl.NOSHUFFLE
            )  # Convert to milimeter

            data = {"rgb_image": base64_data, "timestamp": time.perf_counter_ns()}
            data_depth = {
                "depth_image": blosc_depth,
                "timestamp": time.perf_counter_ns(),
            }

            message = pickle.dumps(data, protocol=-1)
            topic = b"rgb_image "
            message = topic + message

            message_depth = pickle.dumps(data_depth, protocol=-1)
            topic_depth = b"depth_image "
            message_depth = topic_depth + message_depth

            socket.send(message)
            depth_socket.send(message_depth)

            # Show the RGBD Stream
            cv2.imshow("RGB", rgb)
            cv2.imshow("Depth", depth)

            if confidence.shape[0] > 0 and confidence.shape[1] > 0:
                cv2.imshow("Confidence", confidence * 100)

            cv2.waitKey(1)

            self.event.clear()


if __name__ == "__main__":
    app = DemoApp()
    app.connect_to_device(dev_idx=0)
    app.start_processing_stream()
