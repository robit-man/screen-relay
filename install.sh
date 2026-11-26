#!/bin/sh
set -eu

project=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
app_dir="$data_home/screen-relay"
desktop_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/128x128/apps"
bin_dir="$HOME/.local/bin"

case "$(uname -m)" in
    x86_64) asset=doubletake; sha=01fb6c7b9de6f3bd693a629e248fe61ceb7af06353c8f3f21cc29878d1f9f5de ;;
    aarch64) asset=doubletake-arm64; sha=8c2713f122fcee11e3fb358a381d30e661d1e72faf6be919b93ffba7d2a35a85 ;;
    *) printf '%s\n' 'Screen Relay builds target x86-64 and ARM64.' >&2; exit 1 ;;
esac

install -d "$app_dir/bin" "$desktop_dir" "$icon_dir" "$bin_dir"
if [ ! -f "$app_dir/bin/doubletake" ] || ! printf '%s  %s\n' "$sha" "$app_dir/bin/doubletake" | sha256sum --check --status; then
    download=$(mktemp)
    trap 'rm -f "$download"' EXIT HUP INT TERM
    curl --fail --silent --show-error --location --retry 2 --output "$download" \
        "https://github.com/omarroth/doubletake/releases/download/v0.6.0/$asset"
    printf '%s  %s\n' "$sha" "$download" | sha256sum --check --status
    install -m 755 "$download" "$app_dir/bin/doubletake"
fi

/usr/bin/python3 -m venv --system-site-packages "$app_dir/.venv"
"$app_dir/.venv/bin/python" -m pip install --disable-pip-version-check -r "$project/requirements.txt"
install -m 755 "$project/run.py" "$app_dir/run.py"
(
    cd "$project"
    find screenrelay -type f ! -name '*.pyc' -exec install -D -m 644 '{}' "$app_dir/{}" \;
)
install -m 644 "$project/screenrelay/icons/screen-relay.png" \
    "$icon_dir/local.screenrelay.ScreenRelay.png"

cat > "$bin_dir/screen-relay" <<'LAUNCHER'
#!/bin/sh
app_dir=${XDG_DATA_HOME:-"$HOME/.local/share"}/screen-relay
exec "$app_dir/.venv/bin/python" "$app_dir/run.py" "$@"
LAUNCHER
chmod 755 "$bin_dir/screen-relay"

# Encode the launcher path using the desktop entry's two escaping layers.
/usr/bin/python3 - "$bin_dir/screen-relay" "$desktop_dir/local.screenrelay.ScreenRelay.desktop" <<'PY'
import sys
from pathlib import Path

launcher = sys.argv[1]
for reserved in ('\\', '"', '`', '$'):
    launcher = launcher.replace(reserved, '\\' + reserved)
launcher = launcher.replace('\\', '\\\\').replace('%', '%%')
Path(sys.argv[2]).write_text(f"""[Desktop Entry]
Type=Application
Name=Screen Relay
Comment=Cast your screen and desktop audio to a nearby TV
Exec="{launcher}"
Icon=local.screenrelay.ScreenRelay
Terminal=false
Categories=AudioVideo;Video;
StartupNotify=true
""")
PY

printf '%s\n' 'Installed Screen Relay. Open it from the app launcher.'
