#!/usr/bin/python3
"""Small privileged helper. Install root-owned; invoke through pkexec."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ryuo_config import CONFIG_PATH, validate_config

SERVICE = 'ryuo-lcd.service'


def write_config(config, path=CONFIG_PATH):
    """Atomically replace settings so the service never sees partial JSON."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent,
                                         prefix='.ryuo-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(validate_config(config), stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Este ayudante requiere autenticación administrativa.')
    if len(sys.argv) != 2:
        raise ValueError('Se requiere una acción.')
    action = sys.argv[1]
    if action == 'apply':
        payload = sys.stdin.read(16385)
        if len(payload) > 16384:
            raise ValueError('Configuración demasiado grande.')
        config = validate_config(json.loads(payload))
        write_config(config)
        restorecon = shutil.which('restorecon')
        if restorecon:
            subprocess.run([restorecon, str(CONFIG_PATH)], check=True)
        print('Configuración guardada. Reiniciando servicio…', flush=True)
        subprocess.run(['/usr/bin/systemctl', 'restart', SERVICE], check=True, timeout=45)
    elif action in ('start', 'stop', 'restart', 'enable', 'disable'):
        subprocess.run(['/usr/bin/systemctl', action, SERVICE], check=True, timeout=45)
    elif action == 'logs':
        subprocess.run(['/usr/bin/journalctl', '-u', SERVICE, '-n', '60',
                        '--no-pager', '--output=short'], check=True, timeout=10)
    else:
        raise ValueError('Acción no permitida.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
