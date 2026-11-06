# RYUO Control — Python con Qt6 o GTK4 para Linux

**Español** | [English](README.en.md)

Aplicación de escritorio independiente para controlar el LCD de la ASUS ROG
RYUO IV. Ofrece interfaces PySide6 (Qt6) y PyGObject (GTK4) que comparten el
controlador USB HID de este proyecto, obtenido
mediante ingeniería inversa de ASUS InfoHub y del software Android de la pantalla.

Esta primera versión implementa el control del LCD. No tiene todavía todas las
funciones de InfoHub: no convierte videos automáticamente, no actualiza firmware,
no modifica curvas de bomba o ventiladores y no muestra miniaturas de los videos.

## Diferencias respecto a la versión original

La versión original se centraba en instalar el driver para enviar datos al LCD.
Esta versión conserva ese controlador y amplía el proyecto con **RYUO Control**,
una aplicación de escritorio para configurar la pantalla, gestionar sus videos
y administrar el servicio desde Linux.

| Área | Cambios de esta versión |
| --- | --- |
| Interfaz gráfica | Interfaces Python con PySide6/Qt6 o PyGObject/GTK4, disponibles en inglés y español; inglés por defecto y selección de idioma persistente por usuario. |
| Disposición de sensores | Vista interactiva sobre la imagen del AIO, con seis posiciones, intercambio al arrastrar, posiciones vacías y colores configurables. Las posiciones corresponden a las admitidas por el LCD; no son coordenadas libres. |
| Vista previa | Reproducción local del video de fondo con sensores superpuestos sobre la representación de la pantalla curva. Es una simulación, no una captura del LCD real. |
| Videos | Selección de presets y modos de reproducción; subida y aplicación de MP4 propios en una operación, descarga de archivos del AIO y borrado de videos propios con presets protegidos. |
| Brillo | Ajuste entre 0 y 100 % mediante ADB, con selección o detección de la serie USB. |
| Sensores y hardware | Promedio de CCD o Tctl en CPU AMD, temperatura de paquete en Intel y lectura de GPU AMD/NVIDIA según los controladores y herramientas disponibles. |
| Configuración y servicio | Ajustes persistentes y validados en `/etc/ryuo-lcd.json`; controles para iniciar, detener, reiniciar, configurar el arranque automático y consultar registros, con autenticación mediante polkit. |
| Instalación | Un único `install.sh` detecta la familia de distribución e instala las dependencias del driver y de la interfaz elegida con `--qt6` o `--gtk4`, incluido ADB. Contempla Fedora/Nobara, Bazzite/Fedora Atomic, Arch, Ubuntu y SUSE con las condiciones indicadas más abajo. |
| Desinstalación y diagnóstico | Desinstalador con simulación y purga opcional de la configuración del sistema; diagnóstico de sensores, backend HID y detección USB. |

El driver sigue funcionando como servicio independiente: cerrar la interfaz no
interrumpe el envío de sensores. Las funciones de archivos y brillo requieren
acceso ADB al AIO, además de la comunicación HID que utiliza el driver.
Esta ampliación se limita al LCD y todavía no cubre todas las funciones de ASUS
InfoHub. El soporte entre distribuciones no implica que todas las combinaciones
de hardware y sistema se hayan probado físicamente; consulta las limitaciones y
comprobaciones descritas en este documento.

## Funciones

- Panel de CPU, GPU AMD/NVIDIA y memoria en vivo, con historial de uso.
- Temperatura CPU AMD: promedio de CCD o Tctl; Intel: paquete vía coretemp.
- Selección de los cinco videos preinstalados; secuencia, aleatorio o individual.
- Subida de MP4 propios y lectura de la lista de videos del LCD mediante ADB.
- Descarga de las carpetas de presets y videos propios desde la interfaz.
- Borrado con confirmación de videos propios; presets protegidos.
- Asignación de sensores a seis posiciones, con huecos e intercambio de posiciones.
- Colores de títulos y contenido.
- Vista previa local de video con sensores superpuestos, reproducción y pausa.
- Brillo 0–100 % mediante ADB autorizado.
- Inicio, parada, reinicio, arranque automático y registros del servicio.
- Configuración persistente en `/etc/ryuo-lcd.json`, validada y escrita de forma atómica.

## Instalar o actualizar

Ejecuta el instalador único desde el directorio del proyecto:

```bash
sudo bash ./install.sh --qt6   # Qt6: opción predeterminada
sudo bash ./install.sh --gtk4  # GTK4: alternativa con la misma configuración
```

Ejecuta solo el comando de la interfaz que quieras instalar. Sin ninguna opción
se selecciona Qt6; `--qt6` y `--gtk4` son excluyentes. Para cambiar de interfaz,
vuelve a ejecutar el instalador con la otra opción. El acceso **RYUO Control**
abrirá la interfaz seleccionada; los ajustes del LCD y del servicio se conservan.
Los paquetes compartidos o instalados previamente no se desinstalan al cambiar.

`install.sh` necesita la carpeta `scripts/`, los archivos Python y los recursos
del proyecto; no es un archivo autónomo que pueda copiarse por separado.

Detecta la distribución usando `ID` e `ID_LIKE` de `/etc/os-release` y selecciona
las dependencias y el gestor de paquetes correspondientes:

| Familia | Gestor | Dependencias Python |
| --- | --- | --- |
| Fedora, Nobara y derivados mutables | DNF | Paquetes del sistema |
| Bazzite, Fedora Atomic con rpm-ostree | rpm-ostree | Paquetes del host, con fase de reinicio si faltan |
| Arch, EndeavourOS, Manjaro, Garuda, CachyOS | Pacman | Paquetes del sistema |
| Ubuntu 22.04+, Mint, Pop!_OS, KDE neon, Zorin y derivados recientes | APT | Entorno Python aislado |
| openSUSE Leap 16+, Tumbleweed, Slowroll y derivados SUSE mutables | Zypper | Entorno Python aislado |

Los instaladores requieren systemd en el host y Python 3.10 o posterior. Los
repositorios del sistema deben estar habilitados y disponibles. El soporte de
sensores cubre Ryzen/Intel y GPU AMD/NVIDIA según los controladores disponibles;
la instalación del controlador gráfico NVIDIA depende de la distribución. Versiones antiguas o arquitecturas sin wheels compatibles pueden
necesitar una adaptación; el instalador se detiene en lugar de compilar Qt.
SLE depende de que sus repositorios/suscripciones proporcionen las dependencias.
MicroOS/Aeon/Kalpa transaccionales y derivados sin systemd no están cubiertos.

Para revisar el plan sin modificar paquetes, archivos ni servicios:

```bash
bash ./install.sh --gtk4 --dry-run
# Revisar el plan para otra familia:
bash ./install.sh --gtk4 --dry-run --family=arch
```

`--family` acepta `auto` (predeterminado), `fedora`, `bazzite`, `arch`, `ubuntu`
y `suse`. Con `--dry-run` permite revisar otra familia; una instalación real
comprueba que corresponda al host. La simulación no verifica la disponibilidad
de paquetes en los repositorios del equipo destino. Una distribución desconocida
se rechaza antes de instalar paquetes.

La instalación de todas las familias se realiza mediante `install.sh`.

Después de instalar, abre **RYUO Control** desde el menú de aplicaciones o ejecuta:

```bash
/usr/local/bin/ryuo-control
```

Abre la interfaz con tu usuario habitual. Las acciones administrativas solicitan
autenticación mediante polkit. La sesión de escritorio necesita un agente polkit
activo (habitual en KDE/GNOME). En un gestor de ventanas mínimo, instala y activa
el agente correspondiente a tu escritorio.

El instalador utiliza `scripts/install-common.sh` para copiar archivos,
conservar `/etc/ryuo-lcd.json` y los overrides del servicio, restaurar etiquetas
SELinux cuando la herramienta esté disponible, y activar/reiniciar el servicio.
El instalador espera la comprobación inicial del servicio y muestra sus registros.
No se reinicia el equipo automáticamente ni se borran videos o ajustes existentes.

### Dependencias de la primera instalación

ADB se instala automáticamente en todas las familias; no hace falta instalarlo
manualmente antes de ejecutar el script:

| Familia | Paquete que proporciona ADB |
| --- | --- |
| Fedora / Nobara / Bazzite | `android-tools` |
| Arch y derivados | `android-tools` |
| Ubuntu / Debian y derivados | `adb` |
| openSUSE / SUSE | `android-tools` |

También se incluyen Python, HID, polkit/pkexec y las dependencias de la interfaz
elegida. Qt6 usa PySide6, Qt Multimedia, traducciones y bibliotecas Wayland/X11;
Fedora/Arch usan paquetes nativos y Ubuntu/SUSE wheels de Qt. GTK4 usa paquetes
nativos de PyGObject, Cairo, GTK4 y GStreamer; no instala PySide6. Las reglas
USB del RYUO se instalan y recargan en el mismo proceso.

Antes de copiar la app y activar el servicio, se comprueban los ejecutables de
ADB, pkexec, systemd y udev, se ejecuta `adb version` (sin iniciar su servidor),
se comprueban HID y los imports de la interfaz elegida (incluidas las traducciones
españolas en Qt6),
y se leen los sensores. Si falta una dependencia o un ejecutable no funciona,
el instalador se detiene con un error; no declara completada la instalación.
Estas comprobaciones no requieren que el AIO esté autorizado por ADB. La
autorización del dispositivo, si el firmware la solicita, se resuelve aparte.

### Fedora y Nobara

Con `--qt6`:

Usan `dnf` con `python3-hidapi`, `python3-pyside6`, `qt6-qtmultimedia`,
`qt6-qtwayland`, `qt6-qttranslations`,
`android-tools` y `polkit`. Fedora/Nobara de escritorio mantienen el Python del
sistema para la interfaz y el servicio. La selección del idioma sigue siendo una
preferencia del usuario.

### Bazzite y Fedora Atomic

`install.sh` detecta Bazzite y `/run/ostree-booted` en la familia Fedora.
Si faltan dependencias en el despliegue arrancado,
se preparan con `rpm-ostree install --idempotent` y el instalador termina sin
copiar la aplicación ni cambiar sus servicios. Reinicia manualmente y repite el
comando con la misma opción de interfaz, por ejemplo `sudo bash ./install.sh --gtk4`. Si todas las dependencias ya están presentes,
pasa a instalar la app.

La aplicación utiliza `/usr/local` y `/etc`; `/usr/local` debe ser escribible
(como en las imágenes que lo enlazan a `/var/usrlocal`). No se desbloquea `/usr`,
no se usa `apply-live`, no se añaden repositorios y no se instala en Distrobox.
Las dependencias del servicio USB deben estar disponibles en el host. Las capas
RPM pueden afectar futuras actualizaciones de una imagen; consulta la
[documentación de Bazzite sobre package layering](https://docs.bazzite.gg/Installing_and_Managing_Software/rpm-ostree/).

### Arch y derivados

Con `--qt6`:

Se instalan `python-hidapi`, `pyside6`, `qt6-multimedia`, `qt6-translations`,
`android-tools` y `polkit`. Qt Multimedia se incluye explícitamente porque es una
[dependencia opcional de PySide6 en Arch](https://archlinux.org/packages/extra/x86_64/pyside6/).
El instalador utiliza la variante Cython `python-hidapi` que expone `hid.device`.
El driver también adapta otras APIs HID si ya las usas en una instalación manual.

Mantén el sistema actualizado antes de instalar. El script usa `pacman -S
--needed` sin refrescar las bases por separado, y no fuerza una actualización
completa ni usa AUR. Si las bases están desactualizadas, actualiza normalmente
con `sudo pacman -Syu` antes de ejecutar el instalador.

### Ubuntu y SUSE

Con `--qt6`:

Se instalan mediante APT o Zypper Python, ADB, polkit y las bibliotecas de HID y
del escritorio necesarias. PySide6 e hidapi se instalan desde PyPI con wheels
binarios en `/usr/local/share/ryuo-control/venv`, propiedad de root. No se usa
`sudo pip` sobre el Python del sistema ni `--break-system-packages`. Ubuntu usa
`venv`; SUSE instala pip/virtualenv para la versión real de Python del sistema.

El servicio y los lanzadores usan el mismo Python privado. Los wheels de PySide6
aportan sus bibliotecas Qt y traducciones. Se necesita acceso a PyPI además de
los repositorios de la distribución. El instalador no añade PPAs, repositorios
OBS externos ni paquetes de otra versión de la distribución.

### Interfaz GTK4

La versión GTK4 conserva la barra lateral, el tema oscuro con acentos turquesa,
las tarjetas e historial de sensores y las páginas de pantalla y servicio de Qt6.
Incluye inglés/español, seis posiciones arrastrables, colores, brillo, gestión
de videos, vista previa sobre la imagen curva y acciones del servicio por polkit.
Comparte `/etc/ryuo-lcd.json`, el driver, los nombres del protocolo, la preferencia
de idioma y la caché de videos con Qt6.

La apariencia es similar, aunque los controles, diálogos y fuentes pueden variar
según el toolkit y el escritorio. La aplicación cambia su texto al seleccionar
idioma; los botones estándar de los diálogos nativos de GTK pueden seguir el
idioma del escritorio. Qt6 reproduce mediante Qt Multimedia; GTK4 usa
`Gtk.MediaFile` y GStreamer. Los códecs disponibles dependen de la distribución;
si falta el decodificador de un MP4, la vista previa informa el error. No se
añaden repositorios de códecs externos ni se convierte el archivo. El instalador
incluye los complementos base/good y libav donde está disponible en el plan de
paquetes (Fedora/Arch/Ubuntu). En SUSE puede ser necesario habilitar un códec
compatible siguiendo las indicaciones de la distribución.

GTK4 requiere la versión 4.6 o posterior y utiliza los bindings del Python del sistema. En Ubuntu/SUSE, el entorno
privado habilita `--system-site-packages` para acceder a PyGObject/Cairo nativos
y solo instala hidapi mediante pip. Las instrucciones específicas de Qt6 en
las secciones anteriores se aplican a `--qt6`.

Referencias: [instalación de PyGObject](https://pygobject.gnome.org/getting_started.html)
y [video en GTK4](https://docs.gtk.org/gtk4/class.Video.html).

### Ejecución desde el workspace

En Fedora/Arch con dependencias nativas:

```bash
python3 ryuo-gui.py  # Qt6
python3 ryuo-gtk.py  # GTK4, con sus dependencias instaladas
```

En Ubuntu/SUSE después de instalar:

```bash
/usr/local/share/ryuo-control/venv/bin/python ryuo-gui.py  # Instalación --qt6
/usr/local/share/ryuo-control/venv/bin/python ryuo-gtk.py  # Instalación --gtk4
```

Para aplicar cambios desde la GUI debe existir el ayudante instalado en
`/usr/local/bin/ryuo-control.py`. La interfaz y el ayudante no requieren el mismo
entorno Python: el ayudante solo utiliza la biblioteca estándar y los módulos
compartidos de configuración/traducción.

## Desinstalar

Cierra la ventana de RYUO Control y ejecuta desde el workspace:

```bash
sudo bash ./uninstall.sh
```

Funciona en todas las familias cubiertas por los instaladores. Detiene y
desactiva el servicio antes de eliminar archivos, quita la app, lanzadores,
icono, regla udev y el entorno Python privado si existe. Recarga systemd y udev.
Si no logra detener el servicio, se detiene antes de quitar los archivos.

Para revisar el plan sin modificar nada:

```bash
bash ./uninstall.sh --dry-run
```

Por defecto conserva `/etc/ryuo-lcd.json` y los overrides del servicio. Para
eliminar también esos ajustes del sistema:

```bash
sudo bash ./uninstall.sh --purge
```

Los instaladores actualizados también dejan un desinstalador independiente:

```bash
sudo /usr/local/bin/ryuo-uninstall
```

Admite los mismos argumentos y no necesita conservar el workspace. No elimina
paquetes compartidos como ADB, Qt, Python o polkit, ni capas RPM de Bazzite.
Tampoco borra preferencias/caché de los usuarios, videos descargados o archivos
del AIO, incluso con `--purge`. Las preferencias personales y la caché se
encuentran en las rutas indicadas en las secciones de idioma y vista previa.
El propio repositorio del proyecto se conserva.

## Uso

La interfaz está disponible en **English** y **Español**. El primer inicio utiliza
inglés, independientemente del idioma del escritorio. Cambia el selector
**Language / Idioma** de la barra lateral para traducir la ventana al instante.
Se conservan los cambios sin aplicar y la reproducción local del video.

La preferencia se guarda por usuario en
`~/.config/RYUOControl/RYUOControl.conf` (o bajo `XDG_CONFIG_HOME`), sin sudo;
se recupera en el siguiente inicio. No cambia `/etc/ryuo-lcd.json` ni los nombres
del protocolo del dispositivo. Durante una operación de archivos o del servicio,
el selector se desactiva hasta terminar. Los registros de ADB, systemd y otros
programas se conservan en su idioma original. En la interfaz Qt6, los diálogos usan las
traducciones nativas o las incluidas por los wheels de PySide6, según el instalador.

1. En **Resumen**, comprueba la conexión USB y el estado del servicio.
2. En **Pantalla**, selecciona al menos un video y asigna los sensores a las seis posiciones.
3. Selecciona el modo de reproducción, los colores y la lectura CPU.
4. Para brillo, pulsa **Detectar serie USB** y verifica previamente la conexión
   con `sudo adb devices`. Detectar la serie USB no garantiza autorización ADB.
5. Pulsa **Aplicar al LCD**. Se guardan los ajustes y se reinicia el servicio;
   el reinicio incluye una espera inicial de diez segundos.
6. En **Servicio → Leer registro**, verifica el resultado del envío y del brillo.

Los cambios en los controles no se aplican automáticamente. Al cerrar la
ventana, el servicio sigue enviando datos al LCD. **Activar al arrancar** cambia
el arranque automático; **Iniciar** cambia el estado actual del servicio.

## Configuración

El servicio lee `/etc/ryuo-lcd.json` al iniciar. Si el archivo no existe, utiliza
los valores iniciales de `ryuo_config.py`. Si es inválido, informa el error;
no reemplaza silenciosamente tus ajustes.

Ejemplo completo (reemplaza el número de serie por el de tu LCD):

```json
{
  "media": ["RYUO_IV_HW_Info_04.mp4"],
  "play_mode": "Cycle",
  "brightness": 100,
  "adb_serial": "YOUR_LCD_SERIAL",
  "cpu_temp_mode": "ccd_average",
  "sensors": ["CPU Temperature", "GPU Temperature", "CPU Load", "GPU Load"],
  "title_color": "#E5252B",
  "content_color": "#FFFFFF"
}
```

Los presets son `RYUO_IV_HW_Info_01.mp4` hasta `RYUO_IV_HW_Info_05.mp4`.
Los modos son `Cycle`, `Random` y `Single`; para reproducir solo un video,
selecciona únicamente ese preset.

### Subir un video propio

En **Pantalla**, pulsa **Subir y mostrar video MP4…** y elige un archivo. Se copia mediante
ADB a `/sdcard/pcMedia/` con un nombre único. La serie se toma del campo ADB o
se detecta por USB. Requiere conexión ADB autorizada y accesible desde tu sesión.
Si hay errores, consulta **Servicio** y comprueba `adb devices` en tu terminal.

El video queda marcado inmediatamente y reemplaza la selección anterior. La
aplicación copia el archivo y, al terminar, guarda y aplica los ajustes al LCD
automáticamente, con la autenticación administrativa habitual. No necesitas
marcarlo ni pulsar **Aplicar al LCD** otra vez. Los demás videos permanecen en el
dispositivo. Si falla la copia, se restaura la selección anterior. Si el archivo
se copia pero se cancela o falla la aplicación de ajustes, queda seleccionado
para reintentar **Aplicar al LCD** sin subirlo de nuevo.

Puedes elegir varios videos de la lista después. Hay un máximo de cinco videos
seleccionados y un límite adicional del tamaño del paquete de configuración.
**Leer videos del LCD** recupera los MP4 del directorio de videos propios.
Los videos seleccionados se conservan en la configuración; para recuperar
otros videos que subiste pero no seleccionaste, vuelve a leer la lista del LCD.

La aplicación copia sin convertir ni verificar el códec. Como referencia de
compatibilidad: H.264 hasta 1920×1080, o HEVC/H.265 con etiqueta `hvc1` para
2240×1080. Un MP4 incompatible puede copiarse correctamente y no reproducirse.
Consulta la [documentación de formatos](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md#encode-targets).
La reproducción de archivos propios debe comprobarse en tu unidad.

### Descargar los videos del AIO

En **Pantalla → Videos del dispositivo**, pulsa **Descargar presets…** para copiar
`/sdcard/pcMediaPreset`, o **Descargar videos propios…** para copiar
`/sdcard/pcMedia`. Elige la carpeta de destino. Cada descarga crea una subcarpeta
exclusiva (`ryuo-presets-…` o `ryuo-custom-…`) para evitar sobrescribir archivos.
Se copia el directorio completo, incluidos otros tipos de archivo si los hubiera;
no depende de los videos marcados para reproducir.

La transferencia usa ADB con tu usuario, sin sudo ni cambios de propietario.
Requiere que tu sesión pueda acceder al AIO mediante `adb devices`. No detiene
el servicio, no borra archivos del AIO y no cambia la configuración del LCD.
La interfaz muestra el destino al terminar y la salida de ADB en **Servicio**.
Si falla la copia, conserva la carpeta y los archivos parciales para revisión;
una nueva descarga utiliza otra carpeta.

### Borrar videos propios del AIO

Pulsa **Leer videos del LCD** para recuperar los videos propios que no aparezcan
en la lista. Junto a cada video propio hay un botón **Borrar…** que solicita
confirmación antes de eliminarlo del dispositivo. Los presets no tienen ese botón
y la validación también rechaza sus nombres, rutas y comodines.

Si el video está en la lista de reproducción guardada, primero desmárcalo,
selecciona otro video y pulsa **Aplicar al LCD**. Luego podrás borrarlo. Así el
servicio no queda apuntando al archivo eliminado. Una vez que ADB confirme el
borrado, se retira de la interfaz y se vuelve a consultar automáticamente la
lista del mismo AIO. La recarga quita entradas que ya no existen, añade las nuevas
y conserva la selección de los videos que siguen disponibles. Si falla solo la
recarga, se informa que el borrado terminó y se puede reintentar con **Leer videos
del LCD**. Si falla el borrado, la entrada permanece y puedes revisar el error
en **Servicio**.

El borrado solo apunta a un archivo MP4 concreto dentro de `/sdcard/pcMedia/`;
nunca usa borrado recursivo ni apunta a `/sdcard/pcMediaPreset/`. Es definitivo:
descarga primero una copia si necesitas conservar el video.

`adb_serial` vacío usa `RYUO_ADB_SERIAL` del servicio como respaldo, conservando
la configuración anterior. Sin ninguno de los dos, no se modifica el brillo.
El brillo se aplica al iniciar el servicio, no en cada lectura de sensores.
El comando `--stop` apaga el brillo únicamente si hay una conexión ADB funcional.

### Disposición de sensores

En **Pantalla**, el editor muestra una imagen oficial del RYUO IV con los sensores
superpuestos y sus lecturas locales en vivo. Arrastra un sensor sobre otro para
intercambiarlos. Si el destino está vacío, el sensor se mueve y deja vacía su
posición anterior. Haz clic en un sensor (o enfócalo con Tab y pulsa Enter)
para editarlo en la barra integrada bajo la imagen. Allí puedes seleccionar
otro dato o **Vacía · ocultar**. **Ocultar todos** deja solo el video.

Se eliminó el panel separado de seis filas. La configuración mantiene las seis
posiciones de `sysinfoDisplay`; únicamente cambió el editor visual. Los cambios
se guardan y envían al pulsar **Aplicar al LCD**.

La distribución se basa en los límites `tv_title1..6` y `tv_value1..6` leídos por
UIAutomator del AIO conectado: 01/02 a la izquierda, 03/04/05 a la derecha y 06
cerca de la parte inferior central. La proyección sobre la foto es aproximada;
el firmware conserva la ubicación y el tamaño físicos de sus posiciones.
La foto es una referencia del producto, no una captura del video en reproducción.
Su fuente y atribución están en [assets/README.md](assets/README.md).

### Vista previa del video y los sensores

Marca los videos que quieras usar y elige uno en el selector situado encima del
dispositivo. Pulsa **Reproducir vista previa**. El video se reproduce en bucle,
sin audio, con los sensores en vivo y las posiciones que estás editando. Puedes
pausarlo y seguir reorganizando los sensores antes de aplicar los cambios.
Si hay varios videos marcados, el selector permite previsualizar cada uno;
esta vista no reproduce automáticamente la lista completa ni sigue el instante
de reproducción del AIO real.

Para una subida reciente se usa el archivo local original mientras siga
disponible. En los demás casos se descarga una copia del video desde el AIO
mediante ADB. Se almacena en la caché de usuario:

```text
~/.cache/ryuo-control/videos/<identificador-del-AIO>/<archivo.mp4>
```

Se respeta `XDG_CACHE_HOME` si está configurado. Cada AIO tiene una carpeta
distinta. La aplicación no escribe videos en `/usr/local` ni requiere sudo
para la caché. Las descargas incompletas no reemplazan una copia previa válida.
**Actualizar copia** vuelve a descargar el archivo, útil si lo cambiaste en el
AIO. La caché es una copia temporal, no un respaldo; usa los botones de descarga
si quieres guardar tus videos permanentemente. No hay limpieza automática por
tamaño; puedes borrar esta caché con la aplicación cerrada y se descargará de
nuevo cuando la necesites.

La perspectiva se dibuja proyectando el video en dos superficies sobre la foto:
cara principal y lateral curvo. Es una aproximación visual de la curvatura; no
modela reflejos, ángulo de visión ni distorsión óptica exacta. El video conserva
su contenido y queda recortado a esas superficies, con los sensores por encima.
Reproducir, pausar o cambiar esta vista no modifica el AIO. Qt Multimedia debe
disponer del decodificador del video en tu sistema; los errores se muestran en
la vista previa. La configuración real solo cambia al aplicar ajustes.

El mecanismo permite intercambiar seis posiciones predefinidas, sin coordenadas
libres. Referencia:
[documentación del protocolo, Widget slots](https://github.com/FoxPodZ/ryuo-iv-screen/blob/main/PROTOCOL.md#widget-slots).

El JSON conserva seis entradas, incluidas las posiciones vacías, por ejemplo:

```json
"sensors": ["CPU Temperature", "", "GPU Temperature", "", "CPU Load", ""]
```

Las configuraciones anteriores con menos entradas se completan con posiciones
vacías al final, conservando el orden existente. Otros firmwares pueden necesitar
ajustes en las anclas visuales de `DevicePreview`.

No edites ya el campo `media` en Python: usa la interfaz o el JSON. Tras editar
manualmente el JSON, ejecuta `sudo systemctl restart ryuo-lcd.service`.

## Hardware y sensores

- USB soportado: `0b05:1c76` y `1c75:1c76` (ROG RYUO IV; el fabricante USB puede
  variar con el firmware). La detección exige tanto fabricante como producto;
  no abre otros dispositivos ASUS por compartir fabricante.
- Ryzen: promedio de sensores `k10temp` etiquetados `Tccd*`, con un decimal;
  usa Tctl como respaldo si no hay CCD legibles. También se puede seleccionar
  Tctl directamente.
- GPU AMD: temperatura, uso, ventilador, potencia y frecuencia desde `amdgpu` en sysfs.
- GPU NVIDIA: temperatura, uso, frecuencia y potencia mediante `nvidia-smi`.
- Memoria: uso desde `/proc/meminfo`. Zona horaria desde `TZ` o `/etc/localtime`.
- Las medidas no implementadas se envían como cero, incluida la
  velocidad RAM. Los campos no disponibles se muestran como «—» en la interfaz
  cuando el backend informa esa ausencia; la telemetría numérica del LCD conserva
  cero para esos campos por compatibilidad con su protocolo.

### Intel y NVIDIA

Intel se detecta por el hwmon `coretemp`. Se prefiere la lectura **Package id**
(o **Physical id** en kernels antiguos). Si no existe una lectura de paquete,
se usa el núcleo más caliente. Con varios paquetes se muestra el más caliente.
Uso y frecuencia CPU siguen procediendo de procfs/cpufreq, igual que en AMD.
Los modos CCD/Tctl solo afectan a AMD: Intel utiliza siempre la lectura de
paquete/núcleos. Véase la [documentación de coretemp](https://docs.kernel.org/hwmon/coretemp.html).
Si el kernel no lo cargó automáticamente, en un equipo Intel compatible puedes
cargarlo con `sudo modprobe coretemp` y volver a comprobar `--diagnose`.

NVIDIA requiere el módulo del controlador NVIDIA cargado y `nvidia-smi`
disponible. La app consulta temperatura, utilización, potencia y frecuencia con
CSV sin unidades y un tiempo límite de un segundo. No ejecuta órdenes de ajuste
de potencia, reloj o ventilación. Véase [NVIDIA System Management Interface](https://docs.nvidia.com/deploy/nvidia-smi/index.html).
Para verificar las herramientas del controlador:

```bash
nvidia-smi --query-gpu=index,temperature.gpu,utilization.gpu,power.draw,clocks.gr,memory.total --format=csv,noheader,nounits
```

La aplicación utiliza el controlador de vídeo que ya proporciona el sistema.
Sus instaladores no reemplazan el controlador gráfico ni gestionan Secure Boot.
Si falta `nvidia-smi`, instala las herramientas de la misma versión del
controlador NVIDIA siguiendo el método de tu distribución. Nouveau y GPU Intel
no tienen aún un lector de métricas GPU implementado.

En equipos híbridos, una lectura válida de NVIDIA tiene prioridad sobre AMD.
Dentro de cada proveedor se prefiere la GPU de mayor VRAM; NVIDIA desempata por
índice menor. Se muestran las métricas de una única GPU, sin mezclar valores de
dos tarjetas. Si NVIDIA falla, se conserva AMD si estaba disponible.

`N/A`, errores o campos no soportados no bloquean las actualizaciones del LCD.
El diagnóstico enumera los campos ausentes y los problemas de `nvidia-smi`;
la GUI los muestra como «—». El ventilador NVIDIA queda como no implementado:
`fan.speed` es un porcentaje y el campo del LCD que usamos es RPM.
La potencia/voltaje CPU Intel y el voltaje GPU no están implementados.
**GPU frequency** y **GPU power** ya se pueden elegir como sensores del LCD.


### Compatibilidad del driver entre distribuciones

El protocolo USB no depende del gestor de paquetes. El driver lee las interfaces
Linux `sysfs`/`procfs` y usa una capa `ryuo_hid.py` que normaliza estas APIs:

| Binding | API adaptada | Uso previsto |
| --- | --- | --- |
| cython-hidapi | `hid.device()` | Paquetes Fedora/Arch y wheel de los instaladores Ubuntu/SUSE |
| pyhidapi basado en ctypes | `hid.Device(vid=…, pid=…)` | Alternativa en instalaciones manuales |
| pyhidapi clásico | funciones `hidapi.hid_*` | Compatibilidad con paquetes antiguos como los de algunas versiones de Ubuntu |

El backend se carga solo al diagnosticar o abrir USB. `--sensors` funciona aunque
no haya bindings HID o bibliotecas nativas disponibles. Los errores de carga
indican que debes ejecutar el instalador de tu distribución. El handle se cierra
también si falla la configuración inicial o una escritura.

La lectura tolera sensores opcionales ausentes; una GPU sin `fan1_input` sigue
aportando temperatura. Si hay varias GPU AMD, se prefiere la de mayor VRAM y se
usa `boot_vga` para desempatar. No hay rutas fijas `hwmonN`/`cardN`. La frecuencia
CPU promedia lecturas cpufreq y usa `/proc/cpuinfo` como respaldo. La zona horaria
acepta `TZ`, el enlace de `/etc/localtime` o `/etc/timezone` cuando localtime es
una copia. Sin datos suficientes se informa cero/UTC, no una lectura inventada.

El servicio de sistema accede a HID como root. La regla `70-ryuo-lcd.rules`,
instalada por todas las familias, concede acceso USB para ADB a la sesión local
activa mediante `uaccess`, solo para los dos identificadores del RYUO IV. No
concede permisos globales a otros USB ni cambia los permisos de todos los hidraw.
No garantiza acceso desde sesiones SSH sin asiento local. Las distros deben
tener udev/logind; los instaladores recargan las reglas sin reiniciar el equipo.

Validación disponible: pruebas de contratos de las tres APIs HID, fixtures de
sysfs/procfs, casos de dos CCD, coretemp Intel, CSV NVIDIA, varias GPU, zona horaria, sintaxis Python 3.10,
reglas udev y empaquetado en una raíz temporal. En el equipo de desarrollo Fedora
se verificaron detección y sensores reales. La apertura HID sin privilegios fue
rechazada; la transmisión del driver actualizado con el servicio root y las
instalaciones nativas en Arch/Ubuntu/SUSE/Bazzite aún requieren pruebas de campo.
Los nuevos lectores Intel/NVIDIA se validaron con fixtures y respuestas simuladas;
falta comprobarlos en hardware Intel/NVIDIA real.
Esto es preparación de portabilidad, no certificación de todas las versiones.

## Diagnóstico y desarrollo

```bash
/usr/local/bin/ryuo-lcd --sensors
/usr/local/bin/ryuo-lcd --diagnose
sudo systemctl status ryuo-lcd.service --no-pager
sudo journalctl -u ryuo-lcd.service -n 60 --no-pager
```

Desde el workspace también puedes ejecutar `python3 ryuo-lcd.py --diagnose`.
El informe incluye Python, kernel, backend HID, USB detectado, sensores y las
fuentes de lectura (`coretemp`, `k10temp`, `amdgpu` o `nvidia-smi`). No
abre el dispositivo ni envía comandos USB; distingue explícitamente detección
de una prueba de acceso/transmisión. No contiene una comprobación del rendimiento
de la bomba o ventiladores.

Para una única actualización USB, evita que dos procesos escriban simultáneamente:

```bash
sudo systemctl stop ryuo-lcd.service
sudo /usr/local/bin/ryuo-lcd --once
sudo systemctl start ryuo-lcd.service
```

Suite completa (requiere las dependencias de ambas interfaces; no escribe al USB ni cambia servicios):

```bash
python3 -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen python3 ryuo-gui.py --smoke-test
```

El smoke test Qt6 cierra la ventana automáticamente y guarda una captura en
`/tmp/ryuo-control-preview.png`. Para comprobar solo GTK4, desde una sesión gráfica:

```bash
python3 -m unittest discover -s tests -p test_gtk4.py -v
python3 ryuo-gtk.py --smoke-test
```

El smoke test GTK4 no consulta sensores ni servicios y guarda su propia vista en
`/tmp/ryuo-control-gtk-preview.png`. Las pruebas GTK4 se omiten cuando no hay un
display GTK disponible; no equivalen a una comprobación visual en ese caso.
Opcionalmente, `RYUO_TEST_VIDEO=/ruta/prueba.mp4` habilita la prueba de reproducción
local y proyección, sin acceder al AIO. La nueva interfaz se verificó en una
pantalla virtual Broadway, con reproducción de un video generado localmente;
las operaciones USB reales y la instalación nativa en cada distribución siguen
requiriendo comprobaciones de campo.

Archivos principales:

- `ryuo-gui.py`: interfaz Qt6 y monitor de sensores en segundo plano.
- `ryuo-gtk.py`: interfaz GTK4, editor interactivo y vista previa multimedia.
- `ryuo_desktop.py`: utilidades compartidas de detección, sensores y caché sin dependencias gráficas.
- `ryuo-lcd.py`: protocolo HID y envío de sensores.
- `ryuo_hid.py`: detección USB y adaptadores de bindings HID.
- `ryuo_config.py`: validación, valores iniciales y formato de pantalla.
- `ryuo-control.py`: acciones administrativas limitadas al servicio y sus ajustes.
- `install.sh`: instalador único con detección de distribución.
- `scripts/install-platforms.sh`: detección de familias y dependencias por distribución.
- `scripts/install-common.sh`: validación del runtime, archivos, lanzadores y servicio compartidos.

## Licencia

MIT

## Créditos

Este proyecto es un fork de
[asus-ryuo-iv-linux](https://github.com/dragocz95/asus-ryuo-iv-linux),
iniciado por [dragocz95](https://github.com/dragocz95). Su controlador original
para Linux y el trabajo de ingeniería inversa del protocolo USB HID son la base
de RYUO Control. Gracias por compartir el proyecto original.

Ingeniería inversa realizada con ayuda de Claude (Anthropic).

Desarrollo y ampliación de RYUO Control con asistencia de Codex (OpenAI),
utilizando GPT 6.1 Sol.
