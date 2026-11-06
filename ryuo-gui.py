#!/usr/bin/python3
"""RYUO Control — native Qt6 desktop companion for the Linux LCD service."""
import importlib.util
import html
import os
import json
import re
import subprocess
import sys
import tempfile
import uuid
from collections import deque
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QProcess, QRectF, QSignalBlocker, Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QColor, QDrag, QImage, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSlider,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QLibraryInfo, QLocale, QSettings, QTranslator
from PySide6.QtWidgets import QAbstractButton
from ryuo_i18n import tr, set_language, retranslate
from ryuo_desktop import detected_device, preview_cache_path, sensor_value

from ryuo_config import CONFIG_PATH, PRESETS, SENSORS, SLOT_COUNT, load_config, validate_config, valid_media_name, custom_video_path

spec = importlib.util.spec_from_file_location('ryuo_driver', Path(__file__).with_name('ryuo-lcd.py'))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)

STYLE = '''
QWidget { background: #11151c; color: #e8edf5; font-family: "Noto Sans"; font-size: 13px; }
QMainWindow { background: #11151c; }
QLabel#brand { font-size: 25px; font-weight: 800; color: #7de0d3; }
QLabel#heading { font-size: 28px; font-weight: 700; }
QLabel#muted { color: #99a6b9; }
QLabel#metric { font-size: 32px; font-weight: 700; }
QListWidget { background: #0c1016; border: none; padding: 12px; outline: none; }
QListWidget::item { padding: 15px 12px; margin-bottom: 8px; border-radius: 8px; }
QListWidget::item:selected { background: #20343a; color: #7de0d3; }
QGroupBox { border: 1px solid #303a49; border-radius: 12px; margin-top: 14px; padding: 20px 14px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; }
QFrame#card { background: #1a222e; border-radius: 12px; }
QFrame#card QLabel { background: transparent; }
QPushButton { background: #253142; border: 1px solid #3b4a60; border-radius: 7px; padding: 9px 14px; }
QPushButton:hover { background: #34465c; }
QPushButton:disabled { color: #697587; background: #1a222e; }
QPushButton#primary { background: #7de0d3; color: #0c2427; border: none; font-weight: 700; }
QLineEdit, QComboBox, QTextEdit { background: #1a222e; border: 1px solid #3b4a60; border-radius: 6px; padding: 7px; }
QCheckBox { spacing: 10px; padding: 4px; }
QSlider::groove:horizontal { height: 5px; background: #34465c; border-radius: 2px; }
QSlider::handle:horizontal { background: #7de0d3; width: 16px; margin: -6px 0; border-radius: 8px; }
QScrollArea { border: none; }
'''


def label(text, name=None):
    widget = QLabel(text)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


def configure_qt_language(language):
    """Localize Qt's standard dialogs independently of the desktop locale."""
    app = QApplication.instance()
    previous = getattr(app, '_ryuo_translator', None)
    if previous is not None:
        app.removeTranslator(previous)
    QLocale.setDefault(QLocale('es' if language == 'es' else 'en'))
    translator = QTranslator(app)
    if language == 'es' and translator.load('qtbase_es', QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    if previous is not None:
        previous.deleteLater()
    app._ryuo_translator = translator


def retranslate_widgets(root):
    """Update visible text in place, preserving values, focus and playback."""
    for widget in [root, *root.findChildren(QWidget)]:
        for getter, setter in (('toolTip', 'setToolTip'), ('accessibleName', 'setAccessibleName'),
                               ('placeholderText', 'setPlaceholderText'), ('windowTitle', 'setWindowTitle')):
            if hasattr(widget, getter):
                getattr(widget, setter)(retranslate(getattr(widget, getter)()))
        if isinstance(widget, (QLabel, QAbstractButton)):
            widget.setText(retranslate(widget.text()))
        if isinstance(widget, QGroupBox):
            widget.setTitle(retranslate(widget.title()))
        if isinstance(widget, QComboBox):
            with QSignalBlocker(widget):
                for index in range(widget.count()):
                    widget.setItemText(index, retranslate(widget.itemText(index)))
        if isinstance(widget, QListWidget):
            for index in range(widget.count()):
                item = widget.item(index)
                item.setText(retranslate(item.text()))


class Monitor(QThread):
    updated = Signal(dict)

    def run(self):
        while not self.isInterruptionRequested():
            result = {}
            try:
                result['sensor_sources'] = {}
                result['sensors'] = driver.get_sensors(load_config()['cpu_temp_mode'], report=result['sensor_sources'])
                result['device'] = detected_device()
                status = subprocess.run(
                    ['/usr/bin/systemctl', 'show', 'ryuo-lcd.service',
                     '--property=ActiveState,SubState,UnitFileState'],
                    capture_output=True, text=True, timeout=4)
                if status.returncode:
                    raise RuntimeError(status.stderr.strip() or tr('No se puede consultar systemd'))
                result['service'] = dict(line.split('=', 1) for line in status.stdout.splitlines() if '=' in line)
            except Exception as error:
                result['error'] = str(error)
            self.updated.emit(result)
            for _ in range(20):
                if self.isInterruptionRequested():
                    return
                self.msleep(100)


class History(QWidget):
    def __init__(self):
        super().__init__()
        self.cpu = deque(maxlen=60)
        self.gpu = deque(maxlen=60)
        self.setMinimumHeight(120)

    def add(self, cpu, gpu):
        self.cpu.append(cpu)
        self.gpu.append(gpu)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height() - 20
        painter.setPen(QPen(QColor('#2b3544'), 1))
        for fraction in (0, .5, 1):
            y = int(8 + fraction * (h - 8))
            painter.drawLine(0, y, w, y)
        for values, color in ((self.cpu, '#7de0d3'), (self.gpu, '#f6b56a')):
            painter.setPen(QPen(QColor(color), 2))
            points = [(int(i * w / 59), int(h - max(0, min(100, value)) * (h - 8) / 100))
                      for i, value in enumerate(values)]
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                painter.drawLine(x1, y1, x2, y2)


class SensorCard(QLabel):
    """Drag a sensor between the six firmware slots, preserving empty slots."""
    exchanged = Signal(int, int)
    selected = Signal(int)
    MIME = 'application/x-ryuo-sensor-slot'

    def __init__(self, slot, owner):
        super().__init__()
        self.slot = slot
        self.owner = owner
        self.press_position = None
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr('Posición {0} del LCD', slot + 1))
        self.setToolTip(tr('Haz clic para elegir el sensor. Arrastra a otro sensor para intercambiarlos. Enter también selecciona esta posición.'))
        self.highlight(False)

    def highlight(self, active):
        border = '#7de0d3' if active else '#4c5369'
        self.setStyleSheet(f'background: rgba(8, 12, 24, 218); border: 2px solid {border}; border-radius: 9px; padding: 5px;')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.press_position is not None and event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.slot)
        self.press_position = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.selected.emit(self.slot)
            event.accept()
        else:
            super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.press_position is None or not event.buttons() & Qt.MouseButton.LeftButton
                or (event.position().toPoint() - self.press_position).manhattanLength()
                < QApplication.startDragDistance()):
            return
        self.press_position = None
        mime = QMimeData()
        mime.setData(self.MIME, str(self.slot).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.highlight(False)
            drag.deleteLater()

    def source_slot(self, event):
        source = event.source()
        if (not isinstance(source, SensorCard) or source.owner is not self.owner
                or source is self or not event.mimeData().hasFormat(self.MIME)):
            return None
        payload = bytes(event.mimeData().data(self.MIME))
        return source.slot if payload == str(source.slot).encode() else None

    def dragEnterEvent(self, event):
        if self.source_slot(event) is not None:
            self.highlight(True)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self.highlight(False)
        event.accept()

    def dropEvent(self, event):
        self.highlight(False)
        source = self.source_slot(event)
        if source is None:
            event.ignore()
            return
        self.exchanged.emit(source, self.slot)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()


class DevicePreview(QWidget):
    """Official device image with editable widgets over the screen surface."""
    # Perspective approximation of full-screen slots, checked against this
    # AIO's tv_title1..6 / tv_value1..6 bounds via Android UIAutomator.
    ANCHORS = ((.25, .43), (.24, .61), (.79, .52), (.79, .66), (.78, .82), (.55, .76))
    playback_message = Signal(str)

    def __init__(self, owner):
        super().__init__()
        source = Path(__file__).with_name('assets') / 'ryuo-screen.png'
        if not source.exists():
            source = Path('/usr/local/share/ryuo-control/ryuo-screen.png')
        self.picture = QPixmap(str(source))
        self.video_image = QImage()
        self.player = None
        self.video_sink = None
        self.setMinimumHeight(440)
        self.setMinimumWidth(540)
        self.cards = []
        for index in range(SLOT_COUNT):
            card = SensorCard(index, owner)
            card.setParent(self)
            card.exchanged.connect(owner.swap_slots)
            card.selected.connect(owner.select_slot)
            self.cards.append(card)

    def play_video(self, path):
        if self.player is None:
            self.player = QMediaPlayer(self)
            self.video_sink = QVideoSink(self)
            self.player.setVideoSink(self.video_sink)
            self.player.setLoops(QMediaPlayer.Loops.Infinite)
            self.video_sink.videoFrameChanged.connect(self.receive_frame)
            self.player.errorOccurred.connect(
                lambda error, text: self.playback_message.emit(tr('No se puede reproducir localmente: {0}', text)))
        source = QUrl.fromLocalFile(str(Path(path).resolve()))
        if self.player.source() != source:
            self.video_image = QImage()
            self.player.setSource(source)
        self.player.play()

    def receive_frame(self, frame):
        image = frame.toImage()
        if not image.isNull():
            self.video_image = image
            self.update()

    def stop_video(self):
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        self.video_image = QImage()
        self.update()

    def paint_video(self, painter):
        if self.video_image.isNull():
            return
        rectangle = self.image_rect()
        image = self.video_image
        split = int(image.width() * 1606 / 2240)
        # Two projected faces approximate the curve without changing the asset.
        for source, points in (
            (QRectF(0, 0, split, image.height()), ((190, 280), (740, 485), (710, 930), (165, 705))),
            (QRectF(split, 0, image.width() - split, image.height()), ((740, 485), (980, 440), (935, 825), (710, 930))),
        ):
            destination = QPolygonF([QPointF(rectangle.left() + x / 1100 * rectangle.width(),
                                             rectangle.top() + y / 1026 * rectangle.height()) for x, y in points])
            original = QPolygonF([source.topLeft(), source.topRight(), source.bottomRight(), source.bottomLeft()])
            transform = QTransform()
            if QTransform.quadToQuad(original, destination, transform):
                painter.save()
                painter.setTransform(transform)
                painter.drawImage(source, image, source)
                painter.restore()

    def image_rect(self):
        ratio = 1100 / 1026
        height = min(self.height() - 12, (self.width() - 20) / ratio)
        width = height * ratio
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def resizeEvent(self, event):
        rectangle = self.image_rect()
        width = max(96, min(145, int(rectangle.width() * .22)))
        height = max(52, min(68, int(rectangle.height() * .13)))
        for card, (x, y) in zip(self.cards, self.ANCHORS):
            card.setGeometry(int(rectangle.left() + x * rectangle.width() - width / 2),
                             int(rectangle.top() + y * rectangle.height() - height / 2), width, height)
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self.picture.isNull():
            painter.drawPixmap(self.image_rect(), self.picture, QRectF(self.picture.rect()))
        else:
            painter.setBrush(QColor('#202735'))
            painter.setPen(QColor('#626d82'))
            painter.drawRoundedRect(self.image_rect(), 24, 24)
        self.paint_video(painter)


class Window(QMainWindow):
    def __init__(self, monitor=True, cache_root=None, language=None, preferences=None):
        super().__init__()
        self.preferences = preferences if preferences is not None else QSettings('RYUOControl', 'RYUOControl')
        requested = language if language is not None else self.preferences.value('language', 'en')
        self.language = requested if requested in ('en', 'es') else 'en'
        set_language(self.language)
        configure_qt_language(self.language)
        self.setWindowTitle('RYUO Control')
        self.resize(1100, 850)
        self.process = None
        self.pending_action = None
        self.monitor = None
        self.title_color = '#E5252B'
        self.content_color = '#FFFFFF'
        self.dirty = False
        self.last_sensors = None
        self.sensor_sources = {}
        self.action_buttons = []
        self.cache_root = cache_root
        self.local_video_sources = {}
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        side = QWidget()
        side.setFixedWidth(205)
        nav_layout = QVBoxLayout(side)
        nav_layout.setContentsMargins(16, 28, 16, 20)
        nav_layout.addWidget(label('RYUO\nCONTROL', 'brand'))
        nav_layout.addWidget(label('Linux · Qt6', 'muted'))
        nav_layout.addWidget(label(tr('Idioma'), 'muted'))
        self.language_choice = QComboBox()
        self.language_choice.setAccessibleName(tr('Idioma'))
        self.language_choice.addItem('English', 'en')
        self.language_choice.addItem('Español', 'es')
        self.language_choice.setCurrentIndex(self.language_choice.findData(self.language))
        self.language_choice.currentIndexChanged.connect(self.change_language)
        nav_layout.addWidget(self.language_choice)
        self.nav = QListWidget()
        self.nav.addItems([tr('Resumen'), tr('Pantalla'), tr('Servicio')])
        nav_layout.addWidget(self.nav)
        nav_layout.addWidget(label(tr('ROG RYUO IV\nControl independiente'), 'muted'))
        outer.addWidget(side)
        body = QVBoxLayout()
        body.setContentsMargins(24, 24, 24, 20)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.overview_page())
        self.pages.addWidget(self.screen_page())
        self.pages.addWidget(self.service_page())
        body.addWidget(self.pages)
        self.notice = label(tr('Listo. Los cambios se envían al pulsar «Aplicar al LCD».'), 'muted')
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        body.addWidget(self.notice)
        outer.addLayout(body, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.reload_settings()
        if monitor:
            self.monitor = Monitor(self)
            self.monitor.updated.connect(self.update_status)
            self.monitor.start()

    def overview_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label(tr('Tu equipo, en vivo'), 'heading'))
        layout.addWidget(label(tr('Sensores locales · actualización cada 2 segundos'), 'muted'))
        cards = QHBoxLayout()
        self.metrics = {}
        for key, title in [('cpu', tr('CPU · temperatura')), ('gpu', tr('GPU · temperatura')), ('memory', tr('Memoria · uso'))]:
            frame = QFrame()
            frame.setObjectName('card')
            box = QVBoxLayout(frame)
            box.setContentsMargins(18, 20, 18, 20)
            box.addWidget(label(title, 'muted'))
            value = label('—', 'metric')
            detail = label(tr('Esperando sensores…'), 'muted')
            box.addWidget(value)
            box.addWidget(detail)
            self.metrics[key] = (value, detail)
            cards.addWidget(frame)
        layout.addLayout(cards)
        graph_box = QGroupBox(tr('Uso · últimas 60 muestras · escala 0–100 %'))
        graph_layout = QVBoxLayout(graph_box)
        graph_layout.addWidget(label(tr('CPU: turquesa     GPU: naranja'), 'muted'))
        self.history = History()
        graph_layout.addWidget(self.history)
        layout.addWidget(graph_box)
        device_box = QGroupBox(tr('Conexión'))
        device_layout = QVBoxLayout(device_box)
        self.device_label = label(tr('Buscando RYUO IV…'))
        self.service_label = label(tr('Consultando servicio…'), 'muted')
        device_layout.addWidget(self.device_label)
        device_layout.addWidget(self.service_label)
        device_layout.addWidget(label(tr('La pantalla sigue funcionando al cerrar esta ventana si el servicio está activo.'), 'muted'))
        layout.addWidget(device_box)
        layout.addStretch()
        return page

    def screen_page(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.addWidget(label(tr('Diseña tu pantalla'), 'heading'))
        layout.addWidget(label(tr('Videos preinstalados y datos que se muestran en el LCD.'), 'muted'))
        grid = QGridLayout()
        videos = QGroupBox(tr('Videos del dispositivo'))
        video_layout = QVBoxLayout(videos)
        self.video_checks = {}
        self.video_rows = {}
        self.delete_buttons = {}
        self.video_list_layout = QVBoxLayout()
        video_layout.addLayout(self.video_list_layout)
        for index, preset in enumerate(PRESETS, 1):
            self.add_video(preset, f'Preset {index:02}  ·  {preset}')
        upload = QPushButton(tr('Subir y mostrar video MP4…'))
        upload.clicked.connect(self.upload_video)
        refresh = QPushButton(tr('Leer videos del LCD'))
        refresh.clicked.connect(self.list_videos)
        download_presets = QPushButton(tr('Descargar presets…'))
        download_presets.clicked.connect(lambda: self.download_videos('presets'))
        download_custom = QPushButton(tr('Descargar videos propios…'))
        download_custom.clicked.connect(lambda: self.download_videos('custom'))
        self.action_buttons.extend([upload, refresh, download_presets, download_custom])
        video_layout.addWidget(upload)
        video_layout.addWidget(refresh)
        video_layout.addWidget(download_presets)
        video_layout.addWidget(download_custom)
        self.play_mode = QComboBox()
        for title, value in [(tr('En secuencia'), 'Cycle'), (tr('Aleatorio'), 'Random'), (tr('Individual'), 'Single')]:
            self.play_mode.addItem(title, value)
        self.play_mode.currentIndexChanged.connect(self.changed)
        video_layout.addWidget(self.play_mode)
        video_layout.addWidget(label(tr('Máximo 5 videos seleccionados. Para archivos propios: MP4 H.264 hasta 1920×1080 o HEVC hvc1 a 2240×1080. Se copian sin convertir.'), 'muted'))
        grid.addWidget(videos, 0, 0)
        self.sensor_assignments = [''] * SLOT_COUNT
        preview_box = QGroupBox(tr('ROG RYUO IV SLC 360 ARGB · editor de pantalla'))
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.addWidget(label(tr('Arrastra un sensor sobre otro para intercambiarlos. Haz clic en uno para cambiar qué muestra.'), 'muted'))
        playback = QHBoxLayout()
        self.preview_choice = QComboBox()
        self.preview_choice.setAccessibleName(tr('Video para vista previa local'))
        self.preview_choice.currentIndexChanged.connect(self.preview_choice_changed)
        self.preview_play = QPushButton(tr('Reproducir vista previa'))
        self.preview_play.clicked.connect(lambda: self.start_video_preview())
        preview_refresh = QPushButton(tr('Actualizar copia'))
        preview_refresh.clicked.connect(lambda: self.start_video_preview(force=True))
        self.action_buttons.extend([self.preview_play, preview_refresh])
        playback.addWidget(self.preview_choice, 1)
        playback.addWidget(self.preview_play)
        playback.addWidget(preview_refresh)
        preview_layout.addLayout(playback)
        self.preview_status = label(tr('Vista previa local del video elegido · sin sincronización con el AIO.'), 'muted')
        self.preview_status.setTextFormat(Qt.TextFormat.PlainText)
        preview_layout.addWidget(self.preview_status)
        self.device_preview = DevicePreview(self)
        self.device_preview.playback_message.connect(self.preview_status.setText)
        self.slot_previews = self.device_preview.cards
        preview_layout.addWidget(self.device_preview)
        editor = QHBoxLayout()
        self.editor_slot = QComboBox()
        self.editor_slot.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for index in range(SLOT_COUNT):
            self.editor_slot.addItem(tr('Posición {0:02}', index + 1), index)
        self.editor_slot.setAccessibleName(tr('Posición del sensor a editar'))
        self.editor_slot.currentIndexChanged.connect(self.select_slot)
        self.editor_sensor = QComboBox()
        self.editor_sensor.addItem(tr('Vacía · ocultar'), '')
        for key, title in SENSORS.items():
            self.editor_sensor.addItem(tr(title), key)
        self.editor_sensor.setAccessibleName(tr('Sensor de la posición seleccionada'))
        self.editor_sensor.currentIndexChanged.connect(self.edit_slot_sensor)
        editor.addWidget(self.editor_slot)
        editor.addWidget(self.editor_sensor, 1)
        clear = QPushButton(tr('Ocultar todos'))
        clear.clicked.connect(self.clear_slots)
        editor.addWidget(clear)
        preview_layout.addLayout(editor)
        preview_layout.addWidget(label(tr('Imagen de referencia ASUS; perspectiva aproximada. Los cambios se envían al pulsar «Aplicar al LCD».'), 'muted'))
        layout.addWidget(preview_box)
        layout.addLayout(grid)
        settings = QGroupBox(tr('Lecturas y apariencia'))
        form = QFormLayout(settings)
        self.cpu_mode = QComboBox()
        self.cpu_mode.addItem(tr('AMD: promedio CCD · Intel: paquete'), 'ccd_average')
        self.cpu_mode.addItem(tr('AMD: Tctl · Intel: paquete'), 'tctl')
        self.cpu_mode.currentIndexChanged.connect(self.changed)
        form.addRow(tr('Temperatura CPU'), self.cpu_mode)
        self.title_button = QPushButton()
        self.content_button = QPushButton()
        self.title_button.clicked.connect(lambda: self.choose_color('title'))
        self.content_button.clicked.connect(lambda: self.choose_color('content'))
        colors = QHBoxLayout()
        colors.addWidget(self.title_button)
        colors.addWidget(self.content_button)
        form.addRow(tr('Colores'), colors)
        layout.addWidget(settings)
        brightness_box = QGroupBox(tr('Brillo por ADB'))
        form = QFormLayout(brightness_box)
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness_value = label('100 %')
        slider_row = QHBoxLayout()
        slider_row.addWidget(self.brightness)
        slider_row.addWidget(self.brightness_value)
        self.brightness.valueChanged.connect(self.brightness_changed)
        form.addRow(tr('Nivel'), slider_row)
        self.serial = QLineEdit()
        self.serial.setPlaceholderText(tr('Vacío: usa la variable del servicio, si existe'))
        self.serial.textEdited.connect(self.changed)
        serial_row = QHBoxLayout()
        serial_row.addWidget(self.serial)
        detect = QPushButton(tr('Detectar serie USB'))
        detect.clicked.connect(self.detect_serial)
        serial_row.addWidget(detect)
        form.addRow(tr('Serie del LCD'), serial_row)
        form.addRow(label(tr('Requiere ADB autorizado. El resultado se comprueba en el registro del servicio.'), 'muted'))
        layout.addWidget(brightness_box)
        buttons = QHBoxLayout()
        reload_button = QPushButton(tr('Recargar guardado'))
        reload_button.clicked.connect(self.reload_settings)
        self.action_buttons.append(reload_button)
        buttons.addWidget(reload_button)
        buttons.addStretch()
        apply = QPushButton(tr('Aplicar al LCD'))
        apply.setObjectName('primary')
        apply.clicked.connect(self.apply_settings)
        self.action_buttons.append(apply)
        buttons.addWidget(apply)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        page_layout.addLayout(buttons)
        return page

    def service_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label(tr('Servicio y diagnóstico'), 'heading'))
        layout.addWidget(label(tr('El servicio mantiene el LCD actualizado en segundo plano.'), 'muted'))
        for actions in ([(tr('Iniciar'), 'start'), (tr('Detener'), 'stop'), (tr('Reiniciar'), 'restart')],
                        [(tr('Activar al arrancar'), 'enable'), (tr('Desactivar al arrancar'), 'disable'), (tr('Leer registro'), 'logs')]):
            row = QHBoxLayout()
            for title, action in actions:
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False, a=action: self.run_action(a))
                self.action_buttons.append(button)
                row.addWidget(button)
            layout.addLayout(row)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(2000)
        self.logs.setPlaceholderText(tr('Pulsa «Leer registro» para consultar los últimos eventos.\nLas acciones administrativas abren la autenticación del sistema.'))
        layout.addWidget(self.logs)
        layout.addWidget(label(tr('Control del LCD: videos, sensores y brillo. Las curvas de bomba y ventiladores se configuran por separado en BIOS. No se actualiza firmware.'), 'muted'))
        return page

    def changed(self, *args):
        self.dirty = True
        if hasattr(self, 'notice'):
            self.notice.setText(tr('Hay cambios sin aplicar.'))

    def change_language(self, *args):
        language = self.language_choice.currentData()
        if language == self.language or self.process is not None:
            return
        self.language = language
        set_language(language)
        configure_qt_language(language)
        retranslate_widgets(self)
        self.update_color_buttons()
        self.preferences.setValue('language', language)
        self.preferences.sync()
        message = tr('Idioma actualizado.') if self.preferences.status() == QSettings.Status.NoError else tr('No se pudo guardar el idioma.')
        self.notice.setText(message + (tr(' Hay cambios sin aplicar.') if self.dirty else ''))

    def add_video(self, name, title=None):
        if name in self.video_checks or not valid_media_name(name):
            return
        short_name = name if len(name) <= 42 else name[:24] + '…' + name[-16:]
        check = QCheckBox(title or short_name)
        check.setAccessibleName(title or name)
        check.setToolTip(name)
        check.toggled.connect(self.video_selection_changed)
        self.video_checks[name] = check
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(check, 1)
        if name not in PRESETS:
            delete = QPushButton(tr('Borrar…'))
            delete.setAccessibleName(tr('Borrar del AIO el video {0}', name))
            delete.clicked.connect(lambda checked=False, video=name: self.delete_video(video))
            delete.setEnabled(self.process is None)
            self.delete_buttons[name] = delete
            self.action_buttons.append(delete)
            row_layout.addWidget(delete)
        self.video_rows[name] = row
        self.video_list_layout.addWidget(row)

    def video_selection_changed(self, *args):
        self.changed()
        self.refresh_preview_choices()

    def refresh_preview_choices(self):
        if not hasattr(self, 'preview_choice'):
            return
        current = self.preview_choice.currentData()
        selected = [key for key, check in self.video_checks.items() if check.isChecked()]
        with QSignalBlocker(self.preview_choice):
            self.preview_choice.clear()
            for name in selected:
                self.preview_choice.addItem(name, name)
            index = self.preview_choice.findData(current)
            self.preview_choice.setCurrentIndex(index if index >= 0 else (0 if selected else -1))
        if current != self.preview_choice.currentData():
            self.preview_choice_changed()

    def preview_choice_changed(self, *args):
        if hasattr(self, 'device_preview'):
            self.device_preview.stop_video()
        if hasattr(self, 'preview_status'):
            self.preview_play.setText(tr('Reproducir vista previa'))
            self.preview_status.setText(tr('Pulsa reproducir para ver el video seleccionado con los sensores. La copia se descarga solo si hace falta.'))

    def play_preview_file(self, path):
        self.device_preview.play_video(path)
        self.preview_play.setText(tr('Pausar vista previa'))
        self.preview_status.setText(tr('Vista previa local · {0} · en bucle y sin audio. No cambia el LCD.', self.preview_choice.currentData()))

    def start_video_preview(self, force=False):
        if self.process is not None:
            return
        name = self.preview_choice.currentData()
        if not name:
            self.preview_status.setText(tr('Selecciona al menos un video de la lista para previsualizarlo.'))
            return
        player = self.device_preview.player
        if not force and player is not None and player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
            self.preview_play.setText(tr('Reanudar vista previa'))
            self.preview_status.setText(tr('Video pausado. Los sensores siguen actualizándose.'))
            return
        if not force and player is not None and player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            player.play()
            self.preview_play.setText(tr('Pausar vista previa'))
            self.preview_status.setText(tr('Vista previa local reanudada. Los cambios siguen sin enviarse al LCD.'))
            return
        local_source = self.local_video_sources.get(name)
        if not force and local_source is not None and local_source.is_file():
            self.play_preview_file(local_source)
            return
        try:
            serial = self.adb_serial()
            destination = preview_cache_path(serial, name, self.cache_root)
            if not force and destination.is_file() and destination.stat().st_size > 0:
                self.play_preview_file(destination)
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.preview_fetch_name = name
            self.preview_fetch_target = destination
            self.preview_fetch_temp = destination.with_name(f'.{name}.{uuid.uuid4().hex}.part')
        except (ValueError, OSError) as error:
            self.preview_status.setText(tr('No se pudo preparar la vista previa: {0}', error))
            return
        folder = 'pcMediaPreset' if name in PRESETS else 'pcMedia'
        self.preview_status.setText(tr('Descargando una copia de {0} para vista previa…', name))
        self.start_process('preview_fetch', '/usr/bin/adb',
                           ['-s', serial, 'pull', f'/sdcard/{folder}/{name}', str(self.preview_fetch_temp)])

    def finish_preview_fetch(self, success):
        try:
            if not success or not self.preview_fetch_temp.is_file() or self.preview_fetch_temp.stat().st_size == 0:
                raise OSError(tr('ADB no pudo descargar una copia completa. Revisa el registro y la conexión.'))
            os.replace(self.preview_fetch_temp, self.preview_fetch_target)
            if self.preview_choice.currentData() == self.preview_fetch_name:
                self.play_preview_file(self.preview_fetch_target)
            else:
                self.preview_status.setText(tr('Copia descargada en caché. Pulsa reproducir para el video que tienes seleccionado.'))
        except OSError as error:
            self.preview_status.setText(tr('Vista previa no disponible: {0}', error))
        finally:
            try:
                self.preview_fetch_temp.unlink(missing_ok=True)
            except OSError:
                pass

    def delete_video(self, name):
        if self.process is not None:
            return
        try:
            remote = custom_video_path(name)
            if name not in self.video_checks:
                raise ValueError(tr('Primero lee la lista de videos del LCD.'))
            # The running service loads this playlist at startup. Require users
            # to apply another playlist before removing a file it may be using.
            if name in load_config()['media']:
                raise ValueError(tr('Este video está en la lista guardada. Desmárcalo, selecciona otro video y pulsa «Aplicar al LCD» antes de borrarlo.'))
            serial = self.adb_serial()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Borrar video'), str(error))
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle(tr('Borrar video del AIO'))
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(tr('¿Borrar definitivamente este video del AIO?\n\n{0}', name))
        dialog.setInformativeText(tr('Esta acción no se puede deshacer. Los presets no se borrarán.'))
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        self.delete_name = name
        self.delete_serial = serial
        self.start_process('delete_video', '/usr/bin/adb', ['-s', serial, 'shell', 'rm', '--', remote])

    def remove_video(self, name):
        check = self.video_checks.pop(name)
        was_selected = check.isChecked()
        delete = self.delete_buttons.pop(name)
        self.action_buttons.remove(delete)
        row = self.video_rows.pop(name)
        self.video_list_layout.removeWidget(row)
        row.deleteLater()
        if was_selected:
            if not any(item.isChecked() for item in self.video_checks.values()):
                self.video_checks[PRESETS[0]].setChecked(True)
            self.changed()
        self.refresh_preview_choices()

    def sync_custom_videos(self, output):
        names = {name.strip() for name in output.splitlines()
                 if valid_media_name(name.strip()) and name.strip() not in PRESETS}
        for name in list(self.video_checks):
            if name not in PRESETS and name not in names:
                self.remove_video(name)
        for name in sorted(names):
            self.add_video(name)

    def adb_serial(self):
        serial = self.serial.text().strip()
        if not serial:
            device = detected_device()
            serial = device[1] if device else ''
        if not serial or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', serial):
            raise ValueError(tr('Detecta o escribe la serie del LCD en «Brillo por ADB».'))
        return serial

    def upload_video(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr('Seleccionar video para el LCD'), '', tr('Videos MP4 (*.mp4)'), options=QFileDialog.Option.DontUseNativeDialog)
        if filename:
            self.begin_upload(Path(filename))

    def download_videos(self, category):
        if self.process is not None:
            return
        destination = QFileDialog.getExistingDirectory(self, tr('Carpeta donde guardar los videos del LCD'), options=QFileDialog.Option.DontUseNativeDialog)
        if destination:
            self.begin_download(category, Path(destination))

    def begin_download(self, category, destination):
        if self.process is not None:
            return
        sources = {'presets': '/sdcard/pcMediaPreset', 'custom': '/sdcard/pcMedia'}
        try:
            if category not in sources:
                raise ValueError(tr('Categoría de videos inválida.'))
            serial = self.adb_serial()
            destination = Path(destination).resolve(strict=True)
            if not destination.is_dir():
                raise ValueError(tr('El destino debe ser una carpeta.'))
            # A new directory for every download avoids overwriting local files.
            self.download_destination = Path(tempfile.mkdtemp(
                prefix=f'ryuo-{category}-', dir=destination))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Descargar videos'), str(error))
            return
        self.start_process('download', '/usr/bin/adb',
                           ['-s', serial, 'pull', sources[category], str(self.download_destination)])

    def begin_upload(self, source):
        if self.process is not None:
            return
        try:
            source = Path(source).resolve(strict=True)
            if not source.is_file() or source.suffix.lower() != '.mp4' or source.stat().st_size == 0:
                raise ValueError(tr('Selecciona un archivo MP4 no vacío.'))
            serial = self.adb_serial()
            stem = re.sub(r'[^a-zA-Z0-9_-]', '_', source.stem)[:32] or 'video'
            name = f'clip_{stem}_{uuid.uuid4().hex[:12]}.mp4'
            settings = self.collect_settings(media=[name])
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Video', str(error))
            return
        if not Path('/usr/local/bin/ryuo-control.py').exists():
            QMessageBox.information(self, tr('Instalación requerida'), tr('Primero ejecuta desde la carpeta del proyecto: sudo bash ./install.sh'))
            return
        self.upload_name = name
        self.upload_settings = settings
        self.upload_previous_media = [key for key, check in self.video_checks.items() if check.isChecked()]
        self.upload_source = source
        self.upload_serial = serial
        self.add_video(name)
        self.select_uploaded_video()
        self.start_process('upload_mkdir', '/usr/bin/adb',
                           ['-s', serial, 'shell', 'mkdir', '-p', '/sdcard/pcMedia'])

    def select_uploaded_video(self):
        # The new clip replaces the old playlist, avoiding the five-item limit.
        for key, check in self.video_checks.items():
            check.setChecked(key == self.upload_name)

    def restore_upload_selection(self):
        if self.upload_name in self.video_checks:
            self.remove_video(self.upload_name)
        for key, check in self.video_checks.items():
            check.setChecked(key in self.upload_previous_media)
        try:
            self.dirty = self.collect_settings() != load_config()
        except (ValueError, OSError):
            self.dirty = True

    def list_videos(self):
        try:
            serial = self.adb_serial()
        except ValueError as error:
            QMessageBox.warning(self, tr('Videos del LCD'), str(error))
            return
        self.start_process('list_videos', '/usr/bin/adb',
                           ['-s', serial, 'shell', 'ls', '-1', '/sdcard/pcMedia/'])

    def brightness_changed(self, value):
        self.brightness_value.setText(f'{value} %')
        self.changed()

    def select_slot(self, index):
        if not 0 <= index < SLOT_COUNT:
            return
        with QSignalBlocker(self.editor_slot), QSignalBlocker(self.editor_sensor):
            self.editor_slot.setCurrentIndex(index)
            self.editor_sensor.setCurrentIndex(self.editor_sensor.findData(self.sensor_assignments[index]))
        for card in self.slot_previews:
            card.highlight(card.slot == index)

    def edit_slot_sensor(self, *args):
        index = self.editor_slot.currentIndex()
        self.sensor_assignments[index] = self.editor_sensor.currentData()
        self.changed()
        self.update_slot_previews()

    def preview_value(self, sensor):
        return sensor_value(sensor, self.last_sensors, self.sensor_sources)

    def update_slot_previews(self):
        for index, preview in enumerate(self.slot_previews):
            sensor = self.sensor_assignments[index]
            title = html.escape(tr(SENSORS.get(sensor, 'Vacía')))
            value = html.escape(self.preview_value(sensor))
            preview.setAccessibleName(tr('Posición {0}: {1}', index + 1, tr(SENSORS.get(sensor, 'Vacía'))))
            preview.setText(f'<span style="color:{self.title_color};font-size:9px">{title}</span>'
                            f'<br/><b style="color:{self.content_color};font-size:16px">{value}</b>')
        self.select_slot(self.editor_slot.currentIndex())

    def swap_slots(self, first, second):
        if not (0 <= first < SLOT_COUNT and 0 <= second < SLOT_COUNT):
            return
        self.sensor_assignments[first], self.sensor_assignments[second] = (
            self.sensor_assignments[second], self.sensor_assignments[first])
        self.changed()
        self.update_slot_previews()
        self.select_slot(second)

    def clear_slots(self):
        self.sensor_assignments = [''] * SLOT_COUNT
        self.changed()
        self.update_slot_previews()

    def choose_color(self, target):
        color = QColorDialog.getColor(QColor(getattr(self, f'{target}_color')), self, options=QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if color.isValid():
            setattr(self, f'{target}_color', color.name())
            self.update_color_buttons()
            self.changed()

    def update_color_buttons(self):
        self.title_button.setText(tr('Títulos {0}', self.title_color))
        self.content_button.setText(tr('Datos {0}', self.content_color))
        self.update_slot_previews()

    def detect_serial(self):
        device = detected_device()
        if device:
            self.serial.setText(device[1])
            self.changed()
        else:
            QMessageBox.information(self, tr('Dispositivo'), tr('No se encontró un RYUO IV conectado.'))

    def reload_settings(self):
        try:
            config = load_config()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('Configuración'), str(error))
            return
        for name in config['media']:
            self.add_video(name)
        for preset, check in self.video_checks.items():
            check.setChecked(preset in config['media'])
        self.sensor_assignments = list(config['sensors'])
        self.update_slot_previews()
        self.play_mode.setCurrentIndex(self.play_mode.findData(config['play_mode']))
        self.cpu_mode.setCurrentIndex(self.cpu_mode.findData(config['cpu_temp_mode']))
        self.brightness.setValue(config['brightness'])
        self.serial.setText(config['adb_serial'])
        self.title_color = config['title_color']
        self.content_color = config['content_color']
        self.update_color_buttons()
        self.refresh_preview_choices()
        self.dirty = False
        self.notice.setText(tr('Configuración cargada de {0}', CONFIG_PATH) if CONFIG_PATH.exists() else tr('Valores iniciales. Aplica los cambios para guardarlos.'))

    def collect_settings(self, media=None):
        return validate_config({
            'media': ([key for key, check in self.video_checks.items() if check.isChecked()]
                      if media is None else media),
            'sensors': list(self.sensor_assignments),
            'play_mode': self.play_mode.currentData(),
            'cpu_temp_mode': self.cpu_mode.currentData(),
            'brightness': self.brightness.value(), 'adb_serial': self.serial.text().strip(),
            'title_color': self.title_color, 'content_color': self.content_color,
        })

    def apply_settings(self):
        try:
            settings = self.collect_settings()
        except ValueError as error:
            QMessageBox.warning(self, tr('Revisa la selección'), str(error))
            return
        self.run_action('apply', settings)

    def run_action(self, action, payload=None):
        if self.process is not None:
            return
        helper = Path('/usr/local/bin/ryuo-control.py')
        if not helper.exists():
            QMessageBox.information(self, tr('Instalación requerida'),
                                    tr('Primero ejecuta desde la carpeta del proyecto: sudo bash ./install.sh'))
            return
        self.start_process(action, '/usr/bin/pkexec', [str(helper), action], payload)

    def start_process(self, action, program, arguments, payload=None):
        if self.process is not None:
            return
        process = QProcess(self)
        self.process = process
        self.pending_action = action
        self.pending_settings = payload
        self.process_output = ''
        for button in self.action_buttons:
            button.setEnabled(False)
        self.language_choice.setEnabled(False)
        if action in ('upload_mkdir', 'upload'):
            for check in self.video_checks.values():
                check.setEnabled(False)
        self.notice.setText(tr('Procesando archivos del LCD…') if action.startswith('upload') or action in ('list_videos', 'refresh_after_delete', 'download', 'delete_video', 'preview_fetch')
                            else tr('Esperando autenticación / ejecutando acción…'))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.action_finished)
        process.errorOccurred.connect(self.process_error)
        def send_input():
            if payload is not None:
                process.write(json.dumps(payload).encode())
            process.closeWriteChannel()
        process.started.connect(send_input)
        process.start(program, arguments)

    def read_output(self):
        if self.process:
            text = bytes(self.process.readAllStandardOutput()).decode(errors='replace')
            self.process_output = (self.process_output + text)[-131072:]
            if text.strip():
                self.logs.insertPlainText(text.rstrip() + '\n')
                self.logs.ensureCursorVisible()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.logs.append(tr('No se pudo iniciar el comando. Comprueba que android-tools y polkit están instalados.'))
            self.action_finished(-1, QProcess.ExitStatus.CrashExit)

    def action_finished(self, exit_code, exit_status):
        self.read_output()
        if self.pending_action == 'preview_fetch':
            self.finish_preview_fetch(exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit)
            self.finish_process()
            self.notice.setText(tr('Operación de vista previa terminada.') + (tr(' Hay cambios sin aplicar.') if self.dirty else ''))
            return
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            if self.pending_action == 'upload_mkdir':
                self.finish_process()
                self.start_process('upload', '/usr/bin/adb',
                                   ['-s', self.upload_serial, 'push', str(self.upload_source),
                                    f'/sdcard/pcMedia/{self.upload_name}'])
                return
            if self.pending_action == 'upload':
                self.local_video_sources[self.upload_name] = self.upload_source
                self.select_uploaded_video()
                self.finish_process()
                self.start_process('apply_upload', '/usr/bin/pkexec',
                                   ['/usr/local/bin/ryuo-control.py', 'apply'], self.upload_settings)
                return
            elif self.pending_action in ('list_videos', 'refresh_after_delete'):
                self.sync_custom_videos(self.process_output)
                self.notice.setText(
                    (tr('Video borrado y lista actualizada.') if self.pending_action == 'refresh_after_delete'
                     else tr('Lista de videos actualizada.'))
                    + (tr(' Hay cambios sin aplicar.') if self.dirty else ''))
            elif self.pending_action == 'download':
                self.notice.setText(tr('Descarga completada en: {0}', self.download_destination))
            elif self.pending_action == 'delete_video':
                self.remove_video(self.delete_name)
                self.finish_process()
                self.start_process('refresh_after_delete', '/usr/bin/adb',
                                   ['-s', self.delete_serial, 'shell', 'ls', '-1', '/sdcard/pcMedia/'])
                return
            if self.pending_action in ('apply', 'apply_upload'):
                # Edits made during authentication remain marked as unsaved.
                try:
                    self.dirty = self.collect_settings() != self.pending_settings
                except ValueError:
                    self.dirty = True
            if self.pending_action not in ('upload', 'list_videos', 'refresh_after_delete', 'download', 'delete_video'):
                self.notice.setText((tr('Video subido, seleccionado y aplicado al LCD.') if self.pending_action == 'apply_upload'
                                     else tr('Acción completada. Consulta el registro para verificar el LCD.'))
                                    + (tr(' Hay cambios sin aplicar.') if self.dirty else ''))
        else:
            if self.pending_action in ('upload_mkdir', 'upload') and hasattr(self, 'upload_previous_media'):
                self.restore_upload_selection()
                self.notice.setText(tr('No se pudo subir el video. Se restauró la selección anterior. Revisa el registro.'))
            elif self.pending_action == 'apply_upload':
                self.notice.setText(tr('El video se subió y quedó seleccionado, pero no se pudo aplicar. Reintenta «Aplicar al LCD» sin volver a subirlo.'))
            elif self.pending_action == 'refresh_after_delete':
                self.notice.setText(tr('El video se borró, pero no se pudo recargar la lista. Reintenta «Leer videos del LCD».'))
            elif self.pending_action == 'download':
                self.notice.setText(tr('Descarga fallida. Puede haber archivos parciales en {0}. Revisa el registro.', self.download_destination))
            else:
                self.notice.setText(tr('Acción cancelada o fallida. Revisa el registro en «Servicio».'))
            self.pages.setCurrentIndex(2)
            self.nav.setCurrentRow(2)
        self.finish_process()

    def finish_process(self):
        if self.process:
            self.process.deleteLater()
        self.process = None
        for button in self.action_buttons:
            button.setEnabled(True)
        self.language_choice.setEnabled(True)
        for check in self.video_checks.values():
            check.setEnabled(True)

    def update_status(self, data):
        if 'sensors' in data:
            sensors = data['sensors']
            self.last_sensors = sensors
            self.sensor_sources = data.get('sensor_sources', {})
            cpu, gpu, memory = (sensors[k] for k in ('cpu', 'gpu', 'memory'))
            self.metrics['cpu'][0].setText(self.preview_value('CPU Temperature'))
            self.metrics['cpu'][1].setText(f"{cpu['usage']} % · {cpu['speedAverage']} MHz")
            self.metrics['gpu'][0].setText(self.preview_value('GPU Temperature'))
            self.metrics['gpu'][1].setText(f"{self.preview_value('GPU Load')} · {self.preview_value('GPU Power')}")
            self.metrics['memory'][0].setText(f"{memory['load']} %")
            self.metrics['memory'][1].setText(f"{memory['used'] / 2**30:.1f} / {memory['total'] / 2**30:.1f} GiB")
            self.history.add(cpu['usage'], gpu['usage'])
            self.update_slot_previews()
        if 'device' in data:
            device = data['device']
            self.device_label.setText(tr('USB conectado · {0}\nSerie: {1}', device[0], device[1]) if device else tr('RYUO IV no detectado por USB'))
        if 'service' in data:
            service = data['service']
            self.service_label.setText(tr('Servicio: {0} / {1}\nInicio al arrancar: {2}', service.get('ActiveState', tr('desconocido')), service.get('SubState', '—'), service.get('UnitFileState', tr('no instalado'))))
        if 'error' in data:
            self.service_label.setText(tr('Diagnóstico: {0}', data['error']))

    def closeEvent(self, event):
        if self.process is not None:
            QMessageBox.information(self, tr('Acción en curso'), tr('Espera a que finalice la operación.'))
            event.ignore()
            return
        if self.dirty and QMessageBox.question(
                self, tr('Cambios sin aplicar'), tr('¿Cerrar sin aplicar los cambios?'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        if self.monitor:
            self.monitor.requestInterruption()
            self.monitor.wait(6000)
        self.device_preview.stop_video()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('RYUO Control')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    if '--smoke-test' in sys.argv:
        def finish():
            window.grab().save('/tmp/ryuo-control-preview.png')
            window.close()
        QTimer.singleShot(3000, finish)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
