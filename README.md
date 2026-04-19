# TeleFS 🚀

**Telegram as a Remote Filesystem**

TeleFS is a high-performance, secure, and reliable virtual filesystem CLI that uses Telegram's "Saved Messages" as a backend. It allows you to store, manage, and retrieve files directly from your Telegram account with advanced features like chunked transfers, AES encryption, and deduplication.

## ✨ Features

- **Large File Support**: Automatically splits files into chunks (up to 100MB each) for reliable transfer of files larger than 2GB.
- **Resume-able I/O**: Interrupted uploads/downloads can be resumed from the last successful chunk.
- **Deduplication**: Content-addressable storage ensures identical files are only stored once, saving bandwidth.
- **AES-GCM Encryption**: Every chunk is encrypted with a unique nonce and a per-file 256-bit key.
- **Beautiful CLI**: Interactive shell and one-shot commands powered by `rich` for stunning tables and tree views.
- **Node.js Wrapper**: Easily installable via NPM, managing a private Python virtual environment internally.

## 📦 Installation

```bash
npm install -g telefs
```

*Note: TeleFS requires Python 3.8+ to be installed on your system.*

## 🚀 Getting Started

### 1. Configure Credentials
On your first run, you will need to provide your Telegram `API_ID` and `API_HASH` (get them from [my.telegram.org](https://my.telegram.org)).

```bash
telefs
```

### 2. Common Commands

**List Files:**
```bash
telefs ls /Documents
```

**Upload a File:**
```bash
telefs upload ~/Movies/vacation.mp4 /Videos
```

**Download a File:**
```bash
telefs download /Videos/vacation.mp4 ./downloads/
```

**Directory Tree:**
```bash
telefs tree
```

**Remove Files:**
```bash
telefs rm -r /OldFolder
```

## 🛠 Advanced Usage

TeleFS includes an interactive shell for a full filesystem experience:

```bash
telefs
> cd /Photos
> ls
> ul my_photo.png
> quit
```

## 🔒 Security
- **Local Metadata**: File metadata and encryption keys are stored in a local SQLite database (`~/.config/telefs/metadata.db`).
- **End-to-End Encryption**: Data is encrypted locally before being transmitted to Telegram.

## 📄 License
MIT
