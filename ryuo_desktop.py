"""Desktop utilities shared by the Qt6 and GTK4 frontends; no toolkit imports."""
import hashlib
import importlib.util
import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from ryuo_config import load_config, valid_media_name, validate_config
from ryuo_hid import connected_devices
from ryuo_i18n import tr


def detected_device():
    devices = connected_devices()
    return (devices[0]['product'], devices[0]['serial']) if devices else None


def preview_cache_path(serial, name, root=None):
    if not valid_media_name(name) or not serial:
        raise ValueError(tr('Se requiere un video y la serie del AIO para la caché.'))
    base = (Path(root) if root is not None else
            Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'ryuo-control' / 'videos')
    return base / hashlib.sha256(serial.encode()).hexdigest()[:16] / name


def read_status():
    # Lazy import keeps file/configuration helpers independent of sensor access.
    spec = importlib.util.spec_from_file_location('ryuo_desktop_driver', Path(__file__).with_name('ryuo-lcd.py'))
    module = globals().get('_driver')
    if module is None:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_driver'] = module
    result = {'sensor_sources': {}}
    try:
        result['sensors'] = module.get_sensors(load_config()['cpu_temp_mode'], report=result['sensor_sources'])
        result['device'] = detected_device()
        status = subprocess.run(['/usr/bin/systemctl', 'show', 'ryuo-lcd.service',
                                 '--property=ActiveState,SubState,UnitFileState'],
                                capture_output=True, text=True, timeout=4)
        if status.returncode:
            raise RuntimeError(status.stderr.strip() or tr('No se puede consultar systemd'))
        result['service'] = dict(line.split('=', 1) for line in status.stdout.splitlines() if '=' in line)
    except Exception as error:
        result['error'] = str(error)
    return result


def sensor_value(sensor, readings, sources):
    if not sensor:
        return '＋'
    if sensor == 'Date&Time':
        return datetime.now().strftime('%H:%M')
    if not readings:
        return '—'
    if sensor == 'CPU Temperature' and sources.get('cpu') == 'unavailable':
        return '—'
    field = {'GPU Temperature': 'temperature', 'GPU Load': 'usage',
             'GPU Speed': 'speed', 'GPU Power': 'power'}.get(sensor)
    if field and (sources.get('gpu') == 'unavailable' or field in sources.get('gpu_unavailable', [])):
        return '—'
    cpu, gpu = readings['cpu'], readings['gpu']
    return {
        'CPU Temperature': f"{float(cpu['temperature']):.1f} °C",
        'GPU Temperature': f"{float(gpu['temperature']):.1f} °C",
        'CPU Speed Average': f"{cpu['speedAverage']} MHz",
        'CPU Load': f"{cpu['usage']} %", 'GPU Load': f"{gpu['usage']} %",
        'GPU Speed': f"{gpu['speed']} MHz", 'GPU Power': f"{gpu['power']} W",
    }.get(sensor, '—')


def validated_serial(value):
    serial = value.strip()
    if not serial:
        device = detected_device()
        serial = device[1] if device else ''
    if not serial or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', serial):
        raise ValueError(tr('Detecta o escribe la serie del LCD en «Brillo por ADB».'))
    return serial


def upload_plan(source, settings):
    source = Path(source).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != '.mp4' or source.stat().st_size == 0:
        raise ValueError(tr('Selecciona un archivo MP4 no vacío.'))
    stem = re.sub(r'[^a-zA-Z0-9_-]', '_', source.stem)[:32] or 'video'
    name = f'clip_{stem}_{uuid.uuid4().hex[:12]}.mp4'
    config = validate_config({**settings, 'media': [name]})
    return source, name, config


def quad_matrix(points):
    """Column-major 4x4 homography mapping the unit square onto four corners."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
    dx1, dx2, dx3 = x1-x2, x3-x2, x0-x1+x2-x3
    dy1, dy2, dy3 = y1-y2, y3-y2, y0-y1+y2-y3
    denominator = dx1*dy2-dx2*dy1
    if abs(denominator) < 1e-9:
        raise ValueError('Degenerate preview surface')
    g = (dx3*dy2-dx2*dy3)/denominator
    h = (dx1*dy3-dx3*dy1)/denominator
    return [x1-x0+g*x1, y1-y0+g*y1, 0, g,
            x3-x0+h*x3, y3-y0+h*y3, 0, h,
            0, 0, 1, 0, x0, y0, 0, 1]


def triangle_matrix(source, destination):
    """Cairo affine transform mapping three source points onto three targets."""
    (sx, sy), (sx1, sy1), (sx2, sy2) = source
    (dx, dy), (dx1, dy1), (dx2, dy2) = destination
    a, b, c, d = sx1-sx, sx2-sx, sy1-sy, sy2-sy
    determinant = a*d-b*c
    if abs(determinant) < 1e-9:
        raise ValueError('Degenerate preview triangle')
    xx = ((dx1-dx)*d-(dx2-dx)*c)/determinant
    xy = (-(dx1-dx)*b+(dx2-dx)*a)/determinant
    yx = ((dy1-dy)*d-(dy2-dy)*c)/determinant
    yy = (-(dy1-dy)*b+(dy2-dy)*a)/determinant
    return xx, yx, xy, yy, dx-xx*sx-xy*sy, dy-yx*sx-yy*sy
