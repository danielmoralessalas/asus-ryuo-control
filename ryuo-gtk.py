#!/usr/bin/python3
"""RYUO Control — GTK4 desktop frontend for the shared Linux LCD service."""
import configparser
import json
import os
import sys
import tempfile
import threading
import time

import cairo
import uuid
from collections import deque
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk

from ryuo_config import CONFIG_PATH, PRESETS, SENSORS, SLOT_COUNT, custom_video_path, load_config, valid_media_name, validate_config
from ryuo_desktop import detected_device, preview_cache_path, quad_matrix, read_status, sensor_value, triangle_matrix, upload_plan, validated_serial
from ryuo_i18n import retranslate, set_language, tr

STYLE = '''
window.ryuo, .ryuo { background: #11151c; color: #e8edf5; font-family: "Noto Sans"; font-size: 13px; }
.ryuo .sidebar { background: #0c1016; padding: 28px 16px 20px; }
.ryuo .brand { color: #7de0d3; font-size: 25px; font-weight: 800; }
.ryuo .heading { font-size: 28px; font-weight: 700; }
.ryuo .muted { color: #99a6b9; }
.ryuo .metric { font-size: 32px; font-weight: 700; }
.ryuo .card { background: #1a222e; border-radius: 12px; padding: 20px 18px; }
.ryuo .group { border: 1px solid #303a49; border-radius: 12px; padding: 16px; }
.ryuo .group-title { font-weight: 600; }
.ryuo button { background: #253142; color: #e8edf5; border: 1px solid #3b4a60; border-radius: 7px; padding: 9px 14px; box-shadow: none; }
.ryuo button:hover { background: #34465c; }
.ryuo button:disabled { color: #697587; background: #1a222e; }
.ryuo button.primary { background: #7de0d3; color: #0c2427; border: none; font-weight: 700; }
.ryuo .navigation button { padding: 15px 12px; border: none; background: transparent; }
.ryuo .navigation button:checked { background: #20343a; color: #7de0d3; }
.ryuo entry, .ryuo combobox button, .ryuo textview text { background: #1a222e; color: #e8edf5; }
.ryuo scale highlight { background: #7de0d3; }
.ryuo .sensor { background: alpha(#080c18, .86); border: 2px solid #4c5369; padding: 5px; border-radius: 9px; }
.ryuo .sensor.selected, .ryuo .sensor:focus { border-color: #7de0d3; }
'''
ANCHORS = ((.25, .43), (.24, .61), (.79, .52), (.79, .66), (.78, .82), (.55, .76))


def box(vertical=True, spacing=12):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)


def margins(widget, amount):
    for side in ('top', 'bottom', 'start', 'end'):
        getattr(widget, 'set_margin_' + side)(amount)
    return widget


def label(text='', style=None):
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_wrap(True)
    if style:
        widget.add_css_class(style)
    return widget


def combo(items):
    widget = Gtk.ComboBoxText()
    for key, text in items:
        widget.append(key, text)
    widget.set_active(0)
    return widget


class Preferences:
    """Use the same language preference file as QSettings, without importing Qt."""
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'RYUOControl/RYUOControl.conf'

    def language(self):
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(self.path)
            language = parser.get('General', 'language', fallback='en')
            return language if language in ('en', 'es') else 'en'
        except (OSError, configparser.Error):
            return 'en'

    def save(self, language):
        parser = configparser.ConfigParser(interpolation=None)
        if self.path.exists():
            with self.path.open() as stream:
                parser.read_file(stream)
        if not parser.has_section('General'):
            parser.add_section('General')
        parser.set('General', 'language', language)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                parser.write(stream)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class History(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.cpu, self.gpu = deque(maxlen=60), deque(maxlen=60)
        self.set_content_height(120)
        self.set_draw_func(self.draw)

    def add(self, cpu, gpu):
        self.cpu.append(cpu)
        self.gpu.append(gpu)
        self.queue_draw()

    def draw(self, widget, cr, width, height):
        cr.set_source_rgb(.17, .21, .27)
        for fraction in (0, .5, 1):
            y = 8 + fraction*(height-16)
            cr.move_to(0, y)
            cr.line_to(width, y)
        cr.stroke()
        for values, color in ((self.cpu, (.49, .88, .83)), (self.gpu, (.96, .71, .42))):
            cr.set_source_rgb(*color)
            cr.set_line_width(2)
            for index, value in enumerate(values):
                x, y = index*width/59, height-8-max(0, min(100, value))*(height-16)/100
                (cr.move_to if index == 0 else cr.line_to)(x, y)
            cr.stroke()


class VideoSurface(Gtk.Widget):
    def __init__(self):
        super().__init__()
        path = Path(__file__).with_name('assets') / 'ryuo-screen.png'
        if not path.exists():
            path = Path('/usr/local/share/ryuo-control/ryuo-screen.png')
        self.texture = Gdk.Texture.new_from_filename(str(path)) if path.exists() else None
        self.media = None
        self.frame = None
        self.last_frame_at = 0

    def receive_frame(self, media):
        if time.monotonic() - self.last_frame_at < 1/24:
            return
        width, height = media.get_intrinsic_width(), media.get_intrinsic_height()
        native = self.get_native()
        if width <= 0 or height <= 0 or native is None:
            return
        scale = min(1, 720/width)
        width, height = max(1, int(width*scale)), max(1, int(height*scale))
        snapshot = Gtk.Snapshot()
        media.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is None:
            return
        texture = native.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
        pixbuf = Gdk.pixbuf_get_from_texture(texture)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        context = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
        context.paint()
        self.frame = surface
        self.last_frame_at = time.monotonic()
        self.queue_draw()

    def image_rect(self):
        width, height = self.get_width(), self.get_height()
        h = min(max(1, height-12), max(1, width-20)*1026/1100)
        w = h*1100/1026
        return (width-w)/2, (height-h)/2, w, h

    def do_snapshot(self, snapshot):
        x, y, width, height = self.image_rect()
        if self.texture:
            snapshot.append_texture(self.texture, Graphene.Rect().init(x, y, width, height))
        if self.frame is None:
            return
        # A small affine mesh approximates the same homography on both GPU and
        # software renderers. Cairo renderers cannot draw projective GSK nodes.
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, self.get_width(), self.get_height()))
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        split = 1606/2240
        fw, fh = self.frame.get_width(), self.frame.get_height()
        for start, end, corners in (
            (0, split, ((190, 280), (740, 485), (710, 930), (165, 705))),
            (split, 1, ((740, 485), (980, 440), (935, 825), (710, 930))),
        ):
            points = [(x+px*width/1100, y+py*height/1026) for px, py in corners]
            m = quad_matrix(points)
            def project(u, v):
                denominator = m[3]*u+m[7]*v+1
                return ((m[0]*u+m[4]*v+m[12])/denominator,
                        (m[1]*u+m[5]*v+m[13])/denominator)
            for column in range(8):
                for row in range(4):
                    uv = [(column/8, row/4), ((column+1)/8, row/4),
                          ((column+1)/8, (row+1)/4), (column/8, (row+1)/4)]
                    for indices in ((0, 1, 2), (0, 2, 3)):
                        source = [((start+uv[i][0]*(end-start))*fw, uv[i][1]*fh) for i in indices]
                        destination = [project(*uv[i]) for i in indices]
                        cr.save()
                        cr.move_to(*destination[0])
                        cr.line_to(*destination[1])
                        cr.line_to(*destination[2])
                        cr.close_path()
                        cr.clip()
                        cr.transform(cairo.Matrix(*triangle_matrix(source, destination)))
                        cr.set_source_surface(self.frame, 0, 0)
                        cr.paint()
                        cr.restore()


class DevicePreview(Gtk.Overlay):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.token = uuid.uuid4().hex
        self.set_size_request(540, 440)
        self.surface = VideoSurface()
        self.set_child(self.surface)
        self.cards = []
        self.media = None
        self.current_path = None
        self.last_size = None
        for index in range(SLOT_COUNT):
            card = Gtk.Button()
            card.add_css_class('sensor')
            card.set_halign(Gtk.Align.START)
            card.set_valign(Gtk.Align.START)
            content = Gtk.Label()
            content.set_justify(Gtk.Justification.CENTER)
            content.set_wrap(True)
            card.set_child(content)
            card.connect('clicked', lambda button, i=index: owner.select_slot(i))
            card.set_tooltip_text(tr('Haz clic para elegir el sensor. Arrastra a otro sensor para intercambiarlos. Enter también selecciona esta posición.'))
            source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            source.connect('prepare', lambda source, x, y, i=index: Gdk.ContentProvider.new_for_value(f'{self.token}:{i}'))
            card.add_controller(source)
            target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
            target.connect('drop', self.drop_slot, index)
            card.add_controller(target)
            self.add_overlay(card)
            self.cards.append(card)
        self.add_tick_callback(self.layout_cards)

    def drop_slot(self, target, value, x, y, destination):
        parts = str(value).split(':')
        if len(parts) != 2 or parts[0] != self.token or not parts[1].isdigit():
            return False
        source = int(parts[1])
        if not 0 <= source < SLOT_COUNT or source == destination:
            return False
        self.owner.swap_slots(source, destination)
        return True

    def layout_cards(self, widget, clock):
        size = (self.get_width(), self.get_height())
        if size != self.last_size:
            self.last_size = size
            x, y, width, height = self.surface.image_rect()
            cw, ch = max(96, min(145, int(width*.22))), max(52, min(68, int(height*.13)))
            for card, (ax, ay) in zip(self.cards, ANCHORS):
                card.set_size_request(cw, ch)
                card.set_margin_start(max(0, int(x+ax*width-cw/2)))
                card.set_margin_top(max(0, int(y+ay*height-ch/2)))
        return GLib.SOURCE_CONTINUE

    def stop(self):
        if self.media:
            self.media.pause()
            self.media.clear()
        self.surface.media = self.media = None
        self.surface.frame = None
        self.current_path = None
        self.surface.queue_draw()

    def play(self, path):
        path = Path(path).resolve()
        if path != self.current_path:
            self.stop()
            self.current_path = path
            self.media = Gtk.MediaFile.new_for_filename(str(path))
            self.media.set_muted(True)
            self.media.set_loop(True)
            self.media.connect('invalidate-contents', self.surface.receive_frame)
            self.media.connect('notify::error', self.media_error)
            self.surface.media = self.media
        self.media.play()

    def media_error(self, media, prop):
        error = media.get_error()
        if error:
            self.owner.preview_status.set_label(tr('No se puede reproducir localmente: {0}', error.message))


class Window(Gtk.ApplicationWindow):
    def __init__(self, application, monitor=True, preferences=None, cache_root=None, language=None):
        super().__init__(application=application, title='RYUO Control')
        self.set_default_size(1100, 850)
        self.add_css_class('ryuo')
        self.preferences = preferences or Preferences()
        self.language = language or self.preferences.language()
        set_language(self.language)
        self.cache_root = cache_root
        self.process = None
        self.dirty = False
        self.loading = True
        self.closed = False
        self.stop_event = threading.Event()
        self.last_sensors, self.sensor_sources = None, {}
        self.last_status = None
        self.local_video_sources = {}
        self.video_checks, self.video_rows = {}, {}
        self.sensor_assignments = ['']*SLOT_COUNT
        self.title_color, self.content_color = '#E5252B', '#FFFFFF'
        self.translatables = []
        self.choices = []
        self.actions = []
        outer = box(False, 0)
        outer.add_css_class('ryuo')
        self.set_child(outer)
        sidebar = box()
        sidebar.add_css_class('sidebar')
        sidebar.set_size_request(205, -1)
        outer.append(sidebar)
        sidebar.append(label('RYUO\nCONTROL', 'brand'))
        sidebar.append(label('Linux · GTK4', 'muted'))
        sidebar.append(self.text('Idioma', 'muted'))
        self.language_choice = combo([('en', 'English'), ('es', 'Español')])
        self.language_choice.set_active_id(self.language)
        self.language_choice.connect('changed', self.change_language)
        sidebar.append(self.language_choice)
        navigation = box()
        navigation.add_css_class('navigation')
        navigation.set_vexpand(True)
        sidebar.append(navigation)
        self.pages = Gtk.Stack()
        self.pages.set_hexpand(True)
        self.pages.set_vexpand(True)
        self.nav = []
        for name, title, build in [('overview', 'Resumen', self.overview_page),
                                   ('display', 'Pantalla', self.screen_page),
                                   ('service', 'Servicio', self.service_page)]:
            self.pages.add_named(build(), name)
            button = Gtk.ToggleButton(label=tr(title))
            self.translatables.append((button.set_label, title))
            if self.nav:
                button.set_group(self.nav[0])
            button.connect('toggled', lambda button, n=name: self.pages.set_visible_child_name(n) if button.get_active() else None)
            navigation.append(button)
            self.nav.append(button)
        self.nav[0].set_active(True)
        sidebar.append(self.text('ROG RYUO IV\nControl independiente', 'muted'))
        body = margins(box(), 24)
        body.set_hexpand(True)
        outer.append(body)
        body.append(self.pages)
        self.notice = label(tr('Listo. Los cambios se envían al pulsar «Aplicar al LCD».'), 'muted')
        body.append(self.notice)
        self.reload_settings()
        self.connect('close-request', self.close_request)
        if monitor:
            threading.Thread(target=self.monitor_loop, daemon=True).start()

    def text(self, source, style=None):
        widget = label(tr(source), style)
        self.translatables.append((widget.set_label, source))
        return widget

    def button(self, source, callback, action=False):
        widget = Gtk.Button(label=tr(source))
        widget.connect('clicked', lambda _: callback())
        self.translatables.append((widget.set_label, source))
        if action:
            self.actions.append(widget)
        return widget

    def choice(self, items, callback=None):
        widget = combo([(key, tr(title)) for key, title in items])
        self.choices.append((widget, items))
        if callback:
            widget.connect('changed', callback)
        return widget

    def group(self, source, parent):
        frame = box()
        frame.add_css_class('group')
        frame.append(self.text(source, 'group-title'))
        parent.append(frame)
        return frame

    def overview_page(self):
        page = box()
        page.append(self.text('Tu equipo, en vivo', 'heading'))
        page.append(self.text('Sensores locales · actualización cada 2 segundos', 'muted'))
        cards = box(False)
        page.append(cards)
        self.metrics = {}
        for key, title in [('cpu', 'CPU · temperatura'), ('gpu', 'GPU · temperatura'), ('memory', 'Memoria · uso')]:
            card = box()
            card.add_css_class('card')
            card.set_hexpand(True)
            card.append(self.text(title, 'muted'))
            value, detail = label('—', 'metric'), self.text('Esperando sensores…', 'muted')
            card.append(value)
            card.append(detail)
            self.metrics[key] = value, detail
            cards.append(card)
        graph = self.group('Uso · últimas 60 muestras · escala 0–100 %', page)
        graph.append(self.text('CPU: turquesa     GPU: naranja', 'muted'))
        self.history = History()
        graph.append(self.history)
        connection = self.group('Conexión', page)
        self.device_label = label(tr('Buscando RYUO IV…'))
        self.service_label = label(tr('Consultando servicio…'), 'muted')
        connection.append(self.device_label)
        connection.append(self.service_label)
        connection.append(self.text('La pantalla sigue funcionando al cerrar esta ventana si el servicio está activo.', 'muted'))
        return page

    def screen_page(self):
        page = box()
        content = box()
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(content)
        scroll.set_vexpand(True)
        page.append(scroll)
        self.screen_content = content
        content.append(self.text('Diseña tu pantalla', 'heading'))
        content.append(self.text('Videos preinstalados y datos que se muestran en el LCD.', 'muted'))
        preview = self.group('ROG RYUO IV SLC 360 ARGB · editor de pantalla', content)
        preview.append(self.text('Arrastra un sensor sobre otro para intercambiarlos. Haz clic en uno para cambiar qué muestra.', 'muted'))
        row = box(False)
        self.preview_choice = Gtk.ComboBoxText()
        self.preview_choice.set_hexpand(True)
        self.preview_choice.connect('changed', self.preview_changed)
        row.append(self.preview_choice)
        self.preview_play = self.button('Reproducir vista previa', self.start_preview, True)
        row.append(self.preview_play)
        row.append(self.button('Actualizar copia', lambda: self.start_preview(True), True))
        preview.append(row)
        self.preview_status = label(tr('Vista previa local del video elegido · sin sincronización con el AIO.'), 'muted')
        preview.append(self.preview_status)
        self.device_preview = DevicePreview(self)
        preview.append(self.device_preview)
        row = box(False)
        self.editor_slot = combo([(str(i), tr('Posición {0:02}', i+1)) for i in range(SLOT_COUNT)])
        self.editor_slot.connect('changed', lambda widget: self.select_slot(widget.get_active()))
        self.editor_sensor = self.choice([('', 'Vacía · ocultar'), *SENSORS.items()], self.edit_slot)
        self.editor_sensor.set_hexpand(True)
        row.append(self.editor_slot)
        row.append(self.editor_sensor)
        row.append(self.button('Ocultar todos', self.clear_slots))
        preview.append(row)
        preview.append(self.text('Imagen de referencia ASUS; perspectiva aproximada. Los cambios se envían al pulsar «Aplicar al LCD».', 'muted'))
        videos = self.group('Videos del dispositivo', content)
        self.video_list = box()
        videos.append(self.video_list)
        for index, name in enumerate(PRESETS, 1):
            self.add_video(name, f'Preset {index:02}  ·  {name}')
        for title, callback in [('Subir y mostrar video MP4…', self.upload_video),
                                ('Leer videos del LCD', self.list_videos),
                                ('Descargar presets…', lambda: self.download_videos('presets')),
                                ('Descargar videos propios…', lambda: self.download_videos('custom'))]:
            videos.append(self.button(title, callback, True))
        self.play_mode = self.choice([('Cycle', 'En secuencia'), ('Random', 'Aleatorio'), ('Single', 'Individual')], self.changed)
        videos.append(self.play_mode)
        videos.append(self.text('Máximo 5 videos seleccionados. Para archivos propios: MP4 H.264 hasta 1920×1080 o HEVC hvc1 a 2240×1080. Se copian sin convertir.', 'muted'))
        settings = self.group('Lecturas y apariencia', content)
        settings.append(self.text('Temperatura CPU'))
        self.cpu_mode = self.choice([('ccd_average', 'AMD: promedio CCD · Intel: paquete'), ('tctl', 'AMD: Tctl · Intel: paquete')], self.changed)
        settings.append(self.cpu_mode)
        row = box(False)
        self.title_button = Gtk.Button()
        self.content_button = Gtk.Button()
        self.title_button.connect('clicked', lambda _: self.choose_color('title'))
        self.content_button.connect('clicked', lambda _: self.choose_color('content'))
        row.append(self.title_button)
        row.append(self.content_button)
        settings.append(row)
        brightness = self.group('Brillo por ADB', content)
        row = box(False)
        self.brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.brightness.set_draw_value(False)
        self.brightness.set_hexpand(True)
        self.brightness_value = label('100 %')
        self.brightness.connect('value-changed', self.brightness_changed)
        row.append(self.brightness)
        row.append(self.brightness_value)
        brightness.append(row)
        brightness.append(self.text('Serie del LCD'))
        row = box(False)
        self.serial = Gtk.Entry()
        self.serial.set_hexpand(True)
        self.serial.set_placeholder_text(tr('Vacío: usa la variable del servicio, si existe'))
        self.translatables.append((self.serial.set_placeholder_text, 'Vacío: usa la variable del servicio, si existe'))
        self.serial.connect('changed', self.changed)
        row.append(self.serial)
        row.append(self.button('Detectar serie USB', self.detect_serial))
        brightness.append(row)
        brightness.append(self.text('Requiere ADB autorizado. El resultado se comprueba en el registro del servicio.', 'muted'))
        row = box(False)
        row.append(self.button('Recargar guardado', self.reload_settings, True))
        spacer = box()
        spacer.set_hexpand(True)
        row.append(spacer)
        apply = self.button('Aplicar al LCD', self.apply_settings, True)
        apply.add_css_class('primary')
        row.append(apply)
        page.append(row)
        return page

    def service_page(self):
        page = box()
        page.append(self.text('Servicio y diagnóstico', 'heading'))
        page.append(self.text('El servicio mantiene el LCD actualizado en segundo plano.', 'muted'))
        for actions in ([('Iniciar', 'start'), ('Detener', 'stop'), ('Reiniciar', 'restart')],
                        [('Activar al arrancar', 'enable'), ('Desactivar al arrancar', 'disable'), ('Leer registro', 'logs')]):
            row = box(False)
            for title, action in actions:
                row.append(self.button(title, lambda a=action: self.run_action(a), True))
            page.append(row)
        self.logs = Gtk.TextView(editable=False, monospace=True)
        self.logs.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self.logs)
        page.append(scroll)
        page.append(self.text('Pulsa «Leer registro» para consultar los últimos eventos.\nLas acciones administrativas abren la autenticación del sistema.', 'muted'))
        page.append(self.text('Control del LCD: videos, sensores y brillo. Las curvas de bomba y ventiladores se configuran por separado en BIOS. No se actualiza firmware.', 'muted'))
        return page

    def changed(self, *args):
        if not self.loading:
            self.dirty = True
            if hasattr(self, 'notice'):
                self.notice.set_label(tr('Hay cambios sin aplicar.'))

    def change_language(self, widget):
        language = widget.get_active_id()
        if self.process or language == self.language:
            return
        self.language = language
        set_language(language)
        self.loading = True
        playback_label = self.preview_play.get_label()
        for setter, source in self.translatables:
            setter(tr(source))
        for choice, items in self.choices:
            active = choice.get_active_id()
            choice.remove_all()
            for key, source in items:
                choice.append(key, tr(source))
            choice.set_active_id(active or '')
        index = self.editor_slot.get_active()
        self.editor_slot.remove_all()
        for i in range(SLOT_COUNT):
            self.editor_slot.append(str(i), tr('Posición {0:02}', i+1))
        self.editor_slot.set_active(index)
        self.preview_status.set_label(retranslate(self.preview_status.get_label()))
        self.preview_play.set_label(retranslate(playback_label))
        self.loading = False
        self.update_slots()
        if self.last_status:
            self.update_status(self.last_status, history=False)
        try:
            self.preferences.save(language)
            message = tr('Idioma actualizado.')
        except (OSError, configparser.Error):
            message = tr('No se pudo guardar el idioma.')
        self.notice.set_label(message + (tr(' Hay cambios sin aplicar.') if self.dirty else ''))

    def add_video(self, name, title=None):
        if name in self.video_checks or not valid_media_name(name):
            return
        row = box(False)
        check = Gtk.CheckButton(label=title or (name if len(name) <= 42 else name[:24]+'…'+name[-16:]))
        check.set_hexpand(True)
        check.set_tooltip_text(name)
        check.connect('toggled', self.video_changed)
        row.append(check)
        if name not in PRESETS:
            delete = self.button('Borrar…', lambda n=name: self.delete_video(n))
            row.append(delete)
        self.video_checks[name] = check
        self.video_rows[name] = row
        self.video_list.append(row)

    def remove_video(self, name):
        if name in PRESETS or name not in self.video_checks:
            return
        selected = self.video_checks.pop(name).get_active()
        row = self.video_rows.pop(name)
        # Drop translation callbacks referencing deleted widgets.
        children = []
        child = row.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        self.translatables = [(setter, source) for setter, source in self.translatables
                              if getattr(setter, '__self__', None) not in children]
        self.video_list.remove(row)
        if selected:
            if not any(check.get_active() for check in self.video_checks.values()):
                self.video_checks[PRESETS[0]].set_active(True)
            self.changed()
        self.refresh_choices()

    def sync_videos(self, output):
        names = {line.strip() for line in output.splitlines() if valid_media_name(line.strip()) and line.strip() not in PRESETS}
        for name in list(self.video_checks):
            if name not in PRESETS and name not in names:
                self.remove_video(name)
        for name in sorted(names):
            self.add_video(name)

    def selected_media(self):
        return [name for name, check in self.video_checks.items() if check.get_active()]

    def video_changed(self, *args):
        self.changed()
        self.refresh_choices()

    def refresh_choices(self):
        if not hasattr(self, 'preview_choice'):
            return
        current = self.preview_choice.get_active_id()
        self.preview_choice.handler_block_by_func(self.preview_changed)
        self.preview_choice.remove_all()
        selected = self.selected_media()
        for name in selected:
            self.preview_choice.append(name, name)
        self.preview_choice.set_active_id(current if current in selected else selected[0] if selected else '')
        self.preview_choice.handler_unblock_by_func(self.preview_changed)
        if current != self.preview_choice.get_active_id():
            self.preview_changed()

    def preview_changed(self, *args):
        if hasattr(self, 'device_preview'):
            self.device_preview.stop()
            self.preview_play.set_label(tr('Reproducir vista previa'))
            self.preview_status.set_label(tr('Pulsa reproducir para ver el video seleccionado con los sensores. La copia se descarga solo si hace falta.'))

    def play_file(self, path):
        self.device_preview.play(path)
        self.preview_play.set_label(tr('Pausar vista previa'))
        self.preview_status.set_label(tr('Vista previa local · {0} · en bucle y sin audio. No cambia el LCD.', self.preview_choice.get_active_id()))

    def start_preview(self, force=False):
        if self.process:
            return
        name = self.preview_choice.get_active_id()
        if not name:
            self.preview_status.set_label(tr('Selecciona al menos un video de la lista para previsualizarlo.'))
            return
        media = self.device_preview.media
        if media and not force:
            if media.get_playing():
                media.pause()
                self.preview_play.set_label(tr('Reanudar vista previa'))
                self.preview_status.set_label(tr('Video pausado. Los sensores siguen actualizándose.'))
            else:
                media.play()
                self.preview_play.set_label(tr('Pausar vista previa'))
            return
        local = self.local_video_sources.get(name)
        if not force and local and local.is_file():
            self.play_file(local)
            return
        try:
            serial = validated_serial(self.serial.get_text())
            target = preview_cache_path(serial, name, self.cache_root)
            if not force and target.is_file() and target.stat().st_size:
                self.play_file(target)
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f'.{name}.{uuid.uuid4().hex}.part')
            self.preview_fetch = name, target, temporary
        except (ValueError, OSError) as error:
            self.preview_status.set_label(tr('No se pudo preparar la vista previa: {0}', error))
            return
        folder = 'pcMediaPreset' if name in PRESETS else 'pcMedia'
        self.preview_status.set_label(tr('Descargando una copia de {0} para vista previa…', name))
        self.start_process('preview_fetch', ['/usr/bin/adb', '-s', serial, 'pull', f'/sdcard/{folder}/{name}', str(temporary)])

    def finish_preview_fetch(self, success):
        name, target, temporary = self.preview_fetch
        try:
            if not success or not temporary.is_file() or not temporary.stat().st_size:
                raise OSError(tr('ADB no pudo descargar una copia completa. Revisa el registro y la conexión.'))
            temporary.replace(target)
            if self.preview_choice.get_active_id() == name:
                self.play_file(target)
        except OSError as error:
            self.preview_status.set_label(tr('Vista previa no disponible: {0}', error))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def select_slot(self, index):
        if self.loading or not 0 <= index < SLOT_COUNT:
            return
        self.loading = True
        self.editor_slot.set_active(index)
        self.editor_sensor.set_active_id(self.sensor_assignments[index])
        self.loading = False
        for i, card in enumerate(self.device_preview.cards):
            (card.add_css_class if i == index else card.remove_css_class)('selected')

    def edit_slot(self, *args):
        if self.loading:
            return
        index = self.editor_slot.get_active()
        if 0 <= index < SLOT_COUNT:
            self.sensor_assignments[index] = self.editor_sensor.get_active_id() or ''
            self.changed()
            self.update_slots()

    def update_slots(self):
        for index, card in enumerate(self.device_preview.cards):
            sensor = self.sensor_assignments[index]
            title = GLib.markup_escape_text(tr(SENSORS.get(sensor, 'Vacía')))
            value = GLib.markup_escape_text(sensor_value(sensor, self.last_sensors, self.sensor_sources))
            card.get_child().set_markup(f'<span foreground="{self.title_color}" size="small">{title}</span>\n<span foreground="{self.content_color}" size="large" weight="bold">{value}</span>')
        self.title_button.set_label(tr('Títulos {0}', self.title_color))
        self.content_button.set_label(tr('Datos {0}', self.content_color))
        self.select_slot(max(0, self.editor_slot.get_active()))

    def swap_slots(self, first, second):
        if not (0 <= first < SLOT_COUNT and 0 <= second < SLOT_COUNT):
            return
        self.sensor_assignments[first], self.sensor_assignments[second] = self.sensor_assignments[second], self.sensor_assignments[first]
        self.changed()
        self.update_slots()
        self.select_slot(second)

    def clear_slots(self):
        self.sensor_assignments = ['']*SLOT_COUNT
        self.changed()
        self.update_slots()

    def brightness_changed(self, widget):
        self.brightness_value.set_label(f'{int(widget.get_value())} %')
        self.changed()

    def detect_serial(self):
        device = detected_device()
        if device:
            self.serial.set_text(device[1])
        else:
            self.message(tr('Dispositivo'), tr('No se encontró un RYUO IV conectado.'))

    def choose_color(self, target):
        dialog = Gtk.ColorChooserDialog(title=tr('Colores'), transient_for=self, modal=True)
        color = Gdk.RGBA()
        color.parse(getattr(self, target+'_color'))
        dialog.set_rgba(color)
        dialog.set_use_alpha(False)
        def response(dialog, response):
            if response == Gtk.ResponseType.OK:
                color = dialog.get_rgba()
                value = '#' + ''.join(f'{round(channel*255):02X}' for channel in (color.red, color.green, color.blue))
                setattr(self, target+'_color', value)
                self.changed()
                self.update_slots()
            dialog.destroy()
        dialog.connect('response', response)
        dialog.present()

    def reload_settings(self):
        try:
            config = load_config()
        except (ValueError, OSError) as error:
            self.loading = False
            self.message(tr('Configuración'), str(error))
            return
        self.loading = True
        for name in config['media']:
            self.add_video(name)
        for name, check in self.video_checks.items():
            check.set_active(name in config['media'])
        self.sensor_assignments = list(config['sensors'])
        self.play_mode.set_active_id(config['play_mode'])
        self.cpu_mode.set_active_id(config['cpu_temp_mode'])
        self.brightness.set_value(config['brightness'])
        self.serial.set_text(config['adb_serial'])
        self.title_color, self.content_color = config['title_color'], config['content_color']
        self.loading = False
        self.update_slots()
        self.refresh_choices()
        self.dirty = False
        self.notice.set_label(tr('Configuración cargada de {0}', CONFIG_PATH) if CONFIG_PATH.exists() else tr('Valores iniciales. Aplica los cambios para guardarlos.'))

    def collect_settings(self, media=None):
        return validate_config({'media': self.selected_media() if media is None else media,
            'sensors': list(self.sensor_assignments), 'play_mode': self.play_mode.get_active_id(),
            'cpu_temp_mode': self.cpu_mode.get_active_id(), 'brightness': int(self.brightness.get_value()),
            'adb_serial': self.serial.get_text().strip(), 'title_color': self.title_color, 'content_color': self.content_color})

    def apply_settings(self):
        try:
            settings = self.collect_settings()
        except ValueError as error:
            self.message(tr('Revisa la selección'), str(error))
            return
        self.run_action('apply', settings)

    def helper_available(self):
        if Path('/usr/local/bin/ryuo-control.py').exists():
            return True
        self.message(tr('Instalación requerida'), tr('Primero ejecuta desde la carpeta del proyecto: sudo bash ./install.sh'))
        return False

    def run_action(self, action, payload=None):
        if not self.process and self.helper_available():
            self.start_process(action, ['/usr/bin/pkexec', '/usr/local/bin/ryuo-control.py', action], payload)

    def file_chooser(self, title, directory, callback):
        if self.process:
            return
        dialog = Gtk.FileChooserNative.new(title, self,
            Gtk.FileChooserAction.SELECT_FOLDER if directory else Gtk.FileChooserAction.OPEN,
            tr('Seleccionar'), tr('Cancelar'))
        if not directory:
            filter = Gtk.FileFilter()
            filter.set_name('MP4')
            filter.add_pattern('*.mp4')
            filter.add_pattern('*.MP4')
            dialog.add_filter(filter)
        def response(dialog, response):
            file = dialog.get_file()
            path = file.get_path() if file else None
            dialog.destroy()
            self.file_dialog = None
            if response == Gtk.ResponseType.ACCEPT and path:
                callback(Path(path))
        dialog.connect('response', response)
        self.file_dialog = dialog
        dialog.show()

    def upload_video(self):
        self.file_chooser(tr('Seleccionar video para el LCD'), False, self.begin_upload)

    def begin_upload(self, source):
        if self.process:
            return
        try:
            serial = validated_serial(self.serial.get_text())
            # Validate the resulting single-video playlist, even if current checks exceed the limit.
            source, name, config = upload_plan(source, self.collect_settings(media=[PRESETS[0]]))
        except (OSError, ValueError) as error:
            self.message('Video', str(error))
            return
        if not self.helper_available():
            return
        self.upload = {'source': source, 'name': name, 'settings': config,
                       'serial': serial, 'previous': self.selected_media()}
        self.add_video(name)
        for key, check in self.video_checks.items():
            check.set_active(key == name)
        self.start_process('upload_mkdir', ['/usr/bin/adb', '-s', serial, 'shell', 'mkdir', '-p', '/sdcard/pcMedia'])

    def download_videos(self, category):
        self.file_chooser(tr('Carpeta donde guardar los videos del LCD'), True,
                          lambda destination: self.begin_download(category, destination))

    def begin_download(self, category, destination):
        if self.process:
            return
        try:
            sources = {'presets': '/sdcard/pcMediaPreset', 'custom': '/sdcard/pcMedia'}
            if category not in sources:
                raise ValueError(tr('Categoría de videos inválida.'))
            serial = validated_serial(self.serial.get_text())
            destination = Path(destination).resolve(strict=True)
            if not destination.is_dir():
                raise ValueError(tr('El destino debe ser una carpeta.'))
            self.download_destination = Path(tempfile.mkdtemp(prefix=f'ryuo-{category}-', dir=destination))
        except (OSError, ValueError) as error:
            self.message(tr('Descargar videos'), str(error))
            return
        self.start_process('download', ['/usr/bin/adb', '-s', serial, 'pull', sources[category], str(self.download_destination)])

    def list_videos(self):
        try:
            serial = validated_serial(self.serial.get_text())
        except ValueError as error:
            self.message(tr('Videos del LCD'), str(error))
            return
        self.start_process('list_videos', ['/usr/bin/adb', '-s', serial, 'shell', 'ls', '-1', '/sdcard/pcMedia/'])

    def delete_video(self, name):
        if self.process:
            return
        try:
            remote = custom_video_path(name)
            if name not in self.video_checks:
                raise ValueError(tr('Primero lee la lista de videos del LCD.'))
            if name in load_config()['media']:
                raise ValueError(tr('Este video está en la lista guardada. Desmárcalo, selecciona otro video y pulsa «Aplicar al LCD» antes de borrarlo.'))
            serial = validated_serial(self.serial.get_text())
        except (ValueError, OSError) as error:
            self.message(tr('Borrar video'), str(error))
            return
        def confirmed():
            self.delete_name, self.delete_serial = name, serial
            self.start_process('delete_video', ['/usr/bin/adb', '-s', serial, 'shell', 'rm', '--', remote])
        self.message(tr('Borrar video del AIO'), tr('¿Borrar definitivamente este video del AIO?\n\n{0}', name)
                     + '\n\n' + tr('Esta acción no se puede deshacer. Los presets no se borrarán.'), confirmed)

    def message(self, title, text, confirm=None):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, text=title,
                                   secondary_text=text, buttons=Gtk.ButtonsType.NONE)
        dialog.add_button(tr('No') if confirm else tr('Aceptar'), Gtk.ResponseType.NO)
        if confirm:
            dialog.add_button(tr('Sí'), Gtk.ResponseType.YES)
        dialog.set_default_response(Gtk.ResponseType.NO)
        def response(dialog, response):
            dialog.destroy()
            if confirm and response == Gtk.ResponseType.YES:
                confirm()
        dialog.connect('response', response)
        dialog.present()

    def set_busy(self, busy):
        self.screen_content.set_sensitive(not busy)
        self.language_choice.set_sensitive(not busy)
        for widget in self.actions:
            widget.set_sensitive(not busy)

    def append_log(self, text):
        buffer = self.logs.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        if buffer.get_line_count() > 2000:
            success, end = buffer.get_iter_at_line(buffer.get_line_count()-2000)
            if success:
                buffer.delete(buffer.get_start_iter(), end)
        self.logs.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 1)

    def start_process(self, action, command, payload=None):
        if self.process:
            return
        self.set_busy(True)
        self.notice.set_label(tr('Esperando autenticación / ejecutando acción…'))
        try:
            process = Gio.Subprocess.new(command, Gio.SubprocessFlags.STDIN_PIPE |
                                         Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
            self.process = process
            process.communicate_utf8_async(json.dumps(payload) if payload is not None else None,
                                           None, self.process_finished, (action, payload))
        except GLib.Error as error:
            self.append_log(str(error)+'\n')
            self.process = None
            self.set_busy(False)
            self.complete_action(action, False, '', payload)

    def process_finished(self, process, result, context):
        action, payload = context
        try:
            _, output, _ = process.communicate_utf8_finish(result)
            output = (output or '')[-131072:]
            success = process.get_successful()
        except GLib.Error as error:
            output, success = str(error), False
        self.process = None
        self.set_busy(False)
        self.append_log(output+'\n' if output else '')
        self.complete_action(action, success, output, payload)

    def complete_action(self, action, success, output, payload=None):
        if action == 'preview_fetch':
            self.finish_preview_fetch(success)
            self.notice.set_label(tr('Operación de vista previa terminada.'))
            return
        if success:
            if action == 'upload_mkdir':
                info = self.upload
                self.start_process('upload', ['/usr/bin/adb', '-s', info['serial'], 'push', str(info['source']), f"/sdcard/pcMedia/{info['name']}"])
                return
            if action == 'upload':
                info = self.upload
                self.local_video_sources[info['name']] = info['source']
                self.start_process('apply_upload', ['/usr/bin/pkexec', '/usr/local/bin/ryuo-control.py', 'apply'], info['settings'])
                return
            if action == 'delete_video':
                self.remove_video(self.delete_name)
                self.start_process('refresh_after_delete', ['/usr/bin/adb', '-s', self.delete_serial, 'shell', 'ls', '-1', '/sdcard/pcMedia/'])
                return
            if action in ('list_videos', 'refresh_after_delete'):
                self.sync_videos(output)
                message = tr('Video borrado y lista actualizada.') if action == 'refresh_after_delete' else tr('Lista de videos actualizada.')
            elif action in ('apply', 'apply_upload'):
                try:
                    self.dirty = self.collect_settings() != payload
                except ValueError:
                    self.dirty = True
                message = tr('Video subido, seleccionado y aplicado al LCD.') if action == 'apply_upload' else tr('Acción completada. Consulta el registro para verificar el LCD.')
            elif action == 'download':
                message = tr('Descarga completada en: {0}', self.download_destination)
            else:
                message = tr('Acción completada. Consulta el registro para verificar el LCD.')
        else:
            if action in ('upload_mkdir', 'upload'):
                self.remove_video(self.upload['name'])
                for name, check in self.video_checks.items():
                    check.set_active(name in self.upload['previous'])
                try:
                    self.dirty = self.collect_settings() != load_config()
                except (ValueError, OSError):
                    self.dirty = True
                message = tr('No se pudo subir el video. Se restauró la selección anterior. Revisa el registro.')
            elif action == 'apply_upload':
                message = tr('El video se subió y quedó seleccionado, pero no se pudo aplicar. Reintenta «Aplicar al LCD» sin volver a subirlo.')
            elif action == 'refresh_after_delete':
                message = tr('El video se borró, pero no se pudo recargar la lista. Reintenta «Leer videos del LCD».')
            elif action == 'download':
                message = tr('Descarga fallida. Puede haber archivos parciales en {0}. Revisa el registro.', self.download_destination)
            else:
                message = tr('Acción cancelada o fallida. Revisa el registro en «Servicio».')
            self.nav[2].set_active(True)
        self.notice.set_label(message + (tr(' Hay cambios sin aplicar.') if self.dirty else ''))

    def monitor_loop(self):
        while not self.stop_event.is_set():
            data = read_status()
            if self.stop_event.is_set():
                break
            GLib.idle_add(self.update_status, data)
            if self.stop_event.wait(2):
                break

    def update_status(self, data, history=True):
        if self.closed:
            return GLib.SOURCE_REMOVE
        self.last_status = data
        if 'sensors' in data:
            self.last_sensors, self.sensor_sources = data['sensors'], data.get('sensor_sources', {})
            cpu, gpu, memory = (self.last_sensors[key] for key in ('cpu', 'gpu', 'memory'))
            self.metrics['cpu'][0].set_label(sensor_value('CPU Temperature', self.last_sensors, self.sensor_sources))
            self.metrics['cpu'][1].set_label(f"{cpu['usage']} % · {cpu['speedAverage']} MHz")
            self.metrics['gpu'][0].set_label(sensor_value('GPU Temperature', self.last_sensors, self.sensor_sources))
            self.metrics['gpu'][1].set_label(sensor_value('GPU Load', self.last_sensors, self.sensor_sources)+' · '+sensor_value('GPU Power', self.last_sensors, self.sensor_sources))
            self.metrics['memory'][0].set_label(f"{memory['load']} %")
            self.metrics['memory'][1].set_label(f"{memory['used']/2**30:.1f} / {memory['total']/2**30:.1f} GiB")
            if history:
                self.history.add(cpu['usage'], gpu['usage'])
            self.update_slots()
        if 'device' in data:
            device = data['device']
            self.device_label.set_label(tr('USB conectado · {0}\nSerie: {1}', *device) if device else tr('RYUO IV no detectado por USB'))
        if 'service' in data:
            service = data['service']
            self.service_label.set_label(tr('Servicio: {0} / {1}\nInicio al arrancar: {2}', service.get('ActiveState', tr('desconocido')), service.get('SubState', '—'), service.get('UnitFileState', tr('no instalado'))))
        if 'error' in data:
            self.service_label.set_label(tr('Diagnóstico: {0}', data['error']))
        return GLib.SOURCE_REMOVE

    def close_request(self, *args):
        if self.process:
            self.message(tr('Acción en curso'), tr('Espera a que finalice la operación.'))
            return True
        if getattr(self, 'file_dialog', None):
            self.file_dialog.destroy()
            self.file_dialog = None
        if self.dirty:
            self.message(tr('Cambios sin aplicar'), tr('¿Cerrar sin aplicar los cambios?'), self.shutdown)
            return True
        self.shutdown()
        return True

    def shutdown(self):
        self.closed = True
        self.stop_event.set()
        self.device_preview.stop()
        self.destroy()


def main():
    smoke = '--smoke-test' in sys.argv
    app = Gtk.Application(application_id='io.github.ryuocontrol.Gtk', flags=Gio.ApplicationFlags.NON_UNIQUE)
    def activate(app):
        provider = Gtk.CssProvider()
        provider.load_from_data(STYLE.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window = Window(app, monitor=not smoke)
        window.present()
        if smoke:
            window.nav[1].set_active(True)
            def finish():
                # Render only our widget tree, never capture other windows.
                paintable = Gtk.WidgetPaintable.new(window.get_child())
                snapshot = Gtk.Snapshot()
                paintable.snapshot(snapshot, window.get_width(), window.get_height())
                node = snapshot.to_node()
                if node:
                    texture = window.get_renderer().render_texture(node, None)
                    texture.save_to_png('/tmp/ryuo-control-gtk-preview.png')
                window.shutdown()
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(1500, finish)
    app.connect('activate', activate)
    return app.run([sys.argv[0]])


if __name__ == '__main__':
    sys.exit(main())
