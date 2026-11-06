"""Shared, validated settings for the LCD service and Qt application."""
import copy
import json
import re
from pathlib import Path
from ryuo_i18n import tr

CONFIG_PATH = Path('/etc/ryuo-lcd.json')
PRESETS = [f'RYUO_IV_HW_Info_{i:02}.mp4' for i in range(1, 6)]
SLOT_COUNT = 6
SENSORS = {
    'CPU Temperature': 'Temperatura CPU',
    'GPU Temperature': 'Temperatura GPU',
    'CPU Speed Average': 'Frecuencia CPU',
    'CPU Load': 'Uso CPU',
    'GPU Load': 'Uso GPU',
    'Date&Time': 'Fecha y hora',
    'GPU Speed': 'Frecuencia GPU',
    'GPU Power': 'Potencia GPU',
}
DEFAULTS = {
    'media': PRESETS,
    'play_mode': 'Cycle',
    'brightness': 100,
    'adb_serial': '',
    'cpu_temp_mode': 'ccd_average',
    'sensors': list(SENSORS)[:5],
    'title_color': '#E5252B',
    'content_color': '#FFFFFF',
}

def valid_media_name(value):
    """Only basenames: custom clips must not use the device's preset prefix."""
    return (isinstance(value, str) and (value in PRESETS or (
        re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,74}\.mp4', value) is not None
        and not value.upper().startswith('RYUO'))))

def custom_video_path(name):
    """Deletion is restricted to safe custom MP4 basenames, never presets."""
    if not valid_media_name(name) or name in PRESETS:
        raise ValueError(tr('Solo se pueden borrar videos propios; los presets están protegidos.'))
    return f'/sdcard/pcMedia/{name}'


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError(tr('La configuración debe ser un objeto JSON.'))
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ValueError(tr('Campos desconocidos: {0}', ', '.join(sorted(unknown))))
    config = copy.deepcopy(DEFAULTS)
    config.update(data)
    media = config['media']
    if (not isinstance(media, list) or not media or len(media) > len(PRESETS)
            or any(not valid_media_name(value) for value in media)
            or len(media) != len(set(media))):
        raise ValueError(tr('Selección inválida: media'))
    sensors = config['sensors']
    if (not isinstance(sensors, list) or not 1 <= len(sensors) <= SLOT_COUNT
            or any(not isinstance(value, str) or (value != '' and value not in SENSORS)
                   for value in sensors)):
        raise ValueError(tr('La disposición debe contener de 1 a 6 sensores o posiciones vacías.'))
    # Migrate existing compact arrays without changing their slot indices.
    config['sensors'] = sensors + [''] * (SLOT_COUNT - len(sensors))
    if config['play_mode'] not in ('Single', 'Cycle', 'Random'):
        raise ValueError(tr('Modo de reproducción inválido.'))
    if type(config['brightness']) is not int or not 0 <= config['brightness'] <= 100:
        raise ValueError(tr('El brillo debe ser un entero entre 0 y 100.'))
    if config['cpu_temp_mode'] not in ('ccd_average', 'tctl'):
        raise ValueError(tr('Modo de temperatura CPU inválido.'))
    serial = config['adb_serial']
    if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{0,128}', serial):
        raise ValueError(tr('Número de serie ADB inválido.'))
    for key in ('title_color', 'content_color'):
        if not isinstance(config[key], str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', config[key]):
            raise ValueError(tr('Color inválido: {0}', key))
    if len(json.dumps(screen_config(config), separators=(',', ':')).encode()) > 740:
        raise ValueError(tr('La lista de videos es demasiado larga para el LCD. Selecciona menos videos.'))
    return config


def load_config(path=CONFIG_PATH):
    try:
        data = Path(path).read_text()
    except FileNotFoundError:
        return validate_config({})
    return validate_config(json.loads(data))


def screen_config(config):
    return {
        'id': 'Customization', 'screenMode': 'Full Screen', 'ratio': '2:1',
        'media': config['media'], 'playMode': config['play_mode'],
        'settings': {
            'titleColor': config['title_color'],
            'contentColor': config['content_color'],
            'filter': {'value': None, 'opacity': 100}, 'badges': [],
        },
        'sysinfoDisplay': config['sensors'],
    }
