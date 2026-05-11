"""PyQt5 HUD for the DWIN HDW043 4.3" HDMI screen (480x800 portrait).

Shows the annotated camera feed, person/object counts + FPS, the device states,
the latest Gemini scene/decision, and a row of touch buttons (Pause/Resume,
Lamp AUTO/ON/OFF, Center servo). The vision/reflex loop runs in a QThread
(`VisionWorker`) wrapping `src.engine.Engine`; the UI only reacts to its signals.

Launch:  python3 -m src.main --hud           # fullscreen kiosk
         HUD_WINDOWED=1 python3 -m src.main --hud   # in a 480x800 window (dev)
Keys:    Esc/Q quit, P pause, L lamp cycle, C center servo.

Needs python3-pyqt5 (apt) on the Jetson. On the mock backend (no jetson-inference)
the feed shows a placeholder but the rest of the HUD still works.
"""
import logging
import os
import time

log = logging.getLogger("vision.hud")

# Imported lazily inside run_hud() so `import src.main` works without PyQt5 present.
QtCore = QtGui = QtWidgets = None


_STYLE = """
QWidget        { background: #0e1216; color: #e6edf3; font-family: "DejaVu Sans", sans-serif; }
#title         { color: #58c4ff; font-size: 20px; font-weight: bold; }
#status        { font-size: 15px; font-weight: bold; }
#feed          { background: #05070a; border: 1px solid #1d2530; }
#stats         { font-size: 17px; }
#labels        { color: #9fb0c0; font-size: 13px; }
#devices       { font-size: 15px; color: #c9d6e2; }
#gemBox        { background: #11171e; border: 1px solid #1d2530; border-radius: 6px; }
#gemHdr        { color: #b78bff; font-size: 13px; font-weight: bold; }
#gemScene      { font-size: 15px; }
#gemDecision   { color: #9fb0c0; font-size: 13px; }
QPushButton    { background: #1b2530; color: #e6edf3; border: 1px solid #2b3a49;
                 border-radius: 8px; font-size: 16px; font-weight: bold; padding: 10px; }
QPushButton:pressed { background: #2b3a49; }
"""
# small inline styles for the dynamic bits (cheaper than re-parsing _STYLE each frame)
_S_RUN    = "color:#57d977; font-size:15px; font-weight:bold;"
_S_PAUSED = "color:#ffb454; font-size:15px; font-weight:bold;"
_BTN_ARMED  = "background:#244a2c; border:1px solid #3a7d47; border-radius:8px; font-size:16px; font-weight:bold; padding:10px;"
_BTN_PAUSED = "background:#4a3a20; border:1px solid #7d6433; border-radius:8px; font-size:16px; font-weight:bold; padding:10px;"


def _qimage_from_cuda(cuda_img):
    """jetson.utils CUDA image -> QImage (deep-copied so the numpy buffer can die)."""
    if cuda_img is None:
        return None
    try:
        import jetson.utils as ju  # type: ignore
        import numpy as np          # type: ignore
        ju.cudaDeviceSynchronize()
        arr = np.ascontiguousarray(ju.cudaToNumpy(cuda_img))
    except Exception as e:
        log.debug("frame->QImage failed: %s", e)
        return None
    if arr.ndim != 3 or arr.shape[2] not in (3, 4):
        return None
    h, w, ch = int(arr.shape[0]), int(arr.shape[1]), int(arr.shape[2])
    fmt = QtGui.QImage.Format_RGBA8888 if ch == 4 else QtGui.QImage.Format_RGB888
    return QtGui.QImage(arr.data, w, h, ch * w, fmt).copy()


def _build_worker_class():
    class _VisionWorker(QtCore.QThread):
        # (Snapshot, QImage|None)
        snapshot = QtCore.pyqtSignal(object, object)

        def __init__(self, engine, parent=None):
            super(_VisionWorker, self).__init__(parent)
            self.engine = engine
            self._stop = False
            self._idle = 0.0 if engine.det.real else 0.4   # don't busy-spin the mock

        def stop(self):
            self._stop = True

        def run(self):
            try:
                for snap in self.engine.run():
                    if self._stop:
                        break
                    self.snapshot.emit(snap, _qimage_from_cuda(snap.img))
                    if self._idle:
                        time.sleep(self._idle)
            except Exception as e:           # never let the worker thread crash silently
                log.exception("vision worker stopped: %s", e)
    return _VisionWorker


def _build_hud_class():
    QWidget = QtWidgets.QWidget
    Qt = QtCore.Qt

    class _HUD(QWidget):
        def __init__(self, engine, windowed=False):
            super(_HUD, self).__init__()
            self.engine = engine
            self._lamp_mode = 0          # 0 AUTO, 1 ON, 2 OFF
            self.setWindowTitle("Jetson Vision Desk")
            self.setStyleSheet(_STYLE)
            # the DWIN panel is 480x800 but X usually presents it rotated to 800x480 —
            # lay out wide-or-tall to match whatever the screen actually is
            scr = QtWidgets.QApplication.primaryScreen()
            sz = scr.size() if scr is not None else QtCore.QSize(800, 480)
            self._landscape = sz.width() >= sz.height()
            if windowed:
                self.setFixedSize(800, 480) if self._landscape else self.setFixedSize(480, 800)
            else:
                # kiosk: drop window decorations so we look fullscreen even if the WM ignores
                # the FullScreen hint at autostart time (graphical.target races the desktop session)
                self.setWindowFlags(Qt.FramelessWindowHint)
            self._build_ui()

            self.worker = _build_worker_class()(engine)
            self.worker.snapshot.connect(self._on_snapshot)
            self.worker.start()

        # --- layout -------------------------------------------------------
        def _make_feed(self):
            f = QtWidgets.QLabel("camera offline\n(jetson-inference not running)")
            f.setObjectName("feed"); f.setAlignment(Qt.AlignCenter)
            f.setMinimumSize(320, 240); f.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            return f

        def _make_info(self):
            """The stats + devices + Gemini column (used as a side panel in landscape, a stack in portrait)."""
            col = QtWidgets.QVBoxLayout(); col.setSpacing(6)
            self.stats = QtWidgets.QLabel("- people   - objects   - fps"); self.stats.setObjectName("stats")
            self.labels = QtWidgets.QLabel("—"); self.labels.setObjectName("labels"); self.labels.setWordWrap(True)
            self.labels.setMinimumHeight(28)
            self.devices = QtWidgets.QLabel("lamp -   status -   servo -"); self.devices.setObjectName("devices")
            box = QtWidgets.QFrame(); box.setObjectName("gemBox")
            bv = QtWidgets.QVBoxLayout(box); bv.setContentsMargins(10, 6, 10, 6); bv.setSpacing(3)
            gh = QtWidgets.QLabel("GEMINI"); gh.setObjectName("gemHdr")
            self.gemScene = QtWidgets.QLabel("(disabled — no GEMINI_API_KEY)"); self.gemScene.setObjectName("gemScene"); self.gemScene.setWordWrap(True)
            self.gemDecision = QtWidgets.QLabel(""); self.gemDecision.setObjectName("gemDecision"); self.gemDecision.setWordWrap(True)
            bv.addWidget(gh); bv.addWidget(self.gemScene); bv.addWidget(self.gemDecision); bv.addStretch(1)
            for w in (self.stats, self.labels, self.devices):
                col.addWidget(w)
            col.addWidget(box, 1)
            return col

        def _make_buttons(self):
            row = QtWidgets.QHBoxLayout(); row.setSpacing(8)
            self.bPause = QtWidgets.QPushButton("PAUSE"); self.bPause.clicked.connect(self._toggle_pause)
            self.bLamp = QtWidgets.QPushButton("LAMP: AUTO"); self.bLamp.clicked.connect(self._cycle_lamp)
            self.bCenter = QtWidgets.QPushButton("CENTER"); self.bCenter.clicked.connect(self.engine.center_servo)
            for b in (self.bPause, self.bLamp, self.bCenter):
                b.setMinimumHeight(54); b.setFocusPolicy(Qt.NoFocus); row.addWidget(b)
            return row

        def _build_ui(self):
            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(8, 6, 8, 8); root.setSpacing(6)

            # header (always full width on top)
            hdr = QtWidgets.QHBoxLayout()
            t = QtWidgets.QLabel("VISION DESK"); t.setObjectName("title")
            self.status = QtWidgets.QLabel("starting..."); self.status.setObjectName("status"); self.status.setStyleSheet(_S_RUN)
            self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            hdr.addWidget(t); hdr.addStretch(1); hdr.addWidget(self.status)
            root.addLayout(hdr)

            self.feed = self._make_feed()
            info = self._make_info()
            if self._landscape:
                mid = QtWidgets.QHBoxLayout(); mid.setSpacing(8)
                mid.addWidget(self.feed, 3); mid.addLayout(info, 2)
                root.addLayout(mid, 1)
            else:
                self.feed.setMinimumHeight(330)
                root.addWidget(self.feed, 3)
                root.addLayout(info, 2)

            root.addLayout(self._make_buttons())

        # --- button / key actions ----------------------------------------
        def _toggle_pause(self):
            paused = self.engine.toggle_pause()
            self.bPause.setText("RESUME" if paused else "PAUSE")
            self.bPause.setStyleSheet(_BTN_PAUSED if paused else "")

        def _cycle_lamp(self):
            self._lamp_mode = (self._lamp_mode + 1) % 3
            mode, val = [("AUTO", None), ("ON", True), ("OFF", False)][self._lamp_mode]
            self.engine.set_lamp_override(val)
            self.bLamp.setText("LAMP: " + mode)
            self.bLamp.setStyleSheet(_BTN_ARMED if self._lamp_mode == 1 else "")

        def keyPressEvent(self, e):
            k = e.key()
            if k in (Qt.Key_Escape, Qt.Key_Q):
                self.close()
            elif k == Qt.Key_P:
                self._toggle_pause()
            elif k == Qt.Key_L:
                self._cycle_lamp()
            elif k == Qt.Key_C:
                self.engine.center_servo()

        # --- snapshot -> UI ----------------------------------------------
        def _on_snapshot(self, s, qimg):
            # status pill
            self.status.setText("PAUSED" if s.paused else "RUNNING")
            self.status.setStyleSheet(_S_PAUSED if s.paused else _S_RUN)

            # feed
            if qimg is not None:
                pm = QtGui.QPixmap.fromImage(qimg).scaled(
                    self.feed.width(), self.feed.height(), Qt.KeepAspectRatio, Qt.FastTransformation)
                self.feed.setPixmap(pm)
            elif not s.real_det:
                self.feed.setText("camera offline\n(jetson-inference not running)")

            # stats
            n_obj = max(0, len(s.dets) - s.people)
            self.stats.setText("%d people    %d objects    %.0f fps" % (s.people, n_obj, s.fps))
            seen = []
            for d in s.dets:
                tag = "%s %.0f%%" % (d["label"], 100 * d.get("confidence", 0))
                if tag not in seen:
                    seen.append(tag)
            self.labels.setText("  ".join(seen[:8]) if seen else "—")

            # devices
            st = s.state
            servo = st.get("pointer_angle", 0)
            mood = "active/alert" if st.get("mood_light") else "idle"
            self.devices.setText("lamp %s    status-LED %s    servo %.0f deg" % (
                "ON" if st.get("lamp") else "off", mood, servo))

            # gemini
            if not s.gemini_on:
                self.gemScene.setText("(disabled — no GEMINI_API_KEY)"); self.gemDecision.setText("")
            elif s.gemini is None:
                self.gemScene.setText("(no scene yet — querying every few seconds)"); self.gemDecision.setText("")
            else:
                g = s.gemini
                self.gemScene.setText(g.get("scene", "") or "—")
                self.gemDecision.setText("lamp=%s  point_at=%s  alert=%s  •  %s" % (
                    g.get("lamp"), g.get("point_at"), g.get("alert"), g.get("say", "")))

        # --- shutdown -----------------------------------------------------
        def closeEvent(self, e):
            try:
                self.worker.stop()
                self.worker.wait(3000)
            except Exception:
                pass
            try:
                self.engine.shutdown()
            except Exception:
                pass
            e.accept()

    return _HUD


def run_hud():
    """Entry point for `python3 -m src.main --hud`. Returns a process exit code."""
    global QtCore, QtGui, QtWidgets
    try:
        from PyQt5 import QtCore as _C, QtGui as _G, QtWidgets as _W
        QtCore, QtGui, QtWidgets = _C, _G, _W
    except Exception as e:
        print("PyQt5 not available (%s) — install it: sudo apt-get install -y python3-pyqt5" % e)
        return 1

    import signal
    import sys
    from src.engine import Engine

    windowed = bool(os.environ.get("HUD_WINDOWED"))
    app = QtWidgets.QApplication(sys.argv)
    engine = Engine(draw_overlay=True)
    hud = _build_hud_class()(engine, windowed=windowed)
    if windowed:
        hud.show()
    else:
        app.setOverrideCursor(QtCore.Qt.BlankCursor)
        # show() first to let the WM map the window, THEN ask for fullscreen on the next event-loop
        # tick — calling showFullScreen() too early at boot (before mutter/compiz is fully up) makes
        # the WM ignore the hint and the window opens at default size with decorations.
        hud.show()
        QtCore.QTimer.singleShot(200, hud.showFullScreen)

    # let Ctrl-C / SIGTERM close cleanly (Qt only checks Python signals between events,
    # so a heartbeat timer is needed to actually deliver them)
    signal.signal(signal.SIGINT, lambda *a: hud.close())
    signal.signal(signal.SIGTERM, lambda *a: hud.close())
    _beat = QtCore.QTimer(); _beat.start(250); _beat.timeout.connect(lambda: None)
    return app.exec_()
