"""Record Chrome's own compositor frames to a constant-frame-rate H.264 master.

Playwright's built-in recorder encodes VP8 at about 1 Mbit/s, which blurs small UI text once the picture is
zoomed. This recorder asks Chrome for the same screencast frames (CDP Page.screencastFrame) at the full
device-pixel size and encodes them with x264 at high quality. Chrome sends a frame only when the page
changes, so each frame is repeated until the next one arrives, using Chrome's own frame timestamps.
"""
from __future__ import annotations

import base64
import queue
import subprocess
import threading
import time


class ScreencastRecorder:
    def __init__(self, context, page, path, size, fps=25, quality=92, crf=12):
        self.fps = fps
        self.size = size
        self.path = path
        self.cdp = context.new_cdp_session(page)
        self.queue: queue.Queue = queue.Queue()
        self.proc = subprocess.Popen(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', str(fps),
             '-c:v', 'mjpeg', '-i', 'pipe:0',
             '-vf', f'scale={size[0]}:{size[1]}:in_range=full:out_range=tv:in_color_matrix=bt601:out_color_matrix=bt709',
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', str(crf), '-pix_fmt', 'yuv420p',
             '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
             '-r', str(fps), str(path)],
            stdin=subprocess.PIPE)
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.writer.start()
        self.t0 = None              # Chrome timestamp (s since epoch) of the first frame
        self.last = None
        self.next_index = 0
        self.frames_received = 0
        self.max_gap_s = 0.0
        self._previous_ts = None
        self.cdp.on('Page.screencastFrame', self._frame)
        self.cdp.send('Page.startScreencast', {'format': 'jpeg', 'quality': quality, 'maxWidth': size[0],
                                               'maxHeight': size[1], 'everyNthFrame': 1})

    def _write(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            data, count = item
            for _ in range(count):
                self.proc.stdin.write(data)

    def _frame(self, params):
        try:
            self.cdp.send('Page.screencastFrameAck', {'sessionId': params['sessionId']})
        except Exception:  # the session may already be closing
            pass
        stamp = params.get('metadata', {}).get('timestamp') or time.time()
        data = base64.b64decode(params['data'])
        self.frames_received += 1
        if self.t0 is None:
            self.t0 = stamp
        if self._previous_ts is not None:
            self.max_gap_s = max(self.max_gap_s, stamp - self._previous_ts)
        self._previous_ts = stamp
        index = int(round((stamp - self.t0) * self.fps))
        if self.last is not None and index > self.next_index:
            self.queue.put((self.last, index - self.next_index))
            self.next_index = index
        self.last = data

    def now(self):
        """Seconds on the master timeline (0 = first recorded frame)."""
        return time.time() - self.t0 if self.t0 is not None else 0.0

    def stop(self):
        end = time.time()
        try:
            self.cdp.send('Page.stopScreencast')
        except Exception:
            pass
        if self.last is not None:
            index = int(round((end - self.t0) * self.fps))
            self.queue.put((self.last, max(1, index - self.next_index)))
            self.next_index = max(index, self.next_index + 1)
        self.queue.put(None)
        self.writer.join()
        self.proc.stdin.close()
        code = self.proc.wait()
        if code:
            raise RuntimeError(f'ffmpeg exited with {code}')
        return {'frames_written': self.next_index, 'frames_received': self.frames_received,
                'duration_s': self.next_index / self.fps, 'max_gap_between_chrome_frames_s': round(self.max_gap_s, 3),
                'fps': self.fps, 'size': list(self.size)}
