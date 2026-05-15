"""PyQt5 HUD for the DWIN HDW043 4.3" HDMI screen — "Object Identifier" UI.

Layout (landscape 800x480; falls back to a vertical stack on a portrait panel):
  ┌──────────────────────────────────────────────────────┐
  │ VISION DESK                            <state pill>  │   header
  ├────────────────────┬─────────────────────────────────┤
  │                    │  NAME (big)                     │
  │     LIVE FEED      │  KIND (chip)                    │
  │   (annotated)      │  summary text...                │
  │                    │  • fact 1                       │
  │                    │  • fact 2                       │
  │                    │  • fact 3                       │
  ├────────────────────┴─────────────────────────────────┤
  │       [ IDENTIFY NOW ]          [ CLEAR ]            │   touch
  └──────────────────────────────────────────────────────┘

States from the engine drive the info panel:
  IDLE         → "Hold up an object to identify it."
  WATCHING     → "Looking at <label>... <N.N>s"
  IDENTIFYING  → "Identifying…"
  SHOWING      → the result.

Keys: I = identify now, C = clear, Esc/Q = quit.
Launch: `python3 -m src.main --hud`; HUD_WINDOWED=1 for a 800x480 dev window.
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
#status        { font-size: 14px; font-weight: bold; padding: 4px 10px; border-radius: 10px; }
#feed          { background: #05070a; border: 1px solid #1d2530; }
#hint          { color: #9fb0c0; font-size: 15px; }
#name          { color: #e6edf3; font-size: 22px; font-weight: bold; }
#kindChip      { color: #b78bff; font-size: 11px; font-weight: bold; letter-spacing: 1px;
                 background: #1c1730; border: 1px solid #3a2d5c; border-radius: 8px; padding: 2px 8px; }
#summary       { color: #c9d6e2; font-size: 14px; }
#facts         { color: #9fb0c0; font-size: 13px; }
QPushButton    { background: #1b2530; color: #e6edf3; border: 1px solid #2b3a49;
                 border-radius: 8px; font-size: 16px; font-weight: bold; padding: 10px; }
QPushButton:pressed { background: #2b3a49; }
QPushButton:disabled { color: #4f5a66; }
"""

# small inline styles for the dynamic state pill (cheaper than re-parsing _STYLE each frame)
_PILL_IDLE        = "color:#57d977; background:#16241a; font-size:14px; font-weight:bold; padding:4px 10px; border-radius:10px;"
_PILL_WATCHING    = "color:#ffb454; background:#2a1f0e; font-size:14px; font-weight:bold; padding:4px 10px; border-radius:10px;"
_PILL_IDENTIFYING = "color:#58c4ff; background:#0e2030; font-size:14px; font-weight:bold; padding:4px 10px; border-radius:10px;"
_PILL_SHOWING     = "color:#b78bff; background:#1c1730; font-size:14px; font-weight:bold; padding:4px 10px; border-radius:10px;"


def _qimage_from_cuda(cuda_img):
    """jetson.utils CUDA image -> QImage (deep-copied so the numpy buffer can die)."""
    if cuda_img is None:
        return None
    try:
        import jetson.utils as ju   # type: ignore
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
        # emits (Snapshot, QImage|None)
        snapshot = QtCore.pyqtSignal(object, object)

        def __init__(self, engine, parent=None):
            super(_VisionWorker, self).__init__(parent)
            self.engine = engine
            self._stop = False
            self._idle = 0.0 if engine.det.real else 0.4

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
            except Exception as e:
                log.exception("vision worker stopped: %s", e)
    return _VisionWorker


def _build_hud_class():
    QWidget = QtWidgets.QWidget
    Qt = QtCore.Qt

    class _HUD(QWidget):
        def __init__(self, engine, windowed=False):
            super(_HUD, self).__init__()
            self.engine = engine
            self.setWindowTitle("Jetson Vision Desk")
            self.setStyleSheet(_STYLE)
            scr = QtWidgets.QApplication.primaryScreen()
            sz = scr.size() if scr is not None else QtCore.QSize(800, 480)
            self._landscape = sz.width() >= sz.height()
            if windowed:
                self.setFixedSize(800, 480) if self._landscape else self.setFixedSize(480, 800)
            else:
                # kiosk: drop window decorations so we look fullscreen even if the WM ignores
                # the FullScreen hint at autostart (graphical.target races the desktop session)
                self.setWindowFlags(Qt.FramelessWindowHint)
            self._build_ui()
            self.worker = _build_worker_class()(engine)
            self.worker.snapshot.connect(self._on_snapshot)
            self.worker.start()

        # --- layout ----------------------------------------------------------
        def _make_feed(self):
            f = QtWidgets.QLabel("camera offline\n(jetson-inference not running)")
            f.setObjectName("feed"); f.setAlignment(Qt.AlignCenter)
            f.setMinimumSize(320, 240)
            f.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            return f

        def _make_info(self):
            """The right-hand info panel; widgets here are reused as state changes."""
            col = QtWidgets.QVBoxLayout(); col.setSpacing(8); col.setContentsMargins(8, 4, 4, 4)

            # state-driven hint / "Identifying..."
            self.hint = QtWidgets.QLabel("Hold up an object to identify it.")
            self.hint.setObjectName("hint"); self.hint.setWordWrap(True)
            col.addWidget(self.hint)

            # result widgets — hidden until SHOWING
            self.nameLbl = QtWidgets.QLabel(""); self.nameLbl.setObjectName("name"); self.nameLbl.setWordWrap(True)
            col.addWidget(self.nameLbl)
            kindRow = QtWidgets.QHBoxLayout(); kindRow.setSpacing(6)
            self.kindLbl = QtWidgets.QLabel(""); self.kindLbl.setObjectName("kindChip")
            kindRow.addWidget(self.kindLbl); kindRow.addStretch(1)
            col.addLayout(kindRow)
            self.summaryLbl = QtWidgets.QLabel(""); self.summaryLbl.setObjectName("summary"); self.summaryLbl.setWordWrap(True)
            col.addWidget(self.summaryLbl)
            self.factsLbl = QtWidgets.QLabel(""); self.factsLbl.setObjectName("facts"); self.factsLbl.setWordWrap(True)
            self.factsLbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            col.addWidget(self.factsLbl)
            col.addStretch(1)

            # initial: hide result widgets, show only the hint
            for w in (self.nameLbl, self.kindLbl, self.summaryLbl, self.factsLbl):
                w.setVisible(False)
            return col

        def _make_buttons(self):
            row = QtWidgets.QHBoxLayout(); row.setSpacing(8)
            self.bScan  = QtWidgets.QPushButton("IDENTIFY NOW");  self.bScan.clicked.connect(self.engine.request_identify)
            self.bClear = QtWidgets.QPushButton("CLEAR");         self.bClear.clicked.connect(self.engine.clear)
            for b in (self.bScan, self.bClear):
                b.setMinimumHeight(54); b.setFocusPolicy(Qt.NoFocus); row.addWidget(b)
            return row

        def _build_ui(self):
            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(8, 6, 8, 8); root.setSpacing(6)

            # header (full width)
            hdr = QtWidgets.QHBoxLayout()
            t = QtWidgets.QLabel("VISION DESK"); t.setObjectName("title")
            self.status = QtWidgets.QLabel("starting…"); self.status.setObjectName("status")
            self.status.setStyleSheet(_PILL_IDLE)
            self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            hdr.addWidget(t); hdr.addStretch(1); hdr.addWidget(self.status)
            root.addLayout(hdr)

            # middle: feed + info
            self.feed = self._make_feed()
            info = self._make_info()
            if self._landscape:
                mid = QtWidgets.QHBoxLayout(); mid.setSpacing(10)
                mid.addWidget(self.feed, 4); mid.addLayout(info, 5)
                root.addLayout(mid, 1)
            else:
                self.feed.setMinimumHeight(330)
                root.addWidget(self.feed, 3); root.addLayout(info, 2)

            # bottom: buttons
            root.addLayout(self._make_buttons())

        # --- key shortcuts (I = identify, C = clear, Esc/Q = quit) ----------
        def keyPressEvent(self, e):
            k = e.key()
            if k in (Qt.Key_Escape, Qt.Key_Q):
                self.close()
            elif k == Qt.Key_I:
                self.engine.request_identify()
            elif k == Qt.Key_C:
                self.engine.clear()

        # --- snapshot -> UI -------------------------------------------------
        def _on_snapshot(self, s, qimg):
            # update the live feed pixmap
            if qimg is not None:
                pm = QtGui.QPixmap.fromImage(qimg).scaled(
                    self.feed.width(), self.feed.height(),
                    Qt.KeepAspectRatio, Qt.FastTransformation)
                self.feed.setPixmap(pm)
            elif not s.real_det:
                self.feed.setText("camera offline\n(jetson-inference not running)")

            # state pill + info panel
            if s.state == "IDLE":
                self.status.setText("IDLE"); self.status.setStyleSheet(_PILL_IDLE)
                self._show_hint("Hold up an object to identify it.")
            elif s.state == "WATCHING":
                self.status.setText("LOOKING %.1fs" % s.watch_elapsed); self.status.setStyleSheet(_PILL_WATCHING)
                self._show_hint("Looking at %s... hold steady (%.1fs)" %
                                (s.watch_label or "?", s.watch_elapsed))
            elif s.state == "IDENTIFYING":
                self.status.setText("IDENTIFYING…"); self.status.setStyleSheet(_PILL_IDENTIFYING)
                self._show_hint("Identifying… (asking Gemini)")
            elif s.state == "SHOWING":
                self.status.setText("RESULT"); self.status.setStyleSheet(_PILL_SHOWING)
                self._show_result(s.result)

            # if Gemini is off, surface that prominently in IDLE
            if s.state == "IDLE" and not s.gemini_on:
                self.hint.setText("Gemini disabled — set GEMINI_API_KEY in ~/jetson-vision-desk-data/.env")

        def _show_hint(self, text):
            self.hint.setText(text); self.hint.setVisible(True)
            for w in (self.nameLbl, self.kindLbl, self.summaryLbl, self.factsLbl):
                w.setVisible(False)

        def _show_result(self, result):
            if not result:
                self._show_hint("(no result)"); return
            self.hint.setVisible(False)
            self.nameLbl.setText(result.get("name", "Unknown"));   self.nameLbl.setVisible(True)
            self.kindLbl.setText(" " + result.get("kind", "other").upper() + " "); self.kindLbl.setVisible(True)
            self.summaryLbl.setText(result.get("summary", ""));    self.summaryLbl.setVisible(True)
            facts = result.get("facts") or []
            self.factsLbl.setText("\n".join("• " + f for f in facts) if facts else ""); self.factsLbl.setVisible(True)

        # --- shutdown -------------------------------------------------------
        def closeEvent(self, e):
            try:
                self.worker.stop(); self.worker.wait(3000)
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
        # show() first to let the WM map the window, THEN ask for fullscreen on the next event-
        # loop tick — calling showFullScreen() too early at boot (before mutter/compiz is fully
        # up) makes the WM ignore the hint and the window opens at default size with decorations.
        hud.show()
        QtCore.QTimer.singleShot(200, hud.showFullScreen)

    # Ctrl-C / SIGTERM closes cleanly (Qt only checks Python signals between events,
    # so a heartbeat timer is needed to actually deliver them).
    signal.signal(signal.SIGINT, lambda *a: hud.close())
    signal.signal(signal.SIGTERM, lambda *a: hud.close())
    _beat = QtCore.QTimer(); _beat.start(250); _beat.timeout.connect(lambda: None)
    return app.exec_()
