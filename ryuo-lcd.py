#!/usr/bin/env python3
"""ASUS ROG RYUO IV LCD - Send sensor data via USB HID (hidapi)"""
import argparse
import csv
import math
import platform
import shutil
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json, time, struct, os, sys, subprocess
from pathlib import Path
from ryuo_config import load_config, screen_config

from ryuo_hid import connected_devices, load_backend, open_ryuo_device

def read_number(path, default=0):
    try:
        value = float(Path(path).read_text().strip())
        return value if math.isfinite(value) else default
    except (OSError, ValueError):
        return default

def cpu_temperature(hwmon_path):
    """Average readable CCD temperatures; fall back to Tctl without CCDs."""
    temperatures = []
    for label_path in Path(hwmon_path).glob('temp*_label'):
        try:
            if label_path.read_text().strip().startswith('Tccd'):
                input_path = label_path.with_name(
                    label_path.name.replace('_label', '_input'))
                temperatures.append(float(input_path.read_text().strip()) / 1000)
        except (OSError, ValueError):
            continue
    if temperatures:
        return round(sum(temperatures) / len(temperatures), 1)
    return read_number(Path(hwmon_path) / 'temp1_input') / 1000


def intel_temperature(hwmon_path):
    """Prefer the package reading; fall back to the hottest available core."""
    packages, cores = [], []
    for input_path in Path(hwmon_path).glob('temp*_input'):
        value = read_number(input_path, None)
        if value is None:
            continue
        try:
            label = input_path.with_name(input_path.name.replace('_input', '_label')).read_text().strip()
        except OSError:
            label = ''
        target = packages if label.startswith(('Package id', 'Physical id')) else cores
        target.append(value / 1000)
    values = packages or cores
    return max(values) if values else None


def read_nvidia_gpu(sys_root='/sys', report=None):
    """Read-only, bounded nvidia-smi query. Missing values remain unavailable."""
    report = report if report is not None else {}
    if not (Path(sys_root) / 'module/nvidia').exists():
        report['nvidia_status'] = 'NVIDIA kernel module not loaded'
        return None
    executable = shutil.which('nvidia-smi')
    if not executable:
        report['nvidia_status'] = 'nvidia-smi missing; install the matching NVIDIA driver tools'
        return None
    fields = 'index,temperature.gpu,utilization.gpu,power.draw,clocks.gr,memory.total'
    try:
        response = subprocess.run([executable, f'--query-gpu={fields}', '--format=csv,noheader,nounits'],
                                  capture_output=True, text=True, timeout=1,
                                  env={**os.environ, 'LC_ALL': 'C'}, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        report['nvidia_status'] = f'nvidia-smi failed: {getattr(error, "stderr", None) or str(error)}'
        return None

    def number(value):
        try:
            result = float(value.strip())
            return result if math.isfinite(result) else None
        except ValueError:
            return None

    candidates = []
    for row in csv.reader(response.stdout.splitlines(), skipinitialspace=True):
        if len(row) != 6:
            continue
        try:
            index = int(row[0])
        except ValueError:
            continue
        temperature, usage, power, speed, memory = map(number, row[1:])
        if all(value is None for value in (temperature, usage, power, speed)):
            continue
        candidates.append((memory or 0, -index, {
            'temperature': temperature, 'usage': usage, 'power': power, 'speed': speed,
        }))
    if not candidates:
        report['nvidia_status'] = 'nvidia-smi returned no usable measurements'
        return None
    _, index, gpu = max(candidates, key=lambda candidate: candidate[:2])
    report['nvidia_status'] = 'ok'
    report['nvidia_gpu_index'] = -index
    report['nvidia_unavailable'] = [key for key, value in gpu.items() if value is None]
    return gpu

def local_timezone(localtime='/etc/localtime', timezone='/etc/timezone'):
    candidates = []
    zone = os.environ.get('TZ', '').lstrip(':')
    if zone:
        candidates.append(zone.split('/zoneinfo/', 1)[-1])
    path = str(Path(localtime).resolve())
    if '/zoneinfo/' in path:
        candidates.append(path.split('/zoneinfo/', 1)[-1])
    try:
        candidates.append(Path(timezone).read_text().strip())
    except OSError:
        pass
    for candidate in candidates:
        try:
            ZoneInfo(candidate)
            return candidate
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return 'UTC'


def cpu_times(proc_root):
    fields = (Path(proc_root) / 'stat').read_text().splitlines()[0].split()
    if not fields or fields[0] != 'cpu' or len(fields) < 5:
        raise RuntimeError('Invalid /proc/stat: Linux procfs must be mounted')
    values = [int(value) for value in fields[1:9]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def cpu_frequency(sys_root, proc_root):
    root = Path(sys_root) / 'devices/system/cpu'
    for filename in ('scaling_cur_freq', 'cpuinfo_cur_freq'):
        frequencies = [read_number(path) / 1000 for path in root.glob(f'cpu[0-9]*/cpufreq/{filename}')]
        frequencies = [value for value in frequencies if value > 0]
        if frequencies:
            return int(sum(frequencies) / len(frequencies))
    try:
        frequencies = [float(line.split(':', 1)[1]) for line in
                       (Path(proc_root) / 'cpuinfo').read_text().splitlines()
                       if line.split(':', 1)[0].strip() == 'cpu MHz']
        return int(sum(frequencies) / len(frequencies)) if frequencies else 0
    except (OSError, ValueError):
        return 0


def get_sensors(cpu_temp_mode='ccd_average', *, sys_root='/sys', proc_root='/proc', sample_interval=.05, report=None):
    report = report if report is not None else {}
    report.update(cpu='unavailable', gpu='unavailable')
    cpu_temp = gpu_temp = 0.0
    gpu_fan = gpu_power = gpu_usage = gpu_speed = 0
    gpu_paths = []
    intel_temperatures = []
    amd_cpu_found = False
    for path in sorted((Path(sys_root) / 'class/hwmon').glob('*')):
        try:
            name = (path / 'name').read_text().strip()
        except OSError:
            continue
        if name == 'k10temp':
            amd_cpu_found = True
            report['cpu'] = 'k10temp'
            cpu_temp = (cpu_temperature(path) if cpu_temp_mode == 'ccd_average'
                        else read_number(path / 'temp1_input') / 1000)
        if name == 'coretemp':
            temperature = intel_temperature(path)
            if temperature is not None:
                intel_temperatures.append(temperature)
        if name == 'amdgpu':
            gpu_paths.append(path)
    if intel_temperatures and not amd_cpu_found:
        cpu_temp = max(intel_temperatures)
        report['cpu'] = 'coretemp'
    if gpu_paths:
        report['gpu'] = 'amdgpu'
        # Prefer a dedicated GPU by VRAM size; do not let a later iGPU overwrite it.
        path = max(gpu_paths, key=lambda p: (read_number(p / 'device/mem_info_vram_total'),
                                            read_number(p / 'device/boot_vga'), str(p)))
        measurements = {
            'temperature': read_number(path / 'temp1_input', None),
            'usage': read_number(path / 'device/gpu_busy_percent', None),
            'power': read_number(path / 'power1_average', read_number(path / 'power1_input', None)),
            'speed': read_number(path / 'freq1_input', None),
        }
        report['gpu_unavailable'] = [key for key, value in measurements.items() if value is None]
        gpu_temp = (measurements['temperature'] or 0) / 1000
        gpu_fan = int(read_number(path / 'fan1_input'))
        gpu_power = int((measurements['power'] or 0) / 1e6)
        gpu_usage = int(measurements['usage'] or 0)
        gpu_speed = int((measurements['speed'] or 0) / 1e6)

    nvidia = read_nvidia_gpu(sys_root, report)
    if nvidia is not None:
        report['gpu'] = 'nvidia-smi'
        report['gpu_unavailable'] = report['nvidia_unavailable']
        gpu_temp = nvidia['temperature'] if nvidia['temperature'] is not None else 0.0
        gpu_usage = max(0, min(100, int(nvidia['usage'] or 0)))
        gpu_power = max(0, int(nvidia['power'] or 0))
        gpu_speed = max(0, int(nvidia['speed'] or 0))
        # NVIDIA's fan.speed is a percentage, not the RPM field used by the LCD.
        gpu_fan = 0

    total1, idle1 = cpu_times(proc_root)
    time.sleep(sample_interval)
    total2, idle2 = cpu_times(proc_root)
    delta = total2 - total1
    cpu_usage = max(0, min(100, int(100 * (1 - (idle2 - idle1) / delta)))) if delta > 0 else 0
    freq = cpu_frequency(sys_root, proc_root)

    with open(Path(proc_root) / 'meminfo') as f:
        mi = {}
        for line in f:
            p = line.split(':')
            mi[p[0].strip()] = int(p[1].strip().split()[0])
    total_mem = mi['MemTotal'] * 1024
    available = mi.get('MemAvailable', sum(mi.get(key, 0) for key in ('MemFree', 'Buffers', 'Cached', 'SReclaimable')) - mi.get('Shmem', 0))
    used_mem = (mi['MemTotal'] - max(0, min(mi['MemTotal'], available))) * 1024

    return {
        "cpu": {"temperature": cpu_temp, "usage": cpu_usage, "load": cpu_usage,
                "power": 0, "voltage": 0.0, "speedAverage": freq, "fanAverage": 0},
        "gpu": {"temperature": str(gpu_temp), "usage": gpu_usage, "load": gpu_usage,
                "fan": gpu_fan, "power": gpu_power, "speed": gpu_speed, "voltage": 0.0},
        "memory": {"total": total_mem, "used": used_mem,
                   "load": int(used_mem*100/max(1,total_mem)), "speed": 0, "temperature": 0.0},
        "motherboard": {"temperature": 0.0},
        "network": {"download": 0, "upload": 0},
        "disk": {"total": 0, "used": 0, "load": 0, "activity": 0,
                 "readSpeed": 0, "writeSpeed": 0, "temperature": 0},
        "fans": [],
        "timeZone": local_timezone(),
        "timestamp": int(time.time() * 1000)
    }

def checksum(data):
    return sum(data) & 0xFF

def escape_bytes(data):
    r = bytearray()
    for b in data:
        if b == 0x5A: r.extend(b'\x5B\x01')
        elif b == 0x5B: r.extend(b'\x5B\x02')
        else: r.append(b)
    return bytes(r)

def build_packet(content_json, msg_id, cmd_type="all"):
    CR = "\r\n"
    header_str = f"POST {cmd_type} 1{CR}"
    header_str += f"ContentType=json{CR}"
    header_str += f"ContentLength={len(content_json.encode('utf-8'))}{CR}"
    header_str += f"AckNumber={msg_id}{CR}"
    header_str += f"Counter=0{CR}"
    header_str += f"Date=0{CR}"
    header_str += f"FileName=0{CR}"
    header_str += f"FileSize=0{CR}"
    header_str += f"SeqNumber=0{CR}"
    header_str += f"msgId=0{CR}"
    header_str += f"ContentRange=0{CR}"
    header_str += f"{CR}"
    header_str += content_json

    content = header_str.encode('utf-8')
    raw = struct.pack('>H', len(content) + 5) + content
    raw += struct.pack('B', checksum(raw))
    return b'\x5A' + escape_bytes(raw) + b'\x5A'

def send_packet(dev, packet):
    """Send a framed packet as HID report (report ID 0x00 + 1024 bytes)"""
    if len(packet) > 1024:
        raise ValueError(f'Packet exceeds HID capacity: {len(packet)} > 1024')
    report = bytes([0x00]) + packet + b'\x00' * (1024 - len(packet))
    written = dev.write(report)
    if written != len(report):
        raise OSError(f'Incomplete HID write: {written}/{len(report)}')

def send_screen_config(dev, msg_id, settings=None):
    """Send the persisted screen layout and preset playlist."""
    config = screen_config(settings if settings is not None else load_config())
    content = json.dumps(config, separators=(',', ':'))
    packet = build_packet(content, msg_id, cmd_type="waterBlockScreenId")
    send_packet(dev, packet)
    print(f"Sent ScreenConfig: {config['id']} | {', '.join(config['media'])}")

RYUO_SERIAL = os.environ.get('RYUO_ADB_SERIAL')
BACKLIGHT_PATH = "/sys/class/backlight/backlight/brightness"
BACKLIGHT_MAX = 256

def set_brightness_adb(level, serial=None):
    """Set LCD backlight via ADB sysfs (level 0-100 mapped to 0-256)"""
    serial = serial or RYUO_SERIAL
    if not serial:
        return
    value = int(BACKLIGHT_MAX * max(0, min(100, level)) / 100)
    try:
        subprocess.run(
            ["/usr/bin/adb", "-s", serial, "shell",
             f"echo {value} > {BACKLIGHT_PATH}"],
            timeout=5, capture_output=True, check=True
        )
        print(f"Set brightness: {level}% (backlight={value})")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Brightness error: {(e.stderr or b'').decode(errors='replace').strip() or str(e)}", file=sys.stderr)
    except Exception as e:
        print(f"Brightness error: {e}", file=sys.stderr)

def stop_lcd():
    """Set LCD brightness to 0 before shutdown"""
    print("Stopping LCD...")
    if set_brightness_adb(0, load_config()['adb_serial']):
        print("LCD backlight off.")
    else:
        print("LCD backlight unchanged: ADB brightness was not available.")

def diagnostics():
    result = {'python': sys.version, 'executable': sys.executable,
              'kernel': platform.release(), 'platform': sys.platform,
              'usb_devices': connected_devices(),
              'usb_access_tested': False, 'streaming_tested': False}
    try:
        module, api = load_backend()
        result['hid_backend'] = f'{module.__name__} ({api})'
    except (RuntimeError, ImportError, OSError) as error:
        result['hid_error'] = str(error)
    result['sensor_sources'] = {}
    result['sensors'] = get_sensors(load_config()['cpu_temp_mode'], report=result['sensor_sources'])
    return result


def main(once=False):
    print("Opening RYUO IV HID device...")
    dev = open_ryuo_device()
    try:
        print(f"Connected: {dev.get_manufacturer_string()} {dev.get_product_string()}")

        msg_id = 0

        # Send screen config FIRST before any sensor data
        settings = load_config()
        send_screen_config(dev, msg_id, settings)
        msg_id += 1
        time.sleep(1)

        # Apply persisted brightness, with the service environment as serial fallback.
        set_brightness_adb(settings['brightness'], settings['adb_serial'])
        time.sleep(1)

        while True:
            try:
                sensors = get_sensors(settings['cpu_temp_mode'])
                content = json.dumps(sensors, separators=(',', ':'))
                packet = build_packet(content, msg_id, cmd_type="all")
                send_packet(dev, packet)
                msg_id += 1
                if once:
                    print(json.dumps(sensors, indent=2))
                    break

                # Resend config every 60 packets to handle app restarts
                if msg_id % 60 == 0:
                    time.sleep(1)
                    send_screen_config(dev, msg_id, settings)
                    msg_id += 1

                if msg_id % 10 == 0:
                    print(f"Sent {msg_id} updates | CPU: {sensors['cpu']['temperature']}°C {sensors['cpu']['usage']}% | GPU: {sensors['gpu']['temperature']}°C {sensors['gpu']['usage']}%")
                time.sleep(1)
            except KeyboardInterrupt:
                break
            except Exception as e:
                raise RuntimeError(f'HID update failed: {e}') from e

    finally:
        dev.close()
    print("Done.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnose', action='store_true', help='Report runtime, USB identity and sensors without USB writes')
    parser.add_argument('--sensors', action='store_true', help='Print sensors without opening USB')
    parser.add_argument('--once', action='store_true', help='Send one update and exit')
    parser.add_argument('--stop', action='store_true', help='Turn off backlight (requires RYUO_ADB_SERIAL)')
    args = parser.parse_args()
    if args.diagnose:
        print(json.dumps(diagnostics(), indent=2))
    elif args.sensors:
        print(json.dumps(get_sensors(load_config()['cpu_temp_mode']), indent=2))
    elif args.stop:
        stop_lcd()
    else:
        try:
            main(once=args.once)
        except (OSError, RuntimeError, ValueError) as error:
            print(f'Error: {error}', file=sys.stderr)
            sys.exit(1)
