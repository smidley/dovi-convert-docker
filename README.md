# DoVi Convert Docker

A Docker container with a web interface for converting Dolby Vision Profile 7 MKV files (UHD Blu-ray rips) to Profile 8.1. Based on the excellent [dovi_convert](https://github.com/cryptochrome/dovi_convert) script by cryptochrome.

![Docker Pulls](https://img.shields.io/docker/pulls/smidley/dovi-convert)
![Docker Image Size](https://img.shields.io/docker/image-size/smidley/dovi-convert/latest)

## Why Convert Profile 7 to 8.1?

Dolby Vision Profile 7 files from UHD Blu-ray rips contain an Enhancement Layer (EL) that many media players cannot process, including:
- Apple TV 4K (with Plex or Infuse)
- Nvidia Shield
- Zidoo players
- Many smart TVs

Converting to Profile 8.1 removes the EL while preserving the dynamic RPU metadata, ensuring maximum compatibility without falling back to HDR10.

## Features

- 🌐 **Modern Web Interface** - Clean, responsive UI for easy operation
- 🔍 **Directory Scanning** - Recursively scan for Dolby Vision files
- 🎬 **Batch Conversion** - Convert multiple files automatically
- 📊 **Real-time Output** - Live streaming of conversion progress
- ⚙️ **Configurable Options** - Safe mode, auto-cleanup, scan depth
- 💾 **Non-destructive** - Creates backups of original files
- 🐳 **Unraid Ready** - Easy installation via Community Applications

## Quick Start

### Docker Run

```bash
docker run -d \
  --name dovi-convert \
  -p 8080:8080 \
  -v /path/to/media:/media \
  -v /path/to/config:/config \
  -e TZ=America/New_York \
  smidley/dovi-convert:latest
```

### Docker Compose

```yaml
version: '3.8'

services:
  dovi-convert:
    image: smidley/dovi-convert:latest
    container_name: dovi-convert
    ports:
      - "8080:8080"
    volumes:
      - /path/to/your/media:/media
      - /path/to/config:/config
    environment:
      - TZ=America/New_York
    restart: unless-stopped
```

### Unraid

1. Go to the **Apps** tab in Unraid
2. Search for **dovi-convert**
3. Click **Install**
4. Configure your media path and click **Apply**

## Usage

1. Open the web interface at `http://your-server:8080`
2. Click the folder icon to select your media directory
3. Click **Scan for DV Files** to find Dolby Vision content
4. Review the scan results in the output panel
5. Click **Start Conversion** to begin batch conversion

### Options

| Option | Description |
|--------|-------------|
| **Scan Depth** | How many directory levels deep to scan (1-10) |
| **Safe Mode** | Extract video to disk before converting (slower but more reliable) |
| **Include Simple FEL** | Automatically include Simple FEL files in batch conversion |
| **Auto Cleanup** | Delete backup files after successful conversion |

## Volume Mappings

| Container Path | Description |
|----------------|-------------|
| `/media` | Your media library root |
| `/config` | Persistent configuration storage |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `UTC` | Timezone for logs |
| `MEDIA_PATH` | `/media` | Default media path |
| `CONFIG_PATH` | `/config` | Configuration directory |

## Understanding FEL Types

The script distinguishes between different Enhancement Layer types:

- **MEL (Minimal Enhancement Layer)** - Safe to convert, contains no significant data
- **Simple FEL** - Contains EL data but no brightness expansion, safe to convert
- **Complex FEL** - Contains brightness expansion data, conversion may affect quality

Files detected as Complex FEL will be skipped by default. Use the `-force` flag (not available in web UI) only if you understand the implications.

## Important Notes

### Apple TV + Plex Users

If using Plex on Apple TV 4K, be aware of the "Fake Dolby Vision" issue where Plex may display the DV logo but not actually apply dynamic metadata. Consider using **Infuse** for true Profile 8.1 playback.

### Backup Files

Original files are preserved as `*.bak.dovi_convert`. Enable **Auto Cleanup** only after verifying conversions are successful.

## Building Locally

```bash
git clone https://github.com/smidley/dovi-convert-docker.git
cd dovi-convert-docker
docker build -t dovi-convert .
```

## Architecture Support

- `linux/amd64` (x86_64)
- `linux/arm64` (aarch64)

## Credits

- [dovi_convert](https://github.com/cryptochrome/dovi_convert) by cryptochrome - The original conversion script
- [dovi_tool](https://github.com/quietvoid/dovi_tool) by quietvoid - Dolby Vision metadata manipulation
- [FFmpeg](https://ffmpeg.org/) - Video processing
- [MKVToolNix](https://mkvtoolnix.download/) - Matroska container handling

## License

MIT License - See [LICENSE](LICENSE) for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/smidley/dovi-convert-docker/issues)
- **Discussions**: [GitHub Discussions](https://github.com/smidley/dovi-convert-docker/discussions)
