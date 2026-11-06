"""Normalize Linux HID Python bindings without opening USB during import."""
import importlib
from pathlib import Path

PRODUCT_ID = 0x1C76
VENDOR_IDS = (0x0B05, 0x1C75)


def connected_devices(root='/sys/bus/usb/devices'):
    devices = []
    for path in sorted(Path(root).glob('*')):
        try:
            vendor = int((path / 'idVendor').read_text().strip(), 16)
            product = int((path / 'idProduct').read_text().strip(), 16)
            if vendor not in VENDOR_IDS or product != PRODUCT_ID:
                continue
        except (OSError, ValueError):
            continue
        def text(name, default=''):
            try:
                return (path / name).read_text().strip()
            except OSError:
                return default
        devices.append({'vendor_id': vendor, 'product_id': product,
                        'product': text('product', 'ROG RYUO IV'),
                        'serial': text('serial'), 'sysfs_path': str(path)})
    return devices


def load_backend():
    errors = []
    for name in ('hid', 'hidapi'):
        try:
            module = importlib.import_module(name)
        except (ImportError, OSError) as error:
            errors.append(f'{name}: {error}')
            continue
        if callable(getattr(module, 'device', None)):
            return module, 'cython'
        if name == 'hid' and callable(getattr(module, 'Device', None)):
            return module, 'ctypes'
        functions = ('hid_init', 'hid_open', 'hid_write', 'hid_close',
                     'hid_set_nonblocking', 'hid_get_manufacturer_string',
                     'hid_get_product_string')
        if all(callable(getattr(module, function, None)) for function in functions):
            module.hid_init()
            return module, 'legacy'
        errors.append(f'{name}: unsupported Python HID API')
    raise RuntimeError('No usable HID binding. Rerun the installer for your distribution. '
                       + '; '.join(errors))


class HidDevice:
    def __init__(self, module, api):
        self.module = module
        self.api = api
        self.handle = None

    def open(self, vendor, product):
        try:
            if self.api == 'cython':
                self.handle = self.module.device()
                self.handle.open(vendor, product)
            elif self.api == 'ctypes':
                self.handle = self.module.Device(vid=vendor, pid=product)
            else:
                self.handle = self.module.hid_open(vendor, product)
                if not self.handle:
                    self.handle = None
                    raise OSError('HID open returned no handle')
        except Exception as error:
            self.close()
            raise OSError(f'Cannot open {vendor:04x}:{product:04x}: {error}') from error

    def set_nonblocking(self, flag):
        if self.api == 'cython':
            return self.handle.set_nonblocking(flag)
        if self.api == 'ctypes':
            self.handle.nonblocking = bool(flag)
        else:
            self.module.hid_set_nonblocking(self.handle, flag)

    def write(self, data):
        if self.api == 'legacy':
            return self.module.hid_write(self.handle, bytearray(data))
        return self.handle.write(bytes(data))

    def get_manufacturer_string(self):
        if self.api == 'legacy':
            return self.module.hid_get_manufacturer_string(self.handle)
        if self.api == 'ctypes':
            return self.handle.manufacturer
        return self.handle.get_manufacturer_string()

    def get_product_string(self):
        if self.api == 'legacy':
            return self.module.hid_get_product_string(self.handle)
        if self.api == 'ctypes':
            return self.handle.product
        return self.handle.get_product_string()

    def close(self):
        handle, self.handle = self.handle, None
        if handle is not None:
            if self.api == 'legacy':
                self.module.hid_close(handle)
            else:
                handle.close()


def open_ryuo_device():
    module, api = load_backend()
    detected = connected_devices()
    vendors = list(dict.fromkeys(device['vendor_id'] for device in detected)) or VENDOR_IDS
    errors = []
    for vendor in vendors:
        device = HidDevice(module, api)
        try:
            device.open(vendor, PRODUCT_ID)
            device.set_nonblocking(1)
            return device
        except Exception as error:
            device.close()
            errors.append(str(error))
    raise OSError('Unable to access the RYUO IV. Check USB, permissions and the system service. '
                  + '; '.join(errors))
